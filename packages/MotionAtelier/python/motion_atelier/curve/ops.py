# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Pure curve editing operations. Each returns an :class:`EditResult`.

Every operation returns a new curve plus a coherent selection (indices into the new curve)
and any non-fatal notices. Inputs are never mutated (all model types are immutable).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from motion_atelier.curve import bezier
from motion_atelier.curve import errors as E
from motion_atelier.curve.errors import CurveError, Notice
from motion_atelier.curve.evaluate import segment_index
from motion_atelier.curve.model import (
    MIN_KNOT_GAP,
    WEIGHT_CLAMP_TOLERANCE,
    Curve,
    Knot,
    Tangent,
)

IN = "in"
OUT = "out"

# Flattening a zero-length handle would leave it invisible and without effect; such a
# handle gets the one-third weight that makes the segment's time mapping linear.
FLATTEN_ZERO_WEIGHT_REPLACEMENT = 1.0 / 3.0


@dataclass(frozen=True, slots=True)
class EditResult:
    curve: Curve
    selection: frozenset[int]
    notices: tuple[Notice, ...] = ()
    delta: tuple[float, float] | None = None  # applied (dx, dy) for moves


def _indices(curve: Curve, indices: Iterable[int]) -> frozenset[int]:
    out = frozenset(int(i) for i in indices)
    n = len(curve.knots)
    bad = sorted(i for i in out if i < 0 or i >= n)
    if bad:
        raise CurveError(E.INDEX_OUT_OF_RANGE, f"knot indices {bad} not in 0..{n - 1}")
    return out


def _gap(curve: Curve) -> float:
    return MIN_KNOT_GAP * (curve.x_end - curve.x_start)


def _clamped_tangent(slope: float, weight: float, notices: list[Notice], where: str) -> Tangent:
    if weight > 1.0 + WEIGHT_CLAMP_TOLERANCE:
        notices.append(Notice(E.WEIGHT_CLAMPED, f"{where}: weight {weight:.6g} clamped to 1"))
        weight = 1.0
    return Tangent(slope, weight)


# -- insertion / deletion ------------------------------------------------------------------


def insert_knot(curve: Curve, x: float) -> EditResult:
    """Insert a smooth knot on the curve at ``x`` by de Casteljau subdivision.

    The new knot lies exactly on the curve and the two halves reproduce the original segment.
    The new knot's handle weights are provably within ``[0, 1]``. The outer handles keep their
    slopes and absolute length; for extreme full-weight segments their weight re-expressed
    against the shorter half can exceed 1, in which case it is clamped and a
    ``weight_clamped`` notice reports the (small) shape change.
    """
    if not curve.x_start < x < curve.x_end:
        raise CurveError(E.INSERT_OUTSIDE, f"x={x!r} is not strictly inside the curve")
    i = segment_index(curve, x)
    a, b = curve.knots[i], curve.knots[i + 1]
    gap = _gap(curve)
    if x - a.x < gap or b.x - x < gap:
        raise CurveError(E.INSERT_TOO_CLOSE, f"x={x!r} is within {gap:.3g} of a knot")
    px, py = curve.segment_controls(i)
    u = bezier.solve_u(px, x)
    (lx, ly), (rx, ry) = bezier.split(px, py, u)
    mx, my = lx[3], ly[3]
    slope = bezier.slope_at(px, py, u)
    if slope != slope or abs(slope) == float("inf"):
        raise CurveError(E.INSERT_OUTSIDE, "the curve has a vertical tangent at this point")
    d1 = mx - a.x
    d2 = b.x - mx
    notices: list[Notice] = []
    assert a.tout is not None and b.tin is not None
    a_out = _clamped_tangent(a.tout.slope, (lx[1] - lx[0]) / d1, notices, f"knot {i} out")
    b_in = _clamped_tangent(b.tin.slope, (rx[3] - rx[2]) / d2, notices, f"knot {i + 1} in")
    new = Knot(
        mx,
        my,
        Tangent(slope, (lx[3] - lx[2]) / d1),
        Tangent(slope, (rx[1] - rx[0]) / d2),
        True,
    )
    knots = list(curve.knots)
    knots[i] = a.replace(tout=a_out)
    knots[i + 1] = b.replace(tin=b_in)
    knots.insert(i + 1, new)
    return EditResult(Curve(tuple(knots)), frozenset({i + 1}), tuple(notices))


def delete_knots(curve: Curve, indices: Iterable[int]) -> EditResult:
    """Delete interior knots. Endpoints are never deleted.

    Each tangent that faced a removed knot keeps its absolute handle length, so its weight is
    rescaled to the merged segment (which is longer, so the weight only shrinks).
    """
    chosen = _indices(curve, indices)
    notices: list[Notice] = []
    last = len(curve.knots) - 1
    if 0 in chosen or last in chosen:
        notices.append(Notice(E.ENDPOINT_NOT_DELETED, "endpoints cannot be deleted"))
    remove = sorted(i for i in chosen if 0 < i < last)
    if not remove:
        notices.append(Notice(E.NOTHING_SELECTED, "no interior knot selected"))
        return EditResult(curve, chosen, tuple(notices))
    removed = set(remove)
    keep = [i for i in range(last + 1) if i not in removed]
    old = curve.knots
    new_knots: list[Knot] = []
    for pos, i in enumerate(keep):
        knot = old[i]
        changes = {}
        if pos + 1 < len(keep):
            j = keep[pos + 1]
            if j != i + 1:
                assert knot.tout is not None
                ratio = (old[i + 1].x - knot.x) / (old[j].x - knot.x)
                changes["tout"] = Tangent(knot.tout.slope, knot.tout.weight * ratio)
        if pos > 0:
            h = keep[pos - 1]
            if h != i - 1:
                assert knot.tin is not None
                ratio = (knot.x - old[i - 1].x) / (knot.x - old[h].x)
                changes["tin"] = Tangent(knot.tin.slope, knot.tin.weight * ratio)
        new_knots.append(knot.replace(**changes) if changes else knot)
    return EditResult(Curve(tuple(new_knots)), frozenset(), tuple(notices))


# -- flips ---------------------------------------------------------------------------------


def flip_time(curve: Curve, selection: Iterable[int] = ()) -> EditResult:
    """Reverse the motion in time while keeping the start and end values.

    Point map: ``(x, y) → (x0 + x1 − x, y0 + y1 − y)`` (a half-turn about the centre of the
    endpoint frame). Under the commit mapping, which normalises by the endpoint values, this
    is exactly time reversal; keeping the endpoints in place keeps the curve readable. Slopes
    are preserved exactly (both axes flip) and in/out tangents swap.
    """
    sel = _indices(curve, selection)
    knots = curve.knots
    n = len(knots)
    x0, x1 = curve.x_start, curve.x_end
    y0, y1 = knots[0].y, knots[-1].y
    cx, cy = x0 + x1, y0 + y1
    out: list[Knot] = []
    for j in range(n):
        k = knots[n - 1 - j]
        if j == 0:
            x, y = x0, y0
        elif j == n - 1:
            x, y = x1, y1
        else:
            x, y = cx - k.x, cy - k.y
        tin = Tangent(k.tout.slope, k.tout.weight) if k.tout is not None else None
        tout = Tangent(k.tin.slope, k.tin.weight) if k.tin is not None else None
        out.append(Knot(x, y, tin, tout, k.smooth))
    return EditResult(Curve(tuple(out)), frozenset(n - 1 - i for i in sel))


def flip_value(curve: Curve, selection: Iterable[int] = ()) -> EditResult:
    """Mirror the curve's deviation from its endpoint chord (``y → 2·chord(x) − y``).

    Endpoints stay fixed, an ease-in becomes an ease-out, overshoot becomes undershoot.
    Slopes map to ``2m − s`` with ``m`` the chord slope. A mirror about a horizontal line
    would be invisible after the commit mapping normalises by endpoint values.
    """
    sel = _indices(curve, selection)
    knots = curve.knots
    x0, x1 = curve.x_start, curve.x_end
    y0, y1 = knots[0].y, knots[-1].y
    m = (y1 - y0) / (x1 - x0)
    out: list[Knot] = []
    last = len(knots) - 1
    for i, k in enumerate(knots):
        if i in (0, last):
            y = k.y
        else:
            chord = y0 + m * (k.x - x0)
            y = 2.0 * chord - k.y
        # Equal slopes go through the identical expression, so smooth knots stay smooth.
        tin = Tangent(2.0 * m - k.tin.slope, k.tin.weight) if k.tin is not None else None
        tout = Tangent(2.0 * m - k.tout.slope, k.tout.weight) if k.tout is not None else None
        out.append(Knot(k.x, y, tin, tout, k.smooth))
    return EditResult(Curve(tuple(out)), sel)


# -- tangent continuity ----------------------------------------------------------------------


def flatten_tangents(curve: Curve, indices: Iterable[int]) -> EditResult:
    """Zero slopes on the selected knots, making them smooth. Non-zero weights are kept."""
    sel = _indices(curve, indices)
    if not sel:
        return EditResult(curve, sel, (Notice(E.NOTHING_SELECTED),))
    knots = list(curve.knots)
    for i in sorted(sel):
        k = knots[i]

        def flat(t: Tangent | None) -> Tangent | None:
            if t is None:
                return None
            weight = t.weight if t.weight > 0.0 else FLATTEN_ZERO_WEIGHT_REPLACEMENT
            return Tangent(0.0, weight)

        knots[i] = Knot(k.x, k.y, flat(k.tin), flat(k.tout), True)
    return EditResult(Curve(tuple(knots)), sel)


def break_tangents(curve: Curve, indices: Iterable[int]) -> EditResult:
    """Mark interior knots broken. The shape is unchanged."""
    sel = _indices(curve, indices)
    knots = list(curve.knots)
    for i in sorted(sel):
        if 0 < i < len(knots) - 1:
            knots[i] = knots[i].replace(smooth=False)
    return EditResult(Curve(tuple(knots)), sel)


def smooth_slope(curve: Curve, index: int) -> float:
    """Shared slope for smoothing knot ``index``: the slope of the line through both handle
    tips, i.e. the handle-length-weighted mean ``(s_in·h_in + s_out·h_out)/(h_in + h_out)``
    with ``h`` the horizontal handle lengths (plain mean if both handles are zero-length)."""
    k = curve.knots[index]
    assert k.tin is not None and k.tout is not None
    h_in = k.tin.weight * (k.x - curve.knots[index - 1].x)
    h_out = k.tout.weight * (curve.knots[index + 1].x - k.x)
    total = h_in + h_out
    if total <= 0.0:
        return 0.5 * (k.tin.slope + k.tout.slope)
    return (k.tin.slope * h_in + k.tout.slope * h_out) / total


def smooth_tangents(curve: Curve, indices: Iterable[int]) -> EditResult:
    sel = _indices(curve, indices)
    knots = list(curve.knots)
    for i in sorted(sel):
        if 0 < i < len(knots) - 1:
            k = knots[i]
            s = smooth_slope(curve, i)
            assert k.tin is not None and k.tout is not None
            knots[i] = Knot(k.x, k.y, k.tin.with_slope(s), k.tout.with_slope(s), True)
    return EditResult(Curve(tuple(knots)), sel)


def set_continuity(curve: Curve, indices: Iterable[int], smooth: bool) -> EditResult:
    return smooth_tangents(curve, indices) if smooth else break_tangents(curve, indices)


# -- moves and numeric edits ---------------------------------------------------------------


def move_knots(curve: Curve, indices: Iterable[int], dx: float, dy: float) -> EditResult:
    """Translate the selected knots by ``(dx, dy)`` as a group.

    Endpoints keep their ``x``. Interior knots never cross (or come within the minimum gap
    of) an unselected neighbour: ``dx`` is clamped once for the whole group, so relative
    spacing inside the selection is preserved. Weights are relative, so handles stretch with
    their segments.
    """
    sel = _indices(curve, indices)
    if not sel:
        return EditResult(curve, sel, (Notice(E.NOTHING_SELECTED),), (0.0, 0.0))
    knots = curve.knots
    last = len(knots) - 1
    movable = {i for i in sel if 0 < i < last}
    gap = _gap(curve)
    lo, hi = -float("inf"), float("inf")
    for i in movable:
        if i - 1 not in movable:
            lo = max(lo, knots[i - 1].x + gap - knots[i].x)
        if i + 1 not in movable:
            hi = min(hi, knots[i + 1].x - gap - knots[i].x)
    applied_dx = 0.0 if not movable else min(max(dx, lo), hi)
    if lo > hi:  # neighbours already closer than the gap: allow no horizontal motion
        applied_dx = 0.0
    notices: tuple[Notice, ...] = ()
    if movable and applied_dx != dx:
        notices = (Notice(E.MOVE_CLAMPED, "knots stopped at their neighbours"),)
    out = list(knots)
    for i in sel:
        k = knots[i]
        nx = k.x + applied_dx if i in movable else k.x
        out[i] = k.replace(x=nx, y=k.y + dy)
    return EditResult(Curve(tuple(out)), sel, notices, (applied_dx, dy))


def set_knot(curve: Curve, index: int, x: float | None = None,
             y: float | None = None) -> EditResult:
    """Set a knot's position exactly (numeric editing). Invalid positions raise."""
    _indices(curve, [index])
    knots = list(curve.knots)
    k = knots[index]
    last = len(knots) - 1
    nx = k.x if x is None else float(x)
    if x is not None and index in (0, last) and nx != k.x:
        raise CurveError(E.ENDPOINT_FIXED, "endpoint time is fixed")
    if x is not None and 0 < index < last:
        gap = _gap(curve)
        if not (knots[index - 1].x + gap <= nx <= knots[index + 1].x - gap):
            raise CurveError(
                E.KNOT_OUT_OF_ORDER,
                f"x={nx!r} must stay between {knots[index - 1].x!r} and {knots[index + 1].x!r}",
            )
    knots[index] = k.replace(x=nx, y=k.y if y is None else y)
    return EditResult(Curve(tuple(knots)), frozenset({index}))


def set_tangent(curve: Curve, index: int, side: str, slope: float | None = None,
                weight: float | None = None) -> EditResult:
    """Set one side's slope and/or weight. On a smooth knot the other side follows the slope."""
    _indices(curve, [index])
    if side not in (IN, OUT):
        raise CurveError(E.NO_SUCH_TANGENT, f"side must be 'in' or 'out', got {side!r}")
    knots = list(curve.knots)
    k = knots[index]
    current = k.tin if side == IN else k.tout
    if current is None:
        raise CurveError(E.NO_SUCH_TANGENT, f"knot {index} has no {side} tangent")
    new_slope = current.slope if slope is None else slope
    new_weight = current.weight if weight is None else weight
    if not 0.0 <= new_weight <= 1.0:
        raise CurveError(E.WEIGHT_OUT_OF_RANGE, f"weight {new_weight!r} not in [0, 1]")
    changed = Tangent(new_slope, new_weight)
    tin, tout = (changed, k.tout) if side == IN else (k.tin, changed)
    if k.smooth and tin is not None and tout is not None:
        if side == IN:
            tout = tout.with_slope(changed.slope)
        else:
            tin = tin.with_slope(changed.slope)
    knots[index] = Knot(k.x, k.y, tin, tout, k.smooth)
    return EditResult(Curve(tuple(knots)), frozenset({index}))


def normalize_domain(curve: Curve) -> Curve:
    """Affinely map the curve's domain onto ``[0, 1]`` (slopes scale by the domain length)."""
    x0, x1 = curve.x_start, curve.x_end
    if x0 == 0.0 and x1 == 1.0:
        return curve
    span = x1 - x0
    out = []
    last = len(curve.knots) - 1
    for i, k in enumerate(curve.knots):
        x = 0.0 if i == 0 else 1.0 if i == last else (k.x - x0) / span
        tin = Tangent(k.tin.slope * span, k.tin.weight) if k.tin is not None else None
        tout = Tangent(k.tout.slope * span, k.tout.weight) if k.tout is not None else None
        out.append(Knot(x, k.y, tin, tout, k.smooth))
    return Curve(tuple(out))

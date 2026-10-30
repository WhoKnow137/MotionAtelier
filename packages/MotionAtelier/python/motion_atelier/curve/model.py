# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Immutable, validated curve model: tangents, knots, curves.

A curve is an ordered tuple of at least two knots with strictly increasing ``x``. Each knot
has an incoming and an outgoing tangent (slope ``dy/dx`` plus a handle weight in ``[0, 1]``:
the fraction of the adjacent segment's duration the handle spans horizontally). The first
knot has no incoming tangent and the last no outgoing one.

Segment ``a → b`` with duration ``d = b.x − a.x`` is the cubic Bezier with control points::

    P0 = (a.x, a.y)
    P1 = (a.x + wa·d, a.y + sa·wa·d)       (wa, sa) = a.tout
    P2 = (b.x − wb·d, b.y − sb·wb·d)       (wb, sb) = b.tin
    P3 = (b.x, b.y)

Monotone time (the curve never folds back): ``Bx'(u)/(3d) = wa(1−u)² + 2u(1−u)(1−wa−wb)
+ wb·u²`` is bilinear in ``(wa, wb)``, so over the box ``[0,1]²`` its minimum is attained at a
corner, where it equals ``2u(1−u)``, ``(1−u)²``, ``u²`` or ``(1−2u)²`` — all non-negative.
Hence ``Bx`` is non-decreasing whenever both weights lie in ``[0, 1]``
(tested in ``tests/unit/curve/test_model.py``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from motion_atelier.curve import errors as E
from motion_atelier.curve.errors import CurveError

# Handle weights within this distance outside [0, 1] are rounding noise from arithmetic on
# valid weights (e.g. rescaling during knot deletion) and are clamped silently; anything
# further out is a caller error. 1e-9 is ~10⁷ ulps at 1.0: far above rounding noise, far
# below any visible handle change.
WEIGHT_CLAMP_TOLERANCE = 1e-9

# Minimum normalised separation between knots created or moved by editing operations. At a
# 1000-frame range this is 0.001 frame — invisible, yet it keeps every segment long enough
# for the solver's relative tolerances to stay meaningful.
MIN_KNOT_GAP = 1e-6


def _require_finite(value: float, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise CurveError(E.BAD_TYPE, f"{what} must be a number, got {type(value).__name__}")
    value = float(value)
    if not math.isfinite(value):
        raise CurveError(E.NON_FINITE, f"{what} is {value!r}")
    return value


@dataclass(frozen=True, slots=True)
class Tangent:
    """One side of a knot: slope ``dy/dx`` and handle weight in ``[0, 1]``."""

    slope: float
    weight: float

    def __post_init__(self) -> None:
        slope = _require_finite(self.slope, "slope")
        weight = _require_finite(self.weight, "weight")
        if weight < 0.0:
            if weight < -WEIGHT_CLAMP_TOLERANCE:
                raise CurveError(E.WEIGHT_OUT_OF_RANGE, f"weight {weight!r} < 0")
            weight = 0.0
        elif weight > 1.0:
            if weight > 1.0 + WEIGHT_CLAMP_TOLERANCE:
                raise CurveError(E.WEIGHT_OUT_OF_RANGE, f"weight {weight!r} > 1")
            weight = 1.0
        object.__setattr__(self, "slope", slope + 0.0)  # + 0.0 canonicalises -0.0
        object.__setattr__(self, "weight", weight + 0.0)

    def with_slope(self, slope: float) -> Tangent:
        return Tangent(slope, self.weight)

    def with_weight(self, weight: float) -> Tangent:
        return Tangent(self.slope, weight)


@dataclass(frozen=True, slots=True)
class Knot:
    """A curve knot. ``smooth`` knots share one slope on both sides (exact equality)."""

    x: float
    y: float
    tin: Tangent | None = None
    tout: Tangent | None = None
    smooth: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "x", _require_finite(self.x, "x") + 0.0)
        object.__setattr__(self, "y", _require_finite(self.y, "y") + 0.0)
        for name in ("tin", "tout"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, Tangent):
                raise CurveError(E.BAD_TYPE, f"{name} must be a Tangent or None")
        if not isinstance(self.smooth, bool):
            raise CurveError(E.BAD_TYPE, "smooth must be a bool")
        if (
            self.smooth
            and self.tin is not None
            and self.tout is not None
            and self.tin.slope != self.tout.slope
        ):
            raise CurveError(
                E.SMOOTH_SLOPE_MISMATCH,
                f"smooth knot at x={self.x!r} has slopes {self.tin.slope!r} != "
                f"{self.tout.slope!r}",
            )

    def replace(self, **changes) -> Knot:
        values = {
            "x": self.x, "y": self.y, "tin": self.tin, "tout": self.tout, "smooth": self.smooth,
        }
        values.update(changes)
        return Knot(**values)


@dataclass(frozen=True, slots=True)
class Curve:
    """An immutable, validated sequence of knots."""

    knots: tuple[Knot, ...]
    _xs: tuple[float, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        knots = tuple(self.knots)
        object.__setattr__(self, "knots", knots)
        if len(knots) < 2:
            raise CurveError(E.TOO_FEW_KNOTS, f"a curve needs at least 2 knots, got {len(knots)}")
        for knot in knots:
            if not isinstance(knot, Knot):
                raise CurveError(E.BAD_TYPE, "knots must be Knot instances")
        if knots[0].tin is not None:
            raise CurveError(E.ENDPOINT_TANGENT, "the first knot must not have an incoming tangent")
        if knots[-1].tout is not None:
            raise CurveError(E.ENDPOINT_TANGENT, "the last knot must not have an outgoing tangent")
        if knots[0].tout is None or knots[-1].tin is None:
            raise CurveError(E.MISSING_TANGENT, "endpoint knots need their inner tangent")
        for index, knot in enumerate(knots[1:-1], start=1):
            if knot.tin is None or knot.tout is None:
                raise CurveError(E.MISSING_TANGENT, f"interior knot {index} needs both tangents")
        xs = tuple(k.x for k in knots)
        for index in range(1, len(xs)):
            if not xs[index] > xs[index - 1]:
                raise CurveError(
                    E.NOT_INCREASING,
                    f"knot {index} x={xs[index]!r} does not exceed x={xs[index - 1]!r}",
                )
        object.__setattr__(self, "_xs", xs)

    # -- structure -------------------------------------------------------------------------

    @property
    def xs(self) -> tuple[float, ...]:
        return self._xs

    @property
    def segment_count(self) -> int:
        return len(self.knots) - 1

    @property
    def x_start(self) -> float:
        return self._xs[0]

    @property
    def x_end(self) -> float:
        return self._xs[-1]

    def is_normalized(self) -> bool:
        """True for an apply-ready curve: the domain is exactly ``[0, 1]``."""
        return self._xs[0] == 0.0 and self._xs[-1] == 1.0

    def interior_indices(self) -> range:
        return range(1, len(self.knots) - 1)

    def segment_controls(self, index: int) -> tuple[tuple[float, float, float, float],
                                                    tuple[float, float, float, float]]:
        """Return ``(xs, ys)`` control coordinates of segment ``index``."""
        a = self.knots[index]
        b = self.knots[index + 1]
        d = b.x - a.x
        ta = a.tout
        tb = b.tin
        assert ta is not None and tb is not None
        hx_a = ta.weight * d
        hx_b = tb.weight * d
        return (
            (a.x, a.x + hx_a, b.x - hx_b, b.x),
            (a.y, a.y + ta.slope * hx_a, b.y - tb.slope * hx_b, b.y),
        )


def make_knot(x: float, y: float, tin: tuple[float, float] | None,
              tout: tuple[float, float] | None, smooth: bool | None = None) -> Knot:
    """Convenience constructor from ``(slope, weight)`` pairs.

    ``smooth=None`` means "smooth if and only if both slopes are exactly equal".
    """
    t_in = Tangent(*tin) if tin is not None else None
    t_out = Tangent(*tout) if tout is not None else None
    if smooth is None:
        smooth = t_in is None or t_out is None or t_in.slope == t_out.slope
    return Knot(x, y, t_in, t_out, smooth)


def linear_curve(y0: float = 0.0, y1: float = 1.0) -> Curve:
    """The straight line from ``(0, y0)`` to ``(1, y1)`` with one-third handles."""
    slope = y1 - y0
    third = 1.0 / 3.0
    return Curve((
        Knot(0.0, y0, None, Tangent(slope, third)),
        Knot(1.0, y1, Tangent(slope, third), None),
    ))

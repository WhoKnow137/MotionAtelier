# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Commit planning (pure) and the read-back contract.

A normalised curve is mapped onto every valid selected channel. Each interval keeps its
boundary keys' times and values; every interior curve knot becomes a new key; keys strictly
inside the interval are removed; keys outside are never written.

Boundary keys are re-written (their committed half changes). Their *other* half — the one
facing outside the committed range — is written back explicitly with the slope and
acceleration Houdini was evaluating, auto off, absolute mode, so the neighbouring segment
cannot shift because of ties, auto slopes, or ratio mode.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import pairwise

from motion_atelier.app.results import Issue
from motion_atelier.app.snapshots import (
    BEZIER,
    HSCRIPT,
    ChannelSnapshot,
    KeySnapshot,
    SelectionSnapshot,
    normalise_expression,
    ordered_channels,
)
from motion_atelier.curve.model import Curve

APPLY_EACH = "each_interval"
APPLY_WHOLE = "whole_range"
APPLY_MODES = (APPLY_EACH, APPLY_WHOLE)

# Handles at or above this fraction of their segment are rejected: handles must not reach
# the full segment length (Houdini clamps or rebalances them); 0.999 leaves
# a visible margin while accepting every handle an artist can meaningfully draw.
MAX_STORABLE_WEIGHT = 0.999

# Two keys of one channel closer than this many frames are a collision. Houdini merges keys
# within ~1e-3 frame; 0.01 frame is ten times that and still far below any timing an
# artist can perceive.
MIN_KEY_SEPARATION_FRAMES = 0.01

# Read-back tolerance (relative). Stored doubles round-trip exactly, so anything
# beyond 1e-9 relative means Houdini altered what we wrote (e.g. clamped a handle).
READBACK_REL_TOL = 1e-9

# Stable codes.
NO_SELECTION = "no_selection"
CHANNEL_LOCKED = "channel_locked"
CHANNEL_NOT_EDITABLE = "channel_not_editable"
NOT_NUMERIC = "not_numeric"
TOO_FEW_KEYS = "too_few_keys"
CURVE_FLAT_ENDS = "curve_flat_ends"
HANDLE_FULL_LENGTH = "handle_full_length"
NOT_REPRESENTABLE = "not_representable"
TIME_COLLISION = "time_collision"
FLAT_INTERVAL = "flat_interval"
AUTO_TANGENT_FROZEN = "auto_tangent_frozen"
DISCONTINUITY_REMOVED = "discontinuity_removed"


@dataclass(frozen=True, slots=True)
class Half:
    slope: float
    accel: float


@dataclass(frozen=True, slots=True)
class KeyWrite:
    """The complete intended state of one written key."""

    time: float
    value: float
    in_value: float | None  # None: continuous (tied) value
    in_half: Half
    out_half: Half
    expression: str  # function of the segment starting at this key
    language: str
    created: bool


@dataclass(frozen=True, slots=True)
class ChannelPlan:
    """Deletions and writes for one channel.

    ``intervals`` are the time ranges allowed to change; everything else must evaluate
    identically afterwards. ``check_extrapolation`` is False when the plan legitimately moves
    or removes the channel's first or last key (so evaluation beyond them may change).
    """

    path: str
    snapshot: ChannelSnapshot
    intervals: tuple[tuple[float, float], ...]
    deletions: tuple[float, ...]
    writes: tuple[KeyWrite, ...]
    check_extrapolation: bool = True

    @property
    def keys_created(self) -> int:
        return sum(1 for w in self.writes if w.created)

    @property
    def keys_reused(self) -> int:
        return sum(1 for w in self.writes if not w.created)

    @property
    def keys_removed(self) -> int:
        return len(self.deletions)

    def touched_times(self) -> frozenset[float]:
        return frozenset(w.time for w in self.writes) | frozenset(self.deletions)


@dataclass(frozen=True, slots=True)
class CommitPlan:
    source: str
    channels: tuple[ChannelPlan, ...]
    channels_selected: int
    skips: tuple[Issue, ...] = ()
    warnings: tuple[Issue, ...] = ()

    @property
    def intervals(self) -> int:
        return sum(len(c.intervals) for c in self.channels)


class Skip(Exception):
    def __init__(self, issue: Issue) -> None:
        super().__init__(issue.code)
        self.issue = issue


def intervals_for(selected: tuple[int, ...], mode: str) -> list[tuple[int, int]]:
    if mode == APPLY_EACH:
        return [(selected[i], selected[i + 1]) for i in range(len(selected) - 1)]
    return [(selected[0], selected[-1])]


def freeze(key: KeySnapshot) -> KeyWrite:
    """Re-write a key exactly as Houdini evaluates it now: explicit halves, auto off."""
    return KeyWrite(key.time, key.value, key.in_value, Half(key.eff_in_slope, key.eff_in_accel),
                    Half(key.slope, key.accel), key.expression, key.language, False)


def map_interval(curve: Curve, k0: KeySnapshot, k1: KeySnapshot, path: str,
                 value_map: tuple[float, float] | None = None
                 ) -> tuple[list[tuple[float, float, Half | None, Half | None]], bool]:
    """Physical ``(time, value, in_half, out_half)`` per curve knot; returns ``(knots, flat)``.

    By default values follow the commit mapping (the curve's end values onto the keys' values;
    equal key values give a flat interval). ``value_map = (offset, scale)`` maps ``y`` to
    ``offset + scale·y`` instead (used by Reduce Keys, which keeps absolute values).
    """
    knots = curve.knots
    t0, t1 = k0.time, k1.time
    v0, v1 = k0.value, k1.value
    T = t1 - t0
    y0 = knots[0].y
    if value_map is not None:
        offset, scale = value_map
        flat = scale == 0.0
        def v_at(y):
            return offset + scale * y
    else:
        V = v1 - v0
        C = knots[-1].y - y0
        flat = V == 0.0
        if not flat and C == 0.0:
            raise Skip(Issue(CURVE_FLAT_ENDS, "", path))
        scale = 0.0 if flat else V / C  # value units per normalised y unit
        def v_at(y):
            return v0 if flat else v0 + (y - y0) * scale
    out: list[tuple[float, float, Half | None, Half | None]] = []
    last = len(knots) - 1

    def half(slope: float, weight: float, dn: float) -> Half:
        if weight >= MAX_STORABLE_WEIGHT:
            raise Skip(Issue(HANDLE_FULL_LENGTH, f"{weight:.4f}", path))
        dt = weight * dn * T
        dv = slope * weight * dn * scale
        phys_slope = dv / dt if dt > 0.0 else slope * scale / T
        accel = math.hypot(dt, dv)
        if not (math.isfinite(phys_slope) and math.isfinite(accel)):
            raise Skip(Issue(NOT_REPRESENTABLE, "", path))
        return Half(phys_slope + 0.0, accel)

    for j, k in enumerate(knots):
        time = t0 if j == 0 else t1 if j == last else t0 + k.x * T
        value = v0 if j == 0 else v1 if j == last else v_at(k.y)
        tin = half(k.tin.slope, k.tin.weight, k.x - knots[j - 1].x) if k.tin else None
        tout = half(k.tout.slope, k.tout.weight, knots[j + 1].x - k.x) if k.tout else None
        out.append((time, value, tin, tout))
    return out, flat


def plan_channel(channel: ChannelSnapshot, curve: Curve, mode: str, fps: float
                 ) -> tuple[ChannelPlan, list[Issue]]:
    path = channel.path
    if not channel.numeric:
        raise Skip(Issue(NOT_NUMERIC, "", path))
    if channel.locked:
        raise Skip(Issue(CHANNEL_LOCKED, "", path))
    if not channel.editable:
        raise Skip(Issue(CHANNEL_NOT_EDITABLE, "", path))
    if len(channel.selected) < 2:
        raise Skip(Issue(TOO_FEW_KEYS, f"{len(channel.selected)} selected", path))
    keys = channel.keys
    warnings: list[Issue] = []
    pairs = intervals_for(channel.selected, mode)
    starts = {i0: n for n, (i0, _) in enumerate(pairs)}
    ends = {i1: n for n, (_, i1) in enumerate(pairs)}
    mapped = []
    flats = 0
    for i0, i1 in pairs:
        result, flat = map_interval(curve, keys[i0], keys[i1], path)
        mapped.append(result)
        flats += flat
    if flats:
        warnings.append(Issue(FLAT_INTERVAL, str(flats), path))

    writes: list[KeyWrite] = []
    deletions: list[float] = []
    frozen = 0
    dropped = 0
    boundaries = sorted(set(starts) | set(ends))
    for b in boundaries:
        k = keys[b]
        if b in ends:
            in_half = mapped[ends[b]][-1][2]
            in_value = None
            if k.has_discontinuity:
                dropped += 1
        else:
            in_half = Half(k.eff_in_slope, k.eff_in_accel)
            in_value = k.in_value
            frozen += k.in_slope_auto
        if b in starts:
            out_half = mapped[starts[b]][0][3]
            expression, language = BEZIER, HSCRIPT
        else:
            out_half = Half(k.slope, k.accel)
            expression, language = k.expression, k.language
            frozen += k.slope_auto
        assert in_half is not None and out_half is not None
        writes.append(KeyWrite(k.time, k.value, in_value, in_half, out_half, expression,
                               language, False))
    for n, (i0, i1) in enumerate(pairs):
        deletions.extend(keys[i].time for i in range(i0 + 1, i1))
        for time, value, tin, tout in mapped[n][1:-1]:
            assert tin is not None and tout is not None
            writes.append(KeyWrite(time, value, None, tin, tout, BEZIER, HSCRIPT, True))
    writes.sort(key=lambda w: w.time)
    min_gap = MIN_KEY_SEPARATION_FRAMES / fps
    for a, b in pairwise(writes):
        if b.time - a.time < min_gap:
            raise Skip(Issue(TIME_COLLISION, f"{a.time:.6g}s / {b.time:.6g}s", path))
    if frozen:
        warnings.append(Issue(AUTO_TANGENT_FROZEN, str(frozen), path))
    if dropped:
        warnings.append(Issue(DISCONTINUITY_REMOVED, str(dropped), path))
    intervals = tuple((keys[i0].time, keys[i1].time) for i0, i1 in pairs)
    return ChannelPlan(path, channel, intervals, tuple(sorted(deletions)), tuple(writes)), warnings


def plan_commit(selection: SelectionSnapshot, curve: Curve, mode: str) -> CommitPlan:
    """Plan a commit of ``curve`` onto every valid selected channel (no scene access)."""
    if mode not in APPLY_MODES:
        raise ValueError(f"unknown apply mode {mode!r}")
    if not curve.is_normalized():
        raise ValueError("commit requires a normalised curve")
    channels = ordered_channels(c for c in selection.channels if c.selected)
    if not channels:
        return CommitPlan(selection.source, (), 0, (Issue(NO_SELECTION),))
    plans: list[ChannelPlan] = []
    skips: list[Issue] = []
    warnings: list[Issue] = []
    for channel in channels:
        try:
            plan, more = plan_channel(channel, curve, mode, selection.fps)
        except Skip as skip:
            skips.append(skip.issue)
            continue
        plans.append(plan)
        warnings.extend(more)
    return CommitPlan(selection.source, tuple(plans), len(channels), tuple(skips),
                      tuple(warnings))


# -- expected state and read-back comparison ------------------------------------------------


def expected_keys(plan: ChannelPlan) -> tuple[KeySnapshot | KeyWrite, ...]:
    """The channel's keys after the plan: untouched snapshots plus the writes, time-sorted."""
    removed = set(plan.deletions) | {w.time for w in plan.writes}
    kept: list[KeySnapshot | KeyWrite] = [k for k in plan.snapshot.keys if k.time not in removed]
    return tuple(sorted([*kept, *plan.writes], key=lambda k: k.time))


def _close(a: float, b: float, tol: float = READBACK_REL_TOL) -> bool:
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


def readback_mismatches(write: KeyWrite, actual: KeySnapshot, *, check_in: bool = True,
                        check_out: bool = True) -> list[str]:
    """Differences between what was written and what Houdini stored (empty when equal).

    ``check_in``/``check_out`` select which halves to compare: a half that bounds a
    non-Bezier segment does not affect evaluation and Houdini may recompute it; such
    segments are protected by the outside-evaluation check instead.
    """
    problems: list[str] = []
    checks = [
        ("time", write.time, actual.time),
        ("value", write.value, actual.value),
        ("in_value", write.value if write.in_value is None else write.in_value,
         actual.eff_in_value),
    ]
    if check_out:
        checks += [("slope", write.out_half.slope, actual.slope),
                   ("accel", write.out_half.accel, actual.accel)]
    if check_in:
        checks += [("in_slope", write.in_half.slope, actual.eff_in_slope),
                   ("in_accel", write.in_half.accel, actual.eff_in_accel)]
    for name, want, got in checks:
        if not _close(want, got):
            problems.append(f"{name} {want!r} -> {got!r}")
    if actual.slope_auto or actual.in_slope_auto:
        problems.append("auto slope enabled")
    if actual.accel_ratio:
        problems.append("ratio acceleration enabled")
    if normalise_expression(actual.expression) != normalise_expression(write.expression):
        problems.append(f"expression {write.expression!r} -> {actual.expression!r}")
    if actual.language != write.language:
        problems.append(f"language {write.language!r} -> {actual.language!r}")
    return problems

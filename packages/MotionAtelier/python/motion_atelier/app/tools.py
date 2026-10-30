# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Key tool planning (pure). Execution reuses the commit transaction.

Every tool turns a selection snapshot and options into :class:`ChannelPlan` objects. Tools
that move keys move the *selected range* of each channel — every key from the first to the
last selected key — as one block, so keys inside a range never cross. Collisions with
keys outside the block are rejected per channel (never silently overwritten). Unmoved
neighbours with automatic tangents are frozen at their current tangents so the motion outside
the changed range cannot shift.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

from motion_atelier.app import commit as C
from motion_atelier.app.commit import ChannelPlan, Half, KeyWrite, Skip, freeze
from motion_atelier.app.results import Issue
from motion_atelier.app.snapshots import (
    BEZIER,
    HSCRIPT,
    ChannelSnapshot,
    SelectionSnapshot,
    normalise_expression,
    ordered_channels,
)
from motion_atelier.app.tangents import handle_from_houdini
from motion_atelier.curve.model import Curve, Knot, Tangent
from motion_atelier.fitting.fit import DEFAULT_TOLERANCE_PCT, fit, tolerance_from_pct

TOOL_INSPECT = "inspect"
TOOL_REMOVE_INTERIOR = "remove_interior"
TOOL_REMOVE_OUTSIDE = "remove_outside"
TOOL_REDUCE = "reduce"
TOOL_SCALE = "scale"
TOOL_OFFSET = "offset_channels"
TOOL_SHIFT = "shift_following"
TOOL_MATCH = "match_durations"
TOOL_ALIGN = "align"
TOOL_IDS = (TOOL_INSPECT, TOOL_REMOVE_INTERIOR, TOOL_REMOVE_OUTSIDE, TOOL_REDUCE, TOOL_SCALE,
            TOOL_OFFSET, TOOL_SHIFT, TOOL_MATCH, TOOL_ALIGN)

# A finite stand-in for ±∞ in changed ranges (seconds); far beyond any Houdini timeline.
FAR = 1.0e12

# Stable codes.
NOTHING_TO_REMOVE = "nothing_to_remove"
NOTHING_TO_SHIFT = "nothing_to_shift"
NOTHING_TO_DO = "nothing_to_do"
UNSUPPORTED_SEGMENT = "unsupported_segment"
DISCONTINUITY = "discontinuity"
WOULD_CROSS_SELECTION = "would_cross_selection"
TIME_SCALE_ZERO = "time_scale_zero"
ZERO_DURATION = "zero_duration"
BAD_OPTION = "bad_option"
REVERSAL_WITH_DISCONTINUITY = "reversal_with_discontinuity"
TANGENTS_FROZEN = "tangents_frozen"
NOT_REDUCED = "not_reduced"
KEYS_REDUCED = "keys_reduced"


@dataclass(frozen=True)
class ToolPlan:
    tool: str
    source: str
    channels: tuple[ChannelPlan, ...]
    skips: tuple[Issue, ...] = ()
    warnings: tuple[Issue, ...] = ()
    read_only: bool = False
    report: tuple[tuple[str, str], ...] = ()
    channels_seen: int = 0
    # path -> (keys changed, keys created, keys removed)
    counts: dict[str, tuple[int, int, int]] = field(default_factory=dict)


# -- option handling -------------------------------------------------------------------------

DEFAULT_OPTIONS: dict[str, dict[str, Any]] = {
    TOOL_REDUCE: {"tolerance_pct": DEFAULT_TOLERANCE_PCT},
    TOOL_SCALE: {"time_scale": 1.0, "value_scale": 1.0, "time_pivot": "first",
                 "value_pivot": "first"},
    TOOL_OFFSET: {"frames": 2.0, "order": "listed"},
    TOOL_SHIFT: {"frames": 10.0},
    TOOL_MATCH: {"target": "longest", "frames": 24.0, "anchor": "start"},
    TOOL_ALIGN: {"edge": "start"},
}
CHOICES = {
    "time_pivot": ("first", "center", "last", "playhead"),
    "value_pivot": ("first", "center", "zero", "minimum", "maximum"),
    "target": ("longest", "shortest", "first", "frames"),
    "anchor": ("start", "center", "end"),
    "edge": ("start", "end"),
    # Channels are always listed in path order (Houdini does not report selection order);
    # "reverse" staggers from the last channel instead.
    "order": ("listed", "reverse"),
}


def options_for(tool: str, given: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(DEFAULT_OPTIONS.get(tool, {}))
    for key, value in (given or {}).items():
        if key not in merged:
            raise Skip(Issue(BAD_OPTION, f"unknown option {key!r}"))
        merged[key] = value
    for key, value in merged.items():
        if key in CHOICES:
            if value not in CHOICES[key]:
                raise Skip(Issue(BAD_OPTION, f"{key}={value!r}"))
        elif isinstance(value, bool) or not isinstance(value, int | float) or \
                not math.isfinite(value):
            raise Skip(Issue(BAD_OPTION, f"{key}={value!r}"))
    return merged


# -- shared helpers --------------------------------------------------------------------------


def _check_writable(channel: ChannelSnapshot, min_selected: int = 1) -> None:
    path = channel.path
    if not channel.numeric:
        raise Skip(Issue(C.NOT_NUMERIC, "", path))
    if channel.locked:
        raise Skip(Issue(C.CHANNEL_LOCKED, "", path))
    if not channel.editable:
        raise Skip(Issue(C.CHANNEL_NOT_EDITABLE, "", path))
    if len(channel.selected) < min_selected:
        raise Skip(Issue(C.TOO_FEW_KEYS, f"{len(channel.selected)} selected", path))


def _scaled_half(slope: float, accel: float, s_t: float, s_v: float) -> Half:
    dt, dv = handle_from_houdini(slope, accel)
    return Half(slope * s_v / s_t, math.hypot(abs(s_t) * dt, s_v * dv))


def plan_block_move(channel: ChannelSnapshot, a: int, b: int, time_pivot: float, s_t: float,
                    delta_t: float, value_pivot: float, s_v: float, fps: float
                    ) -> tuple[ChannelPlan, list[Issue], int]:
    """Move keys ``a..b`` by ``t' = p + s_t·(t − p) + Δ`` and ``v' = q + s_v·(v − q)``."""
    path = channel.path
    keys = channel.keys
    if s_t == 0.0:
        raise Skip(Issue(TIME_SCALE_ZERO, "", path))
    block = keys[a : b + 1]

    def new_t(t: float) -> float:
        return time_pivot + s_t * (t - time_pivot) + delta_t

    def new_v(v: float) -> float:
        return value_pivot + s_v * (v - value_pivot)

    if s_t == 1.0 and delta_t == 0.0 and s_v == 1.0:
        raise Skip(Issue(NOTHING_TO_DO, "", path))
    reverse = s_t < 0.0
    if reverse and any(k.has_discontinuity for k in block):
        raise Skip(Issue(REVERSAL_WITH_DISCONTINUITY, "", path))
    sep = C.MIN_KEY_SEPARATION_FRAMES / fps
    moved_times = [new_t(k.time) for k in block]
    lo, hi = min(moved_times), max(moved_times)
    # The block must stay strictly between its unmoved neighbours: key order is preserved, so
    # no key is ever overwritten or jumped over.
    left = keys[a - 1].time if a > 0 else -FAR
    right = keys[b + 1].time if b + 1 < len(keys) else FAR
    if lo <= left + sep or hi >= right - sep:
        blocker = left if lo <= left + sep else right
        raise Skip(Issue(C.TIME_COLLISION, f"{blocker:.6g}s", path))
    ordered = sorted(moved_times)
    for x, y in pairwise(ordered):
        if y - x < sep:
            raise Skip(Issue(C.TIME_COLLISION, f"{x:.6g}s / {y:.6g}s", path))
    writes: list[KeyWrite] = []
    frozen = 0
    n = len(block)
    for j, k in enumerate(block):
        out_half = _scaled_half(k.slope, k.accel, s_t, s_v)
        in_half = _scaled_half(k.eff_in_slope, k.eff_in_accel, s_t, s_v)
        frozen += k.slope_auto or k.in_slope_auto
        if reverse:
            in_half, out_half = out_half, in_half
            owner = block[j - 1] if j > 0 else block[n - 1]
            expression, language = owner.expression, owner.language
            in_value = None
        else:
            expression, language = k.expression, k.language
            in_value = None if k.in_value is None else new_v(k.in_value)
        writes.append(KeyWrite(moved_times[j], new_v(k.value), in_value, in_half, out_half,
                               expression, language, False))
    # Unmoved neighbours are always re-written as they are: deleting the block's keys makes
    # Houdini rescale their facing accelerations.
    for neighbour in (keys[a - 1] if a > 0 else None, keys[b + 1] if b + 1 < len(keys) else None):
        if neighbour is not None:
            writes.append(freeze(neighbour))
            frozen += neighbour.slope_auto or neighbour.in_slope_auto
    deletions = tuple(sorted(k.time for k in block))
    old_lo, old_hi = block[0].time, block[-1].time
    changed_lo = min(left, lo, old_lo)
    changed_hi = max(right, hi, old_hi)
    warnings = [Issue(TANGENTS_FROZEN, str(frozen), path)] if frozen else []
    plan = ChannelPlan(path, channel, ((changed_lo, changed_hi),), deletions,
                       tuple(sorted(writes, key=lambda w: w.time)),
                       check_extrapolation=a > 0 and b + 1 < len(keys))
    return plan, warnings, (n, 0, 0)


def _selected_range(channel: ChannelSnapshot) -> tuple[int, int]:
    return channel.selected[0], channel.selected[-1]


# -- individual tools ------------------------------------------------------------------------


def _inspect(selection: SelectionSnapshot) -> ToolPlan:
    rows: list[tuple[str, str]] = [("source", selection.source),
                                   ("channels", str(len(selection.channels)))]
    for channel in ordered_channels(selection.channels):
        sel = channel.selected_keys()
        if sel:
            f0 = sel[0].time * selection.fps + 1.0
            f1 = sel[-1].time * selection.fps + 1.0
            values = [k.value for k in sel]
            functions = sorted({normalise_expression(k.expression) for k in channel.keys})
            detail = (f"{len(sel)}/{len(channel.keys)} keys, frames {f0:.6g}-{f1:.6g}, "
                      f"values {min(values):.6g}..{max(values):.6g}, "
                      f"{', '.join(functions)}")
        else:
            detail = f"0/{len(channel.keys)} keys"
        flags = (" [locked]" if channel.locked else "") + ("" if channel.numeric
                                                          else " [not numeric]")
        rows.append((channel.path, detail + flags))
    return ToolPlan(TOOL_INSPECT, selection.source, (), read_only=True, report=tuple(rows),
                    channels_seen=len(selection.channels))


def _remove_interior(channel: ChannelSnapshot, fps: float, options) -> tuple:
    _check_writable(channel, 2)
    a, b = _selected_range(channel)
    keys = channel.keys
    inside = keys[a + 1 : b]
    if not inside:
        raise Skip(Issue(NOTHING_TO_REMOVE, "", channel.path))
    writes = (freeze(keys[a]), freeze(keys[b]))
    plan = ChannelPlan(channel.path, channel, ((keys[a].time, keys[b].time),),
                       tuple(k.time for k in inside), writes)
    return plan, [], (0, 0, len(inside))


def _remove_outside(channel: ChannelSnapshot, fps: float, options) -> tuple:
    _check_writable(channel, 1)
    a, b = _selected_range(channel)
    keys = channel.keys
    gone = keys[:a] + keys[b + 1 :]
    if not gone:
        raise Skip(Issue(NOTHING_TO_REMOVE, "", channel.path))
    writes = (freeze(keys[a]),) if a == b else (freeze(keys[a]), freeze(keys[b]))
    plan = ChannelPlan(channel.path, channel, ((-FAR, keys[a].time), (keys[b].time, FAR)),
                       tuple(k.time for k in gone), writes, check_extrapolation=False)
    return plan, [], (0, 0, len(gone))


def range_curve(channel: ChannelSnapshot) -> tuple[Curve, float, float]:
    """Exact normalised curve of the selected range: ``(curve, value_offset, value_span)``.

    Only Bezier and linear segments are representable exactly (a linear segment is a Bezier
    with secant slopes and one-third weights).
    """
    a, b = _selected_range(channel)
    keys = channel.keys[a : b + 1]
    path = channel.path
    t0, t1 = keys[0].time, keys[-1].time
    if t1 <= t0:
        raise Skip(Issue(ZERO_DURATION, "", path))
    values = [k.value for k in keys]
    vmin = min(values)
    span = max(values) - vmin
    scale_v = span if span > 0.0 else 1.0
    T = t1 - t0
    tin: list[Tangent | None] = [None] * len(keys)
    tout: list[Tangent | None] = [None] * len(keys)
    for i in range(len(keys) - 1):
        k, nxt = keys[i], keys[i + 1]
        interior_jump = (i > 0 and k.has_discontinuity) or (
            i + 1 < len(keys) - 1 and nxt.has_discontinuity)
        if interior_jump:
            raise Skip(Issue(DISCONTINUITY, f"{k.time:.6g}s", path))
        d = nxt.time - k.time
        function = normalise_expression(k.expression)
        if k.language == HSCRIPT and function == BEZIER:
            dta, _ = handle_from_houdini(k.slope, k.accel)
            dtb, _ = handle_from_houdini(nxt.eff_in_slope, nxt.eff_in_accel)
            if dta > d or dtb > d:
                raise Skip(Issue(UNSUPPORTED_SEGMENT, "handle longer than segment", path))
            tout[i] = Tangent(k.slope * T / scale_v, dta / d)
            tin[i + 1] = Tangent(nxt.eff_in_slope * T / scale_v, dtb / d)
        elif k.language == HSCRIPT and function == "linear()":
            secant = (nxt.eff_in_value - k.value) / d * T / scale_v
            tout[i] = Tangent(secant, 1.0 / 3.0)
            tin[i + 1] = Tangent(secant, 1.0 / 3.0)
        else:
            raise Skip(Issue(UNSUPPORTED_SEGMENT, function, path))
    knots = []
    for i, k in enumerate(keys):
        x = 0.0 if i == 0 else 1.0 if i == len(keys) - 1 else (k.time - t0) / T
        t_in, t_out = tin[i], tout[i]
        smooth = t_in is None or t_out is None or t_in.slope == t_out.slope
        knots.append(Knot(x, (k.value - vmin) / scale_v, t_in, t_out, smooth))
    return Curve(tuple(knots)), vmin, scale_v


def _reduce(channel: ChannelSnapshot, fps: float, options) -> tuple:
    _check_writable(channel, 2)
    curve, offset, span = range_curve(channel)
    result = fit(curve, tolerance_from_pct(curve, options["tolerance_pct"]))
    if not result.reduced:
        raise Skip(Issue(NOT_REDUCED, result.reason or "", channel.path))
    a, b = _selected_range(channel)
    keys = channel.keys
    mapped, _ = C.map_interval(result.curve, keys[a], keys[b], channel.path, (offset, span))
    writes = [KeyWrite(keys[a].time, keys[a].value, keys[a].in_value,
                       Half(keys[a].eff_in_slope, keys[a].eff_in_accel), mapped[0][3],
                       BEZIER, HSCRIPT, False)]
    for time, value, t_in, t_out in mapped[1:-1]:
        writes.append(KeyWrite(time, value, None, t_in, t_out, BEZIER, HSCRIPT, True))
    writes.append(KeyWrite(keys[b].time, keys[b].value, None, mapped[-1][2],
                           Half(keys[b].slope, keys[b].accel), keys[b].expression,
                           keys[b].language, False))
    plan = ChannelPlan(channel.path, channel, ((keys[a].time, keys[b].time),),
                       tuple(k.time for k in keys[a + 1 : b]), tuple(writes))
    detail = f"{b - a + 1} -> {len(mapped)}"
    return plan, [Issue(KEYS_REDUCED, detail, channel.path)], (0, len(mapped) - 2, b - a - 1)


def _scale(channel: ChannelSnapshot, fps: float, options, playhead: float) -> tuple:
    _check_writable(channel, 1)
    a, b = _selected_range(channel)
    keys = channel.keys
    t_first, t_last = keys[a].time, keys[b].time
    pivot_t = {"first": t_first, "last": t_last, "center": 0.5 * (t_first + t_last),
               "playhead": playhead}[options["time_pivot"]]
    values = [k.value for k in keys[a : b + 1]]
    pivot_v = {"first": keys[a].value, "zero": 0.0, "minimum": min(values),
               "maximum": max(values),
               "center": 0.5 * (min(values) + max(values))}[options["value_pivot"]]
    return plan_block_move(channel, a, b, pivot_t, float(options["time_scale"]), 0.0, pivot_v,
                           float(options["value_scale"]), fps)


def _shift_block(channel: ChannelSnapshot, fps: float, delta_t: float) -> tuple:
    a, b = _selected_range(channel)
    return plan_block_move(channel, a, b, 0.0, 1.0, delta_t, 0.0, 1.0, fps)


def _shift_following(channel: ChannelSnapshot, fps: float, options) -> tuple:
    _check_writable(channel, 1)
    _, b = _selected_range(channel)
    keys = channel.keys
    if b + 1 >= len(keys):
        raise Skip(Issue(NOTHING_TO_SHIFT, "", channel.path))
    delta = float(options["frames"]) / fps
    sep = C.MIN_KEY_SEPARATION_FRAMES / fps
    if keys[b + 1].time + delta <= keys[b].time + sep:
        raise Skip(Issue(WOULD_CROSS_SELECTION, "", channel.path))
    return plan_block_move(channel, b + 1, len(keys) - 1, 0.0, 1.0, delta, 0.0, 1.0, fps)


# -- dispatcher ------------------------------------------------------------------------------


def plan_tool(tool: str, selection: SelectionSnapshot, options: dict[str, Any] | None = None
              ) -> ToolPlan:
    if tool not in TOOL_IDS:
        raise ValueError(f"unknown tool {tool!r}")
    if tool == TOOL_INSPECT:
        return _inspect(selection)
    try:
        opts = options_for(tool, options)
    except Skip as skip:
        return ToolPlan(tool, selection.source, (), (skip.issue,))
    channels = ordered_channels(c for c in selection.channels if c.selected)
    if not channels:
        return ToolPlan(tool, selection.source, (), (Issue(C.NO_SELECTION),))
    fps = selection.fps
    plans: list[ChannelPlan] = []
    skips: list[Issue] = []
    warnings: list[Issue] = []
    counts: dict[str, tuple[int, int, int]] = {}
    durations = {}
    if tool == TOOL_MATCH:
        for c in channels:
            a, b = _selected_range(c)
            durations[c.path] = c.keys[b].time - c.keys[a].time
        valid = [d for d in durations.values() if d > 0.0]
        target = None
        if opts["target"] == "frames":
            target = float(opts["frames"]) / fps
        elif valid:
            target = {"longest": max(valid), "shortest": min(valid),
                      "first": next(iter(d for d in durations.values() if d > 0.0))}[
                opts["target"]]
        if target is None or target <= 0.0:
            return ToolPlan(tool, selection.source, (), (Issue(ZERO_DURATION),))
    for index, channel in enumerate(channels):
        try:
            if tool == TOOL_REMOVE_INTERIOR:
                plan, more, count = _remove_interior(channel, fps, opts)
            elif tool == TOOL_REMOVE_OUTSIDE:
                plan, more, count = _remove_outside(channel, fps, opts)
            elif tool == TOOL_REDUCE:
                plan, more, count = _reduce(channel, fps, opts)
            elif tool == TOOL_SCALE:
                plan, more, count = _scale(channel, fps, opts, selection.playhead_time)
            elif tool == TOOL_OFFSET:
                _check_writable(channel, 1)
                step = index if opts["order"] == "listed" else len(channels) - 1 - index
                if step == 0:
                    continue  # the first channel in the order is the reference and stays
                plan, more, count = _shift_block(channel, fps, step * float(opts["frames"])
                                                 / fps)
            elif tool == TOOL_SHIFT:
                plan, more, count = _shift_following(channel, fps, opts)
            elif tool == TOOL_MATCH:
                _check_writable(channel, 2)
                a, b = _selected_range(channel)
                duration = durations[channel.path]
                if duration <= 0.0:
                    raise Skip(Issue(ZERO_DURATION, "", channel.path))
                t0, t1 = channel.keys[a].time, channel.keys[b].time
                pivot = {"start": t0, "end": t1, "center": 0.5 * (t0 + t1)}[opts["anchor"]]
                plan, more, count = plan_block_move(channel, a, b, pivot, target / duration,
                                                    0.0, 0.0, 1.0, fps)
            else:  # TOOL_ALIGN
                _check_writable(channel, 1)
                a, b = _selected_range(channel)
                edge_time = channel.keys[a if opts["edge"] == "start" else b].time
                plan, more, count = _shift_block(channel, fps,
                                                 selection.playhead_time - edge_time)
        except Skip as skip:
            skips.append(skip.issue)
            continue
        plans.append(plan)
        warnings.extend(more)
        counts[channel.path] = count
    return ToolPlan(tool, selection.source, tuple(plans), tuple(skips), tuple(warnings),
                    channels_seen=len(channels), counts=counts)

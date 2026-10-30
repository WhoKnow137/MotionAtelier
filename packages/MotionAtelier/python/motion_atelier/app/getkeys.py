# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Get Keys planning (pure).

The first valid channel in deterministic order is read into a normalised curve. A valid
channel has at least two selected keys with non-zero time span and non-zero value span over
the selected keys. The curve spans every key from the first to the last selected key, so the
existing motion between them is reproduced. Time is normalised by the selected span
and value by min–max over the selected keys. Tangents are read only from Bezier segments
(the segment belongs to its starting key); other segment types contribute flat
tangents and are reported.
"""

from __future__ import annotations

from motion_atelier.app.results import GetKeysResult, Issue
from motion_atelier.app.snapshots import ChannelSnapshot, SelectionSnapshot, ordered_channels
from motion_atelier.app.tangents import handle_from_houdini
from motion_atelier.curve.model import Curve, Knot, Tangent

THIRD = 1.0 / 3.0

# Stable issue codes.
NO_SELECTION = "no_selection"
NOT_NUMERIC = "not_numeric"
TOO_FEW_KEYS = "too_few_keys"
NO_TIME_SPAN = "no_time_span"
NO_VALUE_SPAN = "no_value_span"
NON_BEZIER_SEGMENT = "non_bezier_segment"
HANDLE_CLAMPED = "handle_clamped"
VALUE_DISCONTINUITY = "value_discontinuity"
OTHER_CHANNELS_IGNORED = "other_channels_ignored"


def channel_problem(channel: ChannelSnapshot) -> Issue | None:
    """Why a channel cannot be read (``None`` when it is valid)."""
    if not channel.numeric:
        return Issue(NOT_NUMERIC, "", channel.path)
    selected = channel.selected_keys()
    if len(selected) < 2:
        return Issue(TOO_FEW_KEYS, f"{len(selected)} selected", channel.path)
    if selected[-1].time <= selected[0].time:
        return Issue(NO_TIME_SPAN, "", channel.path)
    values = [k.value for k in selected]
    if max(values) == min(values):
        return Issue(NO_VALUE_SPAN, "", channel.path)
    return None


def channel_to_curve(channel: ChannelSnapshot) -> tuple[Curve, tuple[Issue, ...]]:
    """Normalise a valid channel's selected range into a curve (see module docstring)."""
    first, last = channel.selected[0], channel.selected[-1]
    keys = channel.keys[first : last + 1]
    selected = channel.selected_keys()
    t0, t1 = keys[0].time, keys[-1].time
    vmin = min(k.value for k in selected)
    vspan = max(k.value for k in selected) - vmin
    tspan = t1 - t0
    slope_scale = tspan / vspan
    n = len(keys)
    tin: list[Tangent | None] = [None] * n
    tout: list[Tangent | None] = [None] * n
    non_bezier = 0
    clamped = 0
    for i in range(n - 1):
        a, b = keys[i], keys[i + 1]
        d = b.time - a.time
        if a.is_bezier:
            dt_a, _ = handle_from_houdini(a.slope, a.accel)
            dt_b, _ = handle_from_houdini(b.eff_in_slope, b.eff_in_accel)
            wa, wb = dt_a / d, dt_b / d
            if wa > 1.0 or wb > 1.0:
                clamped += 1
            tout[i] = Tangent(a.slope * slope_scale, min(wa, 1.0))
            tin[i + 1] = Tangent(b.eff_in_slope * slope_scale, min(wb, 1.0))
        else:
            non_bezier += 1
            tout[i] = Tangent(0.0, THIRD)
            tin[i + 1] = Tangent(0.0, THIRD)
    knots = []
    discontinuities = 0
    for i, k in enumerate(keys):
        x = 0.0 if i == 0 else 1.0 if i == n - 1 else (k.time - t0) / tspan
        if 0 < i < n - 1 and k.has_discontinuity:
            discontinuities += 1
        t_in, t_out = tin[i], tout[i]
        smooth = t_in is None or t_out is None or t_in.slope == t_out.slope
        knots.append(Knot(x, (k.value - vmin) / vspan, t_in, t_out, smooth))
    issues = []
    if non_bezier:
        issues.append(Issue(NON_BEZIER_SEGMENT, str(non_bezier), channel.path))
    if clamped:
        issues.append(Issue(HANDLE_CLAMPED, str(clamped), channel.path))
    if discontinuities:
        issues.append(Issue(VALUE_DISCONTINUITY, str(discontinuities), channel.path))
    return Curve(tuple(knots)), tuple(issues)


def plan_get_keys(selection: SelectionSnapshot) -> GetKeysResult:
    channels = ordered_channels(c for c in selection.channels if c.selected)
    if not channels:
        return GetKeysResult(False, selection.source, skips=(Issue(NO_SELECTION),))
    skips: list[Issue] = []
    chosen: ChannelSnapshot | None = None
    others = 0
    for channel in channels:
        problem = channel_problem(channel)
        if problem is not None:
            skips.append(problem)
        elif chosen is None:
            chosen = channel
        else:
            others += 1
    if chosen is None:
        return GetKeysResult(False, selection.source, channels_selected=len(channels),
                             skips=tuple(skips))
    curve, warnings = channel_to_curve(chosen)
    if others:
        warnings = (*warnings, Issue(OTHER_CHANNELS_IGNORED, str(others)))
    return GetKeysResult(
        True,
        selection.source,
        channel=chosen.path,
        curve=curve,
        key_count=len(curve.knots),
        channels_selected=len(channels),
        skips=tuple(skips),
        warnings=warnings,
    )

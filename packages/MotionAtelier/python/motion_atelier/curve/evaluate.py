# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Curve evaluation: value ``y(x)``, velocity ``dy/dx``, sorted sampling, value bounds.

Outside ``[x_start, x_end]`` the curve holds its endpoint values (constant extrapolation)
and its velocity is zero; editing views never query there, but effect pipelines may.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Iterable, Sequence

from motion_atelier.curve import bezier
from motion_atelier.curve.model import Curve

LEFT = "left"
RIGHT = "right"


def segment_index(curve: Curve, x: float) -> int:
    """Index of the segment containing ``x`` (half-open ``[x_i, x_{i+1})``, last closed)."""
    xs = curve.xs
    if x <= xs[0]:
        return 0
    if x >= xs[-1]:
        return len(xs) - 2
    return bisect.bisect_right(xs, x) - 1


def value_at(curve: Curve, x: float) -> float:
    xs = curve.xs
    if x <= xs[0]:
        return curve.knots[0].y
    if x >= xs[-1]:
        return curve.knots[-1].y
    i = segment_index(curve, x)
    px, py = curve.segment_controls(i)
    return bezier.bernstein(py, bezier.solve_u(px, x))


def velocity_at(curve: Curve, x: float, side: str = RIGHT) -> float:
    """``dy/dx`` at ``x``. At a knot, ``side`` chooses the one-sided derivative.

    The derivative at a segment end is the analytic limit (L'Hôpital on the control
    polygon) even for zero-length handles. May return ``±inf`` only for full-length handle
    configurations with a vertical tangent (see :func:`bezier.slope_at`).
    """
    xs = curve.xs
    if x < xs[0] or x > xs[-1]:
        return 0.0
    i = bisect.bisect_left(xs, x)
    if i < len(xs) and xs[i] == x:  # exactly on knot i
        if (side == LEFT and i > 0) or i == len(xs) - 1:
            px, py = curve.segment_controls(i - 1)
            return bezier.slope_at(px, py, 1.0)
        px, py = curve.segment_controls(i)
        return bezier.slope_at(px, py, 0.0)
    seg = i - 1
    px, py = curve.segment_controls(seg)
    return bezier.slope_at(px, py, bezier.solve_u(px, x))


def sample_sorted(curve: Curve, xs_sorted: Iterable[float]) -> list[float]:
    """Evaluate ``y`` at non-decreasing ``xs`` in one sweep: ``O(n + k)`` segment lookups."""
    knots = curve.knots
    kx = curve.xs
    last = len(kx) - 2
    out: list[float] = []
    seg = 0
    px, py = curve.segment_controls(0)
    prev = -math.inf
    for x in xs_sorted:
        if x < prev:
            raise ValueError("sample_sorted requires non-decreasing x")
        prev = x
        if x <= kx[0]:
            out.append(knots[0].y)
            continue
        if x >= kx[-1]:
            out.append(knots[-1].y)
            continue
        moved = False
        while seg < last and x >= kx[seg + 1]:
            seg += 1
            moved = True
        if moved:
            px, py = curve.segment_controls(seg)
        out.append(bezier.bernstein(py, bezier.solve_u(px, x)))
    return out


def uniform_xs(x0: float, x1: float, count: int) -> list[float]:
    if count < 2:
        raise ValueError("count must be at least 2")
    span = x1 - x0
    last = count - 1
    xs = [x0 + span * (i / last) for i in range(count)]
    xs[-1] = x1
    return xs


def sample(curve: Curve, count: int) -> tuple[list[float], list[float]]:
    """``count`` uniform samples over the curve's domain: ``(xs, ys)``."""
    xs = uniform_xs(curve.x_start, curve.x_end, count)
    return xs, sample_sorted(curve, xs)


def velocity_samples(curve: Curve, xs_sorted: Sequence[float]) -> list[float]:
    return [velocity_at(curve, x) for x in xs_sorted]


def value_bounds(curve: Curve) -> tuple[float, float]:
    """Exact ``(min, max)`` of ``y`` over the domain (knots plus interior extrema)."""
    lo = hi = curve.knots[0].y
    for i in range(curve.segment_count):
        _, py = curve.segment_controls(i)
        candidates = [py[3]] + [bezier.bernstein(py, u) for u in bezier.value_extrema_params(py)]
        for v in candidates:
            lo = min(lo, v)
            hi = max(hi, v)
    return lo, hi


def control_bounds(curve: Curve) -> tuple[float, float, float, float]:
    """``(xmin, xmax, ymin, ymax)`` over knots and handle points (for framing)."""
    xmin, xmax = curve.x_start, curve.x_end
    ymin = ymax = curve.knots[0].y
    for i in range(curve.segment_count):
        _, py = curve.segment_controls(i)
        ymin = min(ymin, *py)
        ymax = max(ymax, *py)
    return xmin, xmax, ymin, ymax

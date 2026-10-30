# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Sparse Bezier fitting: replace a dense curve with few keys within a tolerance.

Contract: the result stays within ``tolerance`` (value units) of the source at every dense
verification probe; endpoint values and slopes are exact; prominent extrema are knots; the
result never has more knots than the input; if no smaller verified result exists the input is
returned unchanged with a reason; the procedure is deterministic.

Algorithm:

1. *Probe table* — the source is evaluated exactly at a dense uniform grid plus every source
   knot (the same table is used for the final verification).
2. *Landmarks* — endpoints, kinks (one-sided slopes differ), and extrema whose height relative
   to neighbouring extrema is at least :data:`PROMINENCE_FACTOR` × tolerance. Landmarks are
   always knots and split the problem into independent chunks.
3. *Candidates* — per chunk, the source knots and a uniform grid inside it, at most
   :data:`MAX_CANDIDATES_PER_CHUNK`, chosen deterministically.
4. *Span fit* — between two candidates the end values and one-sided slopes are taken from the
   source; only the two handle weights are free. One-third weights (cubic Hermite) are tried
   first; spans that are close are refined by a compass search on the max error over the
   span's probes. A span is accepted below :data:`SEARCH_MARGIN` × tolerance.
5. *Path search* — dynamic programming for the fewest spans per chunk; reach from each
   candidate stops after :data:`FAIL_STREAK` consecutive failures; a global
   :data:`MAX_SPAN_FITS` budget bounds the work.
6. *Verification* — the assembled curve is compared with the exact source at every probe; any
   violation returns the input with ``no_safe_reduction``.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from itertools import pairwise

from motion_atelier.curve import bezier
from motion_atelier.curve.evaluate import LEFT, sample_sorted, uniform_xs, value_bounds, velocity_at
from motion_atelier.curve.model import Curve, Knot, Tangent

THIRD = 1.0 / 3.0

# Dense probe grid for search and verification. 2048 intervals resolve features down to
# 0.05 % of the duration; with source knots added, every source segment gets probes.
PROBE_INTERVALS = 2048
# Parameter samples per span during the search.
SPAN_PROBES = 24
# Extrema at least this many tolerances high (relative to neighbouring extrema) are kept as
# knots: smaller ones are within reach of the tolerance band and may be smoothed away.
PROMINENCE_FACTOR = 2.0
# One-sided slopes differing by more than this (relative) mark a kink that must be a knot.
KINK_REL_TOL = 1e-6
MAX_CANDIDATES_PER_CHUNK = 64
GRID_CANDIDATES_PER_CHUNK = 48
FAIL_STREAK = 3
MAX_SPAN_FITS = 20000
# Spans are accepted below 80 % of the tolerance during the search, leaving headroom for the
# denser final verification.
SEARCH_MARGIN = 0.8
# Spans above this many tolerances with Hermite weights are rejected without a search.
SEARCH_GIVE_UP = 6.0
MIN_FIT_WEIGHT = 0.01
MAX_FIT_WEIGHT = 0.95  # always storable in Houdini (commit rejects >= 0.999)
SEARCH_STEP = 0.15
SEARCH_MIN_STEP = 0.01

# Tolerance presented to the user, as a percentage of the curve's value range.
DEFAULT_TOLERANCE_PCT = 0.5
MIN_TOLERANCE_PCT = 0.05
MAX_TOLERANCE_PCT = 10.0

# Reasons for returning the input unchanged.
NO_REDUCTION = "no_reduction"
NO_SAFE_REDUCTION = "no_safe_reduction"
FIT_BUDGET_EXCEEDED = "fit_budget_exceeded"


@dataclass(frozen=True, slots=True)
class FitResult:
    curve: Curve
    reduced: bool
    reason: str | None
    max_error: float
    tolerance: float
    knots_in: int
    knots_out: int


class _Budget(Exception):
    pass


class _Source:
    """Exact source values and one-sided slopes on the probe table."""

    def __init__(self, curve: Curve) -> None:
        self.curve = curve
        grid = uniform_xs(curve.x_start, curve.x_end, PROBE_INTERVALS + 1)
        xs = sorted(set(grid) | set(curve.xs))
        self.xs = xs
        self.ys = sample_sorted(curve, xs)

    def value(self, x: float) -> float:
        i = bisect.bisect_left(self.xs, x)
        if i < len(self.xs) and self.xs[i] == x:
            return self.ys[i]
        return sample_sorted(self.curve, [x])[0]

    def slope_right(self, x: float) -> float:
        return velocity_at(self.curve, x)

    def slope_left(self, x: float) -> float:
        return velocity_at(self.curve, x, LEFT)


def _landmarks(src: _Source, tol: float) -> list[float]:
    curve = src.curve
    marks = {curve.x_start, curve.x_end}
    for k in curve.knots[1:-1]:
        s_in, s_out = velocity_at(curve, k.x, LEFT), velocity_at(curve, k.x)
        if abs(s_in - s_out) > KINK_REL_TOL * (1.0 + abs(s_in) + abs(s_out)):
            marks.add(k.x)
    extrema: list[tuple[float, float]] = []
    for i in range(curve.segment_count):
        px, py = curve.segment_controls(i)
        for u in bezier.value_extrema_params(py):
            extrema.append((bezier.bernstein(px, u), bezier.bernstein(py, u)))
    # Knots where the value direction reverses are extrema too.
    ys = [k.y for k in curve.knots]
    for i in range(1, len(ys) - 1):
        if (ys[i] - ys[i - 1]) * (ys[i + 1] - ys[i]) < 0.0:
            extrema.append((curve.knots[i].x, ys[i]))
    extrema.sort()
    seq = [(curve.x_start, curve.knots[0].y), *extrema, (curve.x_end, curve.knots[-1].y)]
    threshold = PROMINENCE_FACTOR * tol
    for j in range(1, len(seq) - 1):
        x, y = seq[j]
        height = min(abs(y - seq[j - 1][1]), abs(y - seq[j + 1][1]))
        if height >= threshold:
            marks.add(x)
    return sorted(marks)


def _candidates(src: _Source, a: float, b: float) -> list[float]:
    inner = [x for x in src.curve.xs if a < x < b]
    grid = uniform_xs(a, b, GRID_CANDIDATES_PER_CHUNK + 1)[1:-1]
    pool = sorted(set(inner) | set(grid))
    if len(pool) > MAX_CANDIDATES_PER_CHUNK - 2:
        step = len(pool) / (MAX_CANDIDATES_PER_CHUNK - 2)
        pool = [pool[int(i * step)] for i in range(MAX_CANDIDATES_PER_CHUNK - 2)]
    return [a, *pool, b]


class _SpanFitter:
    def __init__(self, src: _Source, tol: float) -> None:
        self.src = src
        self.tol = tol
        self.fits = 0
        self.cache: dict[float, tuple[float, float, float]] = {}
        # Interior parameters (j + ½)/SPAN_PROBES: uniform in u, never the exact span ends.
        self.us = [(j + 0.5) / SPAN_PROBES for j in range(SPAN_PROBES)]

    def _error(self, xa, ya, sa, wa, xb, yb, sb, wb, bound: float = float("inf")) -> float:
        """Max deviation at fixed parameters ``u`` (no inversion): the candidate cubic is
        sampled directly and compared with the probe table by linear interpolation, whose
        error ``h²/8·|f''|`` is negligible next to any tolerance. Stops early once
        ``bound`` is exceeded. The final verification is exact."""
        d = xb - xa
        px = (xa, xa + wa * d, xb - wb * d, xb)
        py = (ya, ya + sa * wa * d, yb - sb * wb * d, yb)
        xs, ys = self.src.xs, self.src.ys
        worst = 0.0
        for u in self.us:
            x = bezier.bernstein(px, u)
            i = bisect.bisect_right(xs, x) - 1
            if i >= len(xs) - 1:
                i = len(xs) - 2
            x0, x1 = xs[i], xs[i + 1]
            f = ys[i] + (ys[i + 1] - ys[i]) * ((x - x0) / (x1 - x0))
            e = abs(bezier.bernstein(py, u) - f)
            if e > worst:
                worst = e
                if worst > bound:
                    return worst
        return worst

    def endpoint(self, x: float) -> tuple[float, float, float]:
        """Cached ``(value, left slope, right slope)`` at a candidate time."""
        cached = self.cache.get(x)
        if cached is None:
            src = self.src
            cached = (src.value(x), src.slope_left(x), src.slope_right(x))
            self.cache[x] = cached
        return cached

    def fit(self, xa: float, xb: float) -> tuple[float, float, float] | None:
        """Return ``(wa, wb, max_error)`` if the span fits, else ``None``."""
        self.fits += 1
        if self.fits > MAX_SPAN_FITS:
            raise _Budget()
        ya, _, sa = self.endpoint(xa)
        yb, sb, _ = self.endpoint(xb)
        accept = SEARCH_MARGIN * self.tol
        best = (THIRD, THIRD)
        err = self._error(xa, ya, sa, THIRD, xb, yb, sb, THIRD)
        if err <= accept:
            return best[0], best[1], err
        if err > SEARCH_GIVE_UP * self.tol:
            return None
        step = SEARCH_STEP
        while step >= SEARCH_MIN_STEP:
            improved = False
            for da, db in ((step, 0.0), (-step, 0.0), (0.0, step), (0.0, -step)):
                cand = (min(max(best[0] + da, MIN_FIT_WEIGHT), MAX_FIT_WEIGHT),
                        min(max(best[1] + db, MIN_FIT_WEIGHT), MAX_FIT_WEIGHT))
                e = self._error(xa, ya, sa, cand[0], xb, yb, sb, cand[1], bound=err)
                if e < err:
                    best, err, improved = cand, e, True
            if err <= accept:
                return best[0], best[1], err
            if not improved:
                step *= 0.5
        return None


def _chunk_path(fitter: _SpanFitter, cands: list[float]):
    m = len(cands) - 1
    edges: dict[int, list[tuple[int, float, float]]] = {}
    for i in range(m):
        streak = 0
        for j in range(i + 1, m + 1):
            result = fitter.fit(cands[i], cands[j])
            if result is None:
                streak += 1
                if streak >= FAIL_STREAK:
                    break
                continue
            streak = 0
            edges.setdefault(j, []).append((i, result[0], result[1]))
    best: list[tuple[int, int, float, float] | None] = [None] * (m + 1)
    cost = [0] + [None] * m
    for j in range(1, m + 1):
        for i, wa, wb in edges.get(j, []):
            if cost[i] is not None and (cost[j] is None or cost[i] + 1 < cost[j]):
                cost[j] = cost[i] + 1
                best[j] = (i, j, wa, wb)
    if cost[m] is None:
        return None
    spans = []
    j = m
    while j > 0:
        i, _, wa, wb = best[j]
        spans.append((cands[i], cands[j], wa, wb))
        j = i
    return list(reversed(spans))


def _assemble(src: _Source, spans) -> Curve:
    xs = [spans[0][0]] + [b for _, b, _, _ in spans]
    knots = []
    last = len(xs) - 1
    for n, x in enumerate(xs):
        y = src.value(x)
        tin = Tangent(src.slope_left(x), spans[n - 1][3]) if n > 0 else None
        tout = Tangent(src.slope_right(x), spans[n][2]) if n < last else None
        smooth = tin is None or tout is None or tin.slope == tout.slope
        knots.append(Knot(x, y, tin, tout, smooth))
    return Curve(tuple(knots))


def max_error(src: _Source, curve: Curve) -> float:
    ys = sample_sorted(curve, src.xs)
    return max(abs(a - b) for a, b in zip(ys, src.ys, strict=True))


def tolerance_from_pct(curve: Curve, pct: float) -> float:
    lo, hi = value_bounds(curve)
    span = hi - lo
    return (pct / 100.0) * (span if span > 0 else 1.0)


def fit(curve: Curve, tolerance: float) -> FitResult:
    """Fit ``curve`` with as few knots as practical within ``tolerance`` (value units)."""
    if tolerance <= 0.0:
        raise ValueError("tolerance must be positive")
    n_in = len(curve.knots)
    src = _Source(curve)

    def unchanged(reason: str, err: float = 0.0) -> FitResult:
        return FitResult(curve, False, reason, err, tolerance, n_in, n_in)

    if n_in <= 2:
        return unchanged(NO_REDUCTION)
    marks = _landmarks(src, tolerance)
    fitter = _SpanFitter(src, tolerance)
    spans = []
    try:
        for a, b in pairwise(marks):
            path = _chunk_path(fitter, _candidates(src, a, b))
            if path is None:
                return unchanged(NO_SAFE_REDUCTION)
            spans.extend(path)
    except _Budget:
        return unchanged(FIT_BUDGET_EXCEEDED)
    result = _assemble(src, spans)
    err = max_error(src, result)
    if err > tolerance:
        return unchanged(NO_SAFE_REDUCTION, err)
    if len(result.knots) >= n_in:
        return unchanged(NO_REDUCTION, err)
    return FitResult(result, True, None, err, tolerance, n_in, len(result.knots))

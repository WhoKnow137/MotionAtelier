# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Sampling, edge handling, end preservation and PCHIP reconstruction.

PCHIP slopes follow Fritsch & Carlson (*SIAM J. Numer. Anal.* 17, 1980) with the weighted
harmonic mean of Fritsch & Butland at interior points and the shape-preserving three-point
formula at the ends. The end correction uses Perlin's smootherstep ``6t⁵ − 15t⁴ + 10t³``
(2002), whose first and second derivatives vanish at both ends.
"""

from __future__ import annotations

from collections.abc import Sequence

from motion_atelier.curve.evaluate import sample_sorted, uniform_xs
from motion_atelier.curve.model import Curve, Knot, Tangent
from motion_atelier.effects.model import EDGE_REFLECT

THIRD = 1.0 / 3.0

# Sample-count bounds for sampled effects. 257 resolves curves of a few dozen knots and
# smoothing radii down to ~1 % of the duration; 1025 keeps a full 16-layer stack within an
# interactive budget. Counts are odd so the centre sample sits at x = 0.5.
MIN_SAMPLES = 257
MAX_SAMPLES = 1025
SAMPLES_PER_KNOT = 8


def choose_samples(curve: Curve, needed: int = 0) -> int:
    n = max(MIN_SAMPLES, SAMPLES_PER_KNOT * len(curve.knots) + 1, int(needed))
    n = min(n, MAX_SAMPLES)
    return n if n % 2 == 1 else n + 1


def sample(curve: Curve, count: int) -> tuple[list[float], list[float]]:
    xs = uniform_xs(curve.x_start, curve.x_end, count)
    return xs, sample_sorted(curve, xs)


def edge_index(i: int, n: int, edge: str) -> int:
    """Map an out-of-range sample index into ``[0, n)`` by holding or reflecting."""
    if 0 <= i < n:
        return i
    if edge == EDGE_REFLECT and n > 1:
        period = 2 * (n - 1)
        i %= period
        return period - i if i >= n else i
    return 0 if i < 0 else n - 1


def smootherstep(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return t * t * t * (t * (6.0 * t - 15.0) + 10.0)


def preserve_ends(xs: Sequence[float], original: Sequence[float],
                  processed: Sequence[float]) -> list[float]:
    """Subtract ``d0·(1−w) + d1·w`` (``w`` = smootherstep of x) so both ends match exactly."""
    d0 = processed[0] - original[0]
    d1 = processed[-1] - original[-1]
    x0, x1 = xs[0], xs[-1]
    span = x1 - x0
    out = []
    for x, v in zip(xs, processed, strict=True):
        w = smootherstep((x - x0) / span)
        out.append(v - (d0 * (1.0 - w) + d1 * w))
    out[0] = original[0]
    out[-1] = original[-1]
    return out


def mix(original: Sequence[float], processed: Sequence[float], amount: float) -> list[float]:
    if amount >= 1.0:
        return list(processed)
    return [a + amount * (b - a) for a, b in zip(original, processed, strict=True)]


def _sign(v: float) -> int:
    return (v > 0.0) - (v < 0.0)


def pchip_slopes(xs: Sequence[float], ys: Sequence[float]) -> list[float]:
    n = len(xs)
    h = [xs[i + 1] - xs[i] for i in range(n - 1)]
    d = [(ys[i + 1] - ys[i]) / h[i] for i in range(n - 1)]
    if n == 2:
        return [d[0], d[0]]
    m = [0.0] * n
    for k in range(1, n - 1):
        a, b = d[k - 1], d[k]
        if a == 0.0 or b == 0.0 or _sign(a) != _sign(b):
            m[k] = 0.0
        else:
            w1 = 2.0 * h[k] + h[k - 1]
            w2 = h[k] + 2.0 * h[k - 1]
            m[k] = (w1 + w2) / (w1 / a + w2 / b)

    def end(h0: float, h1: float, d0: float, d1: float) -> float:
        value = ((2.0 * h0 + h1) * d0 - h0 * d1) / (h0 + h1)
        if _sign(value) != _sign(d0):
            return 0.0
        if _sign(d0) != _sign(d1) and abs(value) > abs(3.0 * d0):
            return 3.0 * d0
        return value

    m[0] = end(h[0], h[1], d[0], d[1])
    m[-1] = end(h[-1], h[-2], d[-1], d[-2])
    return m


def rebuild(xs: Sequence[float], ys: Sequence[float]) -> Curve:
    """A smooth curve through the samples with PCHIP slopes and one-third weights."""
    slopes = pchip_slopes(xs, ys)
    last = len(xs) - 1
    knots = []
    for i, (x, y, s) in enumerate(zip(xs, ys, slopes, strict=True)):
        t = Tangent(s, THIRD)
        knots.append(Knot(x, y, t if i else None, t if i < last else None, True))
    return Curve(tuple(knots))

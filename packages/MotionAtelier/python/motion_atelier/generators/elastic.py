# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Elastic: underdamped second-order step response (Ogata, *Modern Control Engineering*).

``x'' + 2ζω x' + ω² x = ω²`` released at rest from 0 has

    y(t) = 1 − e^(−ζωt)·(cos ω_d t + ζ/√(1−ζ²)·sin ω_d t),     ω_d = ω√(1−ζ²),
    y'(t) = (ω/√(1−ζ²))·e^(−ζωt)·sin ω_d t.

Extrema sit at ``t_k = kπ/ω_d`` with values ``1 + (−1)^(k+1)·rᵏ`` where
``r = exp(−πζ/√(1−ζ²))`` is the classical overshoot ratio. Shifting time by ``π/ω_d``
multiplies the deviation ``y − 1`` by ``−r``, so **every half swing is a scaled copy of the
first**; the normalised swing shape depends on ``ζ`` alone.

Sparse representation: knots at the start, at each of the ``n`` extrema (zero slope), and at
the end. Settle: after the last extremum the curve returns to the target in one more
half period, ending with zero velocity, so knots are uniformly spaced at ``k/(n+1)``. Each half
swing is one flat-tangent cubic whose two weights are fitted once per ``ζ`` (least squares over
fixed probes, deterministic compass search) to the analytic swing shape. Measured maximum error
over the parameter range: :data:`SWING_ERROR_BOUND` of each swing's amplitude.
"""

from __future__ import annotations

import functools
import math

from motion_atelier.curve import bezier
from motion_atelier.curve.model import Curve, Knot, Tangent
from motion_atelier.generators.base import GeneratorOutput, ShapeKind
from motion_atelier.generators.params import FLOAT, INT, Params, ParamSpec

VERSION = 1

# Upper damping bound. At ζ = 0.7 the overshoot is already only 4.6 %; beyond it the swing is
# so asymmetric that the best flat-tangent cubic misses the analytic swing by more than 1.2 %
# of its amplitude (2.3 % at 0.8), and it needs near-full-length handles that Houdini cannot
# store. Within [MIN_DAMPING, MAX_DAMPING] the measured bound is SWING_ERROR_BOUND.
MAX_DAMPING = 0.7
SWING_ERROR_BOUND = 0.012  # max |cubic − analytic| per half swing, as a fraction of its amplitude
MIN_DAMPING = 0.02  # r ≈ 0.94: nearly undamped yet still settling

# Fitted weights are kept inside [MIN_WEIGHT, MAX_WEIGHT]: strictly below 1 so the handle is
# representable in Houdini, strictly above 0 so the tangent direction stays defined.
MIN_SWING_WEIGHT = 0.02
MAX_SWING_WEIGHT = 0.95
SYMMETRIC_START_WEIGHT = 1.0 - 2.0 / math.pi  # matches a half-cosine's midpoint slope π/2
FIT_PROBES = 64  # interior probes j/64 for the least-squares fit
FIT_INITIAL_STEP = 0.05
FIT_MIN_STEP = 1e-4

PARAMS = (
    ParamSpec("oscillations", INT, 1, 12, 3, "param.elastic.oscillations", step=1, decimals=0),
    ParamSpec("damping", FLOAT, MIN_DAMPING, MAX_DAMPING, 0.25, "param.elastic.damping",
              step=0.01, decimals=3),
)


def overshoot_ratio(damping: float) -> float:
    return math.exp(-math.pi * damping / math.sqrt(1.0 - damping * damping))


def swing_shape(damping: float, s: float) -> float:
    """Normalised half-swing ``(y(sT) − y(0)) / (y(T) − y(0))`` with ``T = π/ω_d`` (ω = 1)."""
    wd = math.sqrt(1.0 - damping * damping)
    t = s * math.pi / wd
    amplitude = 1.0 + overshoot_ratio(damping)  # y(T) − y(0)
    y = 1.0 - math.exp(-damping * t) * (math.cos(wd * t) + damping / wd * math.sin(wd * t))
    return y / amplitude


def swing_error(damping: float, w_out: float, w_in: float, probes: int = FIT_PROBES) -> float:
    px = (0.0, w_out, 1.0 - w_in, 1.0)
    py = (0.0, 0.0, 1.0, 1.0)
    worst = 0.0
    for j in range(1, probes):
        s = j / probes
        u = bezier.solve_u(px, s)
        worst = max(worst, abs(bezier.bernstein(py, u) - swing_shape(damping, s)))
    return worst


def swing_squared_error(damping: float, w_out: float, w_in: float) -> float:
    px = (0.0, w_out, 1.0 - w_in, 1.0)
    py = (0.0, 0.0, 1.0, 1.0)
    total = 0.0
    for j in range(1, FIT_PROBES):
        s = j / FIT_PROBES
        d = bezier.bernstein(py, bezier.solve_u(px, s)) - swing_shape(damping, s)
        total += d * d
    return total


@functools.lru_cache(maxsize=512)
def swing_weights(damping: float) -> tuple[float, float]:
    """Deterministic least-squares fit of ``(w_out, w_in)`` for one half swing (cached).

    Compass search from the symmetric half-cosine weight, halving its step down to
    :data:`FIT_MIN_STEP`. Least squares is used rather than minimax because its landscape is
    smooth in ``(w_out, w_in, ζ)``: neighbouring damping values get neighbouring weights and
    no seed-dependent local minima (measured; minimax search jumped between minima). The
    resulting maximum error is pinned by :data:`SWING_ERROR_BOUND`.
    """
    best = (SYMMETRIC_START_WEIGHT, SYMMETRIC_START_WEIGHT)
    best_err = swing_squared_error(damping, *best)
    step = FIT_INITIAL_STEP
    while step >= FIT_MIN_STEP:
        improved = False
        for da, db in ((step, 0.0), (-step, 0.0), (0.0, step), (0.0, -step)):
            cand = (
                min(max(best[0] + da, MIN_SWING_WEIGHT), MAX_SWING_WEIGHT),
                min(max(best[1] + db, MIN_SWING_WEIGHT), MAX_SWING_WEIGHT),
            )
            err = swing_squared_error(damping, *cand)
            if err < best_err:
                best, best_err, improved = cand, err, True
        if not improved:
            step *= 0.5
    return best


class Elastic:
    kind = ShapeKind.ELASTIC
    version = VERSION
    params_schema = PARAMS

    def build(self, params: Params) -> GeneratorOutput:
        values = dict(params)
        n = int(values["oscillations"])
        zeta = float(values["damping"])
        r = overshoot_ratio(zeta)
        w_out, w_in = swing_weights(zeta)
        segments = n + 1
        ys = [0.0] + [1.0 + (-1.0) ** (k + 1) * r**k for k in range(1, n + 1)] + [1.0]
        knots = []
        for k, y in enumerate(ys):
            x = 0.0 if k == 0 else 1.0 if k == segments else k / segments
            tin = Tangent(0.0, w_in) if k > 0 else None
            tout = Tangent(0.0, w_out) if k < segments else None
            knots.append(Knot(x, y, tin, tout, True))
        return GeneratorOutput(Curve(tuple(knots)))


GENERATOR = Elastic()

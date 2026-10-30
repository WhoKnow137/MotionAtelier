# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Wave: straight progression plus an enveloped sinusoid.

    y(x)  = x + A·env(x)·sin θ(x),        θ = 2π·c·x + φ
    env(x) = 4x(1 − x)·e^(−λx)           (zero at both ends; λ = decay)
    y'(x) = 1 + A·(env'(x)·sin θ + env(x)·2πc·cos θ)
    env'(x) = e^(−λx)·(4(1 − 2x) − 4λx(1 − x))

Keying (deterministic): knots at both ends and at every extremum of the deviation
``A·env·sin θ`` (roots of its derivative, bracketed on a fixed grid and bisected to full
precision), each with its exact value and slope, as cubic Hermite segments (one-third
weights). The worst segment, measured at fixed interior probes against the analytic signal,
is split at its midpoint until the maximum error is within :data:`MAX_ERROR` or the knot cap
is reached; hitting the cap returns the best curve with a ``wave_knot_cap`` notice.
"""

from __future__ import annotations

import math

from motion_atelier.curve import bezier
from motion_atelier.curve.errors import Notice
from motion_atelier.curve.model import MIN_KNOT_GAP, Curve, Knot, Tangent
from motion_atelier.generators.base import GeneratorOutput, ShapeKind
from motion_atelier.generators.params import FLOAT, Params, ParamSpec

VERSION = 1
THIRD = 1.0 / 3.0
TAU = 2.0 * math.pi

# Maximum deviation from the analytic wave, as a fraction of the unit progression. 5e-4 is
# 0.05 % of the motion (0.05 unit on a 100-unit channel): invisible, yet reached with a
# handful of keys per half cycle.
MAX_ERROR = 5e-4
# Hard cap on knots. 24 cycles need ~4 keys per half cycle at most amplitudes (≈ 200).
KNOT_CAP = 240
SCAN_PER_CYCLE = 48  # bracketing grid density for the deviation's critical points
SCAN_MIN = 256
PROBES_PER_SEGMENT = 12  # error probes at j/13, j = 1..12, inside every segment
BISECT_ITERATIONS = 80  # more than enough to reach double precision on [0, 1]

KNOT_CAP_HIT = "wave_knot_cap"

PARAMS = (
    ParamSpec("cycles", FLOAT, 0.5, 24.0, 4.0, "param.wave.cycles", step=0.25, decimals=2,
              soft_max=12.0),
    ParamSpec("amplitude", FLOAT, -1.0, 1.0, 0.15, "param.wave.amplitude", step=0.01,
              decimals=3, soft_min=0.0, soft_max=0.5),
    ParamSpec("phase", FLOAT, -180.0, 180.0, 0.0, "param.wave.phase", step=1.0, decimals=1,
              unit="°"),
    ParamSpec("decay", FLOAT, 0.0, 8.0, 0.0, "param.wave.decay", step=0.1, decimals=2),
)


class _Signal:
    def __init__(self, cycles: float, amplitude: float, phase_deg: float, decay: float):
        self.c = cycles
        self.a = amplitude
        self.phi = math.radians(phase_deg)
        self.lam = decay

    def env(self, x: float) -> float:
        return 4.0 * x * (1.0 - x) * math.exp(-self.lam * x)

    def env_d(self, x: float) -> float:
        return math.exp(-self.lam * x) * (4.0 * (1.0 - 2.0 * x) - 4.0 * self.lam * x * (1.0 - x))

    def value(self, x: float) -> float:
        return x + self.a * self.env(x) * math.sin(TAU * self.c * x + self.phi)

    def deviation_slope(self, x: float) -> float:
        theta = TAU * self.c * x + self.phi
        return self.a * (self.env_d(x) * math.sin(theta)
                         + self.env(x) * TAU * self.c * math.cos(theta))

    def slope(self, x: float) -> float:
        return 1.0 + self.deviation_slope(x)


def _critical_points(sig: _Signal) -> list[float]:
    if sig.a == 0.0:
        return []  # no deviation, no extrema
    count = max(SCAN_MIN, int(math.ceil(sig.c * SCAN_PER_CYCLE)))
    xs = [i / count for i in range(count + 1)]
    gs = [sig.deviation_slope(x) for x in xs]
    roots = []
    for i in range(count):
        a, b = xs[i], xs[i + 1]
        ga, gb = gs[i], gs[i + 1]
        if ga == 0.0:
            # A zero exactly on the grid is an extremum only if the slope changes sign there.
            if i > 0 and gs[i - 1] * gb < 0.0:
                roots.append(a)
            continue
        if ga * gb >= 0.0:
            continue
        for _ in range(BISECT_ITERATIONS):
            m = 0.5 * (a + b)
            if m <= a or m >= b:
                break
            gm = sig.deviation_slope(m)
            if (gm < 0.0) == (ga < 0.0):
                a, ga = m, gm
            else:
                b = m
        roots.append(0.5 * (a + b))
    min_gap = 10.0 * MIN_KNOT_GAP
    out: list[float] = []
    for r in roots:
        if r <= min_gap or r >= 1.0 - min_gap:
            continue
        if out and r - out[-1] < min_gap:
            continue
        out.append(r)
    return out


def _knot(sig: _Signal, x: float, first: bool, last: bool) -> Knot:
    y = 0.0 if first else 1.0 if last else sig.value(x)
    s = sig.slope(x)
    t = Tangent(s, THIRD)
    return Knot(x, y, None if first else t, None if last else t, True)


def _segment_error(sig: _Signal, a: Knot, b: Knot) -> float:
    d = b.x - a.x
    px = (a.x, a.x + d * THIRD, b.x - d * THIRD, b.x)  # Hermite: Bx is linear in u
    assert a.tout is not None and b.tin is not None
    py = (a.y, a.y + a.tout.slope * d * THIRD, b.y - b.tin.slope * d * THIRD, b.y)
    worst = 0.0
    for j in range(1, PROBES_PER_SEGMENT + 1):
        u = j / (PROBES_PER_SEGMENT + 1)
        x = bezier.bernstein(px, u)
        worst = max(worst, abs(bezier.bernstein(py, u) - sig.value(x)))
    return worst


def key_wave(sig: _Signal) -> tuple[Curve, float, tuple[Notice, ...]]:
    """Return ``(curve, measured_max_error, notices)``."""
    xs = [0.0, *_critical_points(sig), 1.0]
    knots = [_knot(sig, x, i == 0, i == len(xs) - 1) for i, x in enumerate(xs)]
    errors = [_segment_error(sig, knots[i], knots[i + 1]) for i in range(len(knots) - 1)]
    notices: tuple[Notice, ...] = ()
    min_gap = 10.0 * MIN_KNOT_GAP
    while True:
        worst = max(errors)
        if worst <= MAX_ERROR:
            break
        i = errors.index(worst)  # lowest index wins ties: deterministic
        a, b = knots[i], knots[i + 1]
        mid = 0.5 * (a.x + b.x)
        if len(knots) >= KNOT_CAP or b.x - a.x < 2.0 * min_gap:
            notices = (Notice(KNOT_CAP_HIT, f"max error {worst:.3g} with {len(knots)} keys"),)
            break
        new = _knot(sig, mid, False, False)
        knots.insert(i + 1, new)
        errors[i : i + 1] = [_segment_error(sig, a, new), _segment_error(sig, new, b)]
    return Curve(tuple(knots)), max(errors), notices


class Wave:
    kind = ShapeKind.WAVE
    version = VERSION
    params_schema = PARAMS

    def build(self, params: Params) -> GeneratorOutput:
        v = dict(params)
        sig = _Signal(float(v["cycles"]), float(v["amplitude"]), float(v["phase"]),
                      float(v["decay"]))
        curve, _, notices = key_wave(sig)
        return GeneratorOutput(curve, notices)


def signal(params: Params) -> _Signal:
    v = dict(params)
    return _Signal(float(v["cycles"]), float(v["amplitude"]), float(v["phase"]), float(v["decay"]))


GENERATOR = Wave()

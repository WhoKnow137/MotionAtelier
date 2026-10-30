# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Noise: deterministic value noise on a uniform lattice, tapered to exact endpoints.

Lattice ``x_i = i/F`` for ``i = 0..F``; values ``y_i = x_i + A·sin(π x_i)·h(seed, i)`` with
``h ∈ [−1, 1]`` the integer hash of :mod:`motion_atelier.generators.hashing`; the taper
``sin(πx)`` is zero at both ends, and the endpoints are set to exactly 0 and 1.

Tangents (weights 1/3): with secants ``δ_i`` and the centred slope ``c_i`` (mean of the
adjacent secants; the single secant at an end), the incoming and outgoing slopes are
``(1 − s)·δ_{i−1} + s·c_i`` and ``(1 − s)·δ_i + s·c_i``. Smoothness ``s = 0`` gives straight
segments with broken knots; ``s = 1`` gives smooth knots with centred slopes.
"""

from __future__ import annotations

import math

from motion_atelier.curve.model import Curve, Knot, Tangent
from motion_atelier.generators.base import GeneratorOutput, ShapeKind
from motion_atelier.generators.hashing import lattice_unit
from motion_atelier.generators.params import FLOAT, INT, Params, ParamSpec

VERSION = 1
THIRD = 1.0 / 3.0
MAX_SEED = 2**31 - 1

PARAMS = (
    ParamSpec("frequency", INT, 1, 64, 8, "param.noise.frequency", step=1, decimals=0,
              soft_max=32),
    ParamSpec("amplitude", FLOAT, 0.0, 1.0, 0.2, "param.noise.amplitude", step=0.01,
              decimals=3, soft_max=0.5),
    ParamSpec("seed", INT, 0, MAX_SEED, 1, "param.noise.seed", step=1, decimals=0,
              soft_max=999),
    ParamSpec("smoothness", FLOAT, 0.0, 1.0, 0.75, "param.noise.smoothness", step=0.01,
              decimals=3),
)


def lattice_values(frequency: int, amplitude: float, seed: int) -> list[float]:
    ys = []
    for i in range(frequency + 1):
        if i == 0:
            ys.append(0.0)
        elif i == frequency:
            ys.append(1.0)
        else:
            x = i / frequency
            ys.append(x + amplitude * math.sin(math.pi * x) * lattice_unit(seed, i))
    return ys


class Noise:
    kind = ShapeKind.NOISE
    version = VERSION
    params_schema = PARAMS

    def build(self, params: Params) -> GeneratorOutput:
        v = dict(params)
        f = int(v["frequency"])
        a = float(v["amplitude"])
        seed = int(v["seed"])
        s = float(v["smoothness"])
        ys = lattice_values(f, a, seed)
        secants = [(ys[i + 1] - ys[i]) * f for i in range(f)]
        knots = []
        for i in range(f + 1):
            x = 0.0 if i == 0 else 1.0 if i == f else i / f
            left = secants[i - 1] if i > 0 else None
            right = secants[i] if i < f else None
            if left is None:
                centred = right
            elif right is None:
                centred = left
            else:
                centred = 0.5 * (left + right)
            tin = Tangent((1.0 - s) * left + s * centred, THIRD) if left is not None else None
            tout = Tangent((1.0 - s) * right + s * centred, THIRD) if right is not None else None
            smooth = tin is None or tout is None or tin.slope == tout.slope
            knots.append(Knot(x, ys[i], tin, tout, smooth))
        return GeneratorOutput(Curve(tuple(knots)))


GENERATOR = Noise()

# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""How model parameters are shown.

The generator and effect parameters keep their qualified names, ranges and defaults (they
are stored in recipes). The interface shows each one with its display label and
units: ``shown = model × scale``. Labels and tooltips are catalogue entries
(``<spec.label>`` and ``<spec.label>.tip``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from motion_atelier.generators.params import INT, ParamSpec

# 90 % of a one-pole step response is reached after τ·ln 10.
LN10 = math.log(10.0)


@dataclass(frozen=True)
class Shown:
    scale: float = 1.0
    suffix: str = ""
    decimals: int | None = None  # None: the spec's own decimals
    soft_max: float | None = None  # in model units; None: the spec's own soft maximum


GENERATORS: dict[tuple[str, str], Shown] = {
    ("bounce", "bounces"): Shown(decimals=0),
    ("bounce", "restitution"): Shown(decimals=2),
    ("elastic", "oscillations"): Shown(decimals=0),
    ("elastic", "damping"): Shown(decimals=2, soft_max=0.6),
    ("wave", "cycles"): Shown(decimals=2, soft_max=10.0),
    ("wave", "amplitude"): Shown(decimals=2),
    ("wave", "phase"): Shown(decimals=2),
    ("wave", "decay"): Shown(decimals=2, soft_max=5.0),
    ("noise", "frequency"): Shown(decimals=0, soft_max=32),
    ("noise", "amplitude"): Shown(decimals=2),
    ("noise", "seed"): Shown(decimals=0, soft_max=100),
    ("noise", "smoothness"): Shown(decimals=2),
}

EFFECTS: dict[tuple[str, str], Shown] = {
    ("contrast", "amount"): Shown(100.0, "%", 0),
    ("timing_bias", "bias"): Shown(100.0, "%", 0),
    ("noise", "amplitude"): Shown(1.0, "", 3),
    ("noise", "frequency"): Shown(1.0, "", 0),
    ("noise", "seed"): Shown(1.0, "", 0),
    ("gaussian", "radius"): Shown(100.0, "%", 1),
    ("gaussian", "passes"): Shown(1.0, "", 0),
    ("sharpen", "amount"): Shown(100.0, "%", 1),
    ("sharpen", "radius"): Shown(100.0, "%", 1),
    ("spring", "frequency"): Shown(1.0, "", 1),
    ("spring", "damping"): Shown(1.0, "", 2),
    ("lag", "time"): Shown(100.0 * LN10, "%", 1),
    ("delay", "time"): Shown(100.0, "%", 1),
    ("jiggle", "frequency"): Shown(1.0, "", 1),
    ("jiggle", "damping"): Shown(1.0, "", 2),
    ("jiggle", "limit"): Shown(1.0, "", 2),
}

# Add Effect menu order.
EFFECT_MENU_ORDER = ("contrast", "timing_bias", "noise", "gaussian", "sharpen", "jiggle",
                     "spring", "lag", "delay")


@dataclass(frozen=True)
class FieldFormat:
    lo: float
    hi: float
    soft_hi: float
    decimals: int
    step: float
    scale: float
    suffix: str
    integer: bool


def field_format(spec: ParamSpec, shown: Shown | None) -> FieldFormat:
    shown = shown or Shown()
    integer = spec.kind == INT
    decimals = 0 if integer else (shown.decimals if shown.decimals is not None
                                  else spec.decimals)
    soft = shown.soft_max if shown.soft_max is not None else (
        spec.soft_max if spec.soft_max is not None else spec.maximum)
    soft = min(spec.maximum, soft)
    step = 1.0 if integer else (10.0 ** -decimals if decimals else 1.0)
    return FieldFormat(spec.minimum * shown.scale, spec.maximum * shown.scale,
                       soft * shown.scale, decimals, step, shown.scale, shown.suffix, integer)


def to_model(fmt: FieldFormat, shown_value: float, spec: ParamSpec) -> float | int:
    value = shown_value / fmt.scale if fmt.scale else shown_value
    value = min(spec.maximum, max(spec.minimum, value))
    return int(round(value)) if fmt.integer else value

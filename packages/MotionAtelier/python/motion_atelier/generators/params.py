# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Parameter specifications: one source of truth for validation, UI controls, and records."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from motion_atelier.curve.errors import MotionAtelierError

FLOAT = "float"
INT = "int"

# Stable error codes.
UNKNOWN_PARAM = "unknown_param"
MISSING_PARAM = "missing_param"
BAD_PARAM_TYPE = "bad_param_type"
PARAM_OUT_OF_RANGE = "param_out_of_range"


class ParamError(MotionAtelierError):
    """Invalid parameter set for a generator or effect."""


@dataclass(frozen=True, slots=True)
class ParamSpec:
    """A named numeric parameter with a hard range and a default.

    ``soft_min``/``soft_max`` bound the UI slider; the numeric field accepts the full hard
    range. ``label`` is a string-catalogue id.
    """

    name: str
    kind: str
    minimum: float
    maximum: float
    default: float
    label: str
    step: float = 0.01
    decimals: int = 3
    soft_min: float | None = None
    soft_max: float | None = None
    unit: str = ""

    def __post_init__(self) -> None:
        if self.kind not in (FLOAT, INT):
            raise ValueError(f"bad kind {self.kind!r}")
        if not self.minimum <= self.default <= self.maximum:
            raise ValueError(f"default of {self.name} outside its range")
        if self.kind == INT and not all(
            float(v).is_integer() for v in (self.minimum, self.maximum, self.default)
        ):
            raise ValueError(f"integer parameter {self.name} has non-integer bounds")

    @property
    def slider_min(self) -> float:
        return self.minimum if self.soft_min is None else self.soft_min

    @property
    def slider_max(self) -> float:
        return self.maximum if self.soft_max is None else self.soft_max

    def coerce(self, value: Any) -> float | int:
        """Validate one value; returns ``int`` for integer specs, ``float`` otherwise."""
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ParamError(BAD_PARAM_TYPE, f"{self.name} must be a number")
        if not math.isfinite(value):
            raise ParamError(BAD_PARAM_TYPE, f"{self.name} must be finite")
        if self.kind == INT:
            if not float(value).is_integer():
                raise ParamError(BAD_PARAM_TYPE, f"{self.name} must be an integer")
            value = int(value)
        else:
            value = float(value) + 0.0
        if not self.minimum <= value <= self.maximum:
            raise ParamError(
                PARAM_OUT_OF_RANGE,
                f"{self.name}={value!r} outside [{self.minimum!r}, {self.maximum!r}]",
            )
        return value


Params = tuple[tuple[str, float | int], ...]


def validate_params(specs: Iterable[ParamSpec], values: Mapping[str, Any]) -> Params:
    """Exact-key validation. Returns an immutable, spec-ordered parameter tuple."""
    specs = tuple(specs)
    names = {s.name for s in specs}
    unknown = sorted(set(values) - names)
    if unknown:
        raise ParamError(UNKNOWN_PARAM, f"unknown parameters {unknown}")
    missing = [s.name for s in specs if s.name not in values]
    if missing:
        raise ParamError(MISSING_PARAM, f"missing parameters {missing}")
    return tuple((s.name, s.coerce(values[s.name])) for s in specs)


def defaults(specs: Iterable[ParamSpec]) -> Params:
    return tuple(
        (s.name, int(s.default) if s.kind == INT else float(s.default)) for s in specs
    )


def with_values(specs: Iterable[ParamSpec], base: Params, **changes: Any) -> Params:
    merged = dict(base)
    merged.update(changes)
    return validate_params(specs, merged)


def as_dict(params: Params) -> dict[str, float | int]:
    return dict(params)

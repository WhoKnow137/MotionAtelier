# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Shape kinds, the generator protocol, and versioned generator records."""

from __future__ import annotations

import enum
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from motion_atelier.curve.errors import MotionAtelierError, Notice
from motion_atelier.curve.model import Curve
from motion_atelier.generators.params import Params, ParamSpec, validate_params

# Stable error codes.
UNKNOWN_GENERATOR = "unknown_generator"
UNSUPPORTED_VERSION = "unsupported_generator_version"


class GeneratorError(MotionAtelierError):
    pass


class ShapeKind(enum.Enum):
    EASE = "ease"
    BOUNCE = "bounce"
    ELASTIC = "elastic"
    WAVE = "wave"
    NOISE = "noise"

    @classmethod
    def parse(cls, value: str) -> ShapeKind:
        try:
            return cls(value)
        except ValueError:
            raise GeneratorError(UNKNOWN_GENERATOR, f"unknown shape {value!r}") from None


@dataclass(frozen=True, slots=True)
class GeneratorOutput:
    curve: Curve
    notices: tuple[Notice, ...] = ()


class Generator(Protocol):
    kind: ShapeKind
    version: int
    params_schema: tuple[ParamSpec, ...]

    def build(self, params: Params) -> GeneratorOutput: ...


@dataclass(frozen=True, slots=True)
class GeneratorRecord:
    """``(kind, version, params)``: everything needed to regenerate a curve identically."""

    kind: ShapeKind
    version: int
    params: Params

    def to_data(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "version": self.version, "params": dict(self.params)}


def make_record(generator: Generator, values: Mapping[str, Any]) -> GeneratorRecord:
    return GeneratorRecord(
        generator.kind, generator.version, validate_params(generator.params_schema, values)
    )

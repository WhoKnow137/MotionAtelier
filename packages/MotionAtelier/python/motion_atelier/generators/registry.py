# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Generator registry and versioned record handling."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from motion_atelier.generators import bounce, elastic, noise, wave
from motion_atelier.generators.base import (
    UNKNOWN_GENERATOR,
    UNSUPPORTED_VERSION,
    Generator,
    GeneratorError,
    GeneratorOutput,
    GeneratorRecord,
    ShapeKind,
)
from motion_atelier.generators.params import defaults, validate_params

GENERATORS: dict[ShapeKind, Generator] = {
    ShapeKind.BOUNCE: bounce.GENERATOR,
    ShapeKind.ELASTIC: elastic.GENERATOR,
    ShapeKind.WAVE: wave.GENERATOR,
    ShapeKind.NOISE: noise.GENERATOR,
}
GENERATOR_KINDS: tuple[ShapeKind, ...] = (
    ShapeKind.BOUNCE, ShapeKind.ELASTIC, ShapeKind.WAVE, ShapeKind.NOISE,
)


def generator_for(kind: ShapeKind) -> Generator:
    try:
        return GENERATORS[kind]
    except KeyError:
        raise GeneratorError(UNKNOWN_GENERATOR, f"{kind.value} is not a generator") from None


def default_record(kind: ShapeKind) -> GeneratorRecord:
    gen = generator_for(kind)
    return GeneratorRecord(kind, gen.version, defaults(gen.params_schema))


def build(record: GeneratorRecord) -> GeneratorOutput:
    """Regenerate a curve from a record. Unknown or future versions are rejected."""
    gen = generator_for(record.kind)
    if record.version != gen.version:
        raise GeneratorError(
            UNSUPPORTED_VERSION,
            f"{record.kind.value} version {record.version} (this build supports {gen.version})",
        )
    return gen.build(validate_params(gen.params_schema, dict(record.params)))


def record_from_data(data: Any) -> GeneratorRecord:
    if not isinstance(data, dict) or set(data) != {"kind", "version", "params"}:
        raise GeneratorError(UNKNOWN_GENERATOR, "generator record needs kind, version, params")
    kind = ShapeKind.parse(data["kind"]) if isinstance(data["kind"], str) else None
    if kind is None or kind not in GENERATORS:
        raise GeneratorError(UNKNOWN_GENERATOR, f"unknown generator {data['kind']!r}")
    version = data["version"]
    gen = GENERATORS[kind]
    if isinstance(version, bool) or not isinstance(version, int) or version != gen.version:
        raise GeneratorError(UNSUPPORTED_VERSION, f"{kind.value} version {version!r}")
    params = data["params"]
    if not isinstance(params, Mapping):
        raise GeneratorError(UNKNOWN_GENERATOR, "params must be an object")
    return GeneratorRecord(kind, version, validate_params(gen.params_schema, params))

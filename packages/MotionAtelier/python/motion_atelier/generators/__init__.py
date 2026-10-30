# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Procedural shape generators: Bounce, Elastic, Wave, Noise (standard library + curve)."""

from motion_atelier.generators.base import (
    GeneratorError,
    GeneratorOutput,
    GeneratorRecord,
    ShapeKind,
)
from motion_atelier.generators.params import ParamError, ParamSpec

__all__ = [
    "GeneratorError",
    "GeneratorOutput",
    "GeneratorRecord",
    "ParamError",
    "ParamSpec",
    "ShapeKind",
]

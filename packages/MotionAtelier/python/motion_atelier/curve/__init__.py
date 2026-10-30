# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Curve kernel: model, Bezier evaluation, operations, serialisation (standard library only)."""

from motion_atelier.curve.errors import CurveError, MotionAtelierError, Notice
from motion_atelier.curve.evaluate import (
    sample,
    sample_sorted,
    segment_index,
    value_at,
    value_bounds,
    velocity_at,
)
from motion_atelier.curve.model import Curve, Knot, Tangent, linear_curve, make_knot
from motion_atelier.curve.serialize import curve_digest, curve_from_data, curve_to_data

__all__ = [
    "Curve",
    "CurveError",
    "Knot",
    "MotionAtelierError",
    "Notice",
    "Tangent",
    "curve_digest",
    "curve_from_data",
    "curve_to_data",
    "linear_curve",
    "make_knot",
    "sample",
    "sample_sorted",
    "segment_index",
    "value_at",
    "value_bounds",
    "velocity_at",
]

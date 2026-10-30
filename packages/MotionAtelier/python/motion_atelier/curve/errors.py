# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Typed errors and warnings with stable codes.

Every pure layer raises a subclass of :class:`MotionAtelierError`. Callers and tests switch on
``code`` (a stable identifier); ``detail`` is human-readable context and may change.
"""

from __future__ import annotations

from dataclasses import dataclass


class MotionAtelierError(Exception):
    """Base class for all expected, typed failures in MotionAtelier."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


class CurveError(MotionAtelierError):
    """Invalid curve data or an impossible curve operation."""


@dataclass(frozen=True, slots=True)
class Notice:
    """A non-fatal diagnostic: something happened that the user should know about."""

    code: str
    detail: str = ""


# Stable curve error codes (tests assert on these).
NON_FINITE = "non_finite"
NOT_INCREASING = "not_increasing"
TOO_FEW_KNOTS = "too_few_knots"
WEIGHT_OUT_OF_RANGE = "weight_out_of_range"
ENDPOINT_TANGENT = "endpoint_tangent"
MISSING_TANGENT = "missing_tangent"
SMOOTH_SLOPE_MISMATCH = "smooth_slope_mismatch"
SOLVE_FAILED = "solve_failed"
INDEX_OUT_OF_RANGE = "index_out_of_range"
ENDPOINT_FIXED = "endpoint_fixed"
KNOT_OUT_OF_ORDER = "knot_out_of_order"
INSERT_TOO_CLOSE = "insert_too_close"
INSERT_OUTSIDE = "insert_outside"
NO_SUCH_TANGENT = "no_such_tangent"
BAD_CURVE_DATA = "bad_curve_data"
BAD_TYPE = "bad_type"

# Stable warning codes.
WEIGHT_CLAMPED = "weight_clamped"
ENDPOINT_NOT_DELETED = "endpoint_not_deleted"
NOTHING_SELECTED = "nothing_selected"
MOVE_CLAMPED = "move_clamped"

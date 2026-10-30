# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Conversion between physical handles and Houdini's Bezier slope/acceleration.

Measured in Houdini: a Bezier half with slope ``s`` (value
units per second) and acceleration ``a`` places its control point at ``dt = a/√(1 + s²)``
seconds from the key, ``dv = s·dt``; i.e. the acceleration is the handle's Euclidean length
in (seconds, value) units. This holds in ratio mode too.
"""

from __future__ import annotations

import math

from motion_atelier.curve.errors import MotionAtelierError

VERTICAL_HANDLE = "vertical_handle"


class TangentError(MotionAtelierError):
    pass


def handle_from_houdini(slope: float, accel: float) -> tuple[float, float]:
    """``(dt, dv)`` of a half with the given slope and acceleration (``dt ≥ 0``)."""
    dt = abs(accel) / math.sqrt(1.0 + slope * slope)
    return dt, slope * dt


def houdini_from_handle(dt: float, dv: float, slope_if_zero: float = 0.0) -> tuple[float, float]:
    """Slope and acceleration for a physical handle ``(dt ≥ 0, dv)``.

    A zero-length handle keeps ``slope_if_zero`` (it does not affect evaluation). A vertical
    handle (``dt = 0`` with ``dv ≠ 0``) cannot be stored and raises ``vertical_handle``.
    """
    if dt < 0.0:
        raise ValueError("dt must be non-negative")
    if dt == 0.0:
        if dv != 0.0:
            raise TangentError(VERTICAL_HANDLE, "a vertical handle cannot be stored in Houdini")
        return slope_if_zero, 0.0
    slope = dv / dt
    return slope, math.hypot(dt, dv)

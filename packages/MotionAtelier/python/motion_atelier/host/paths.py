# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Where MotionAtelier keeps user data: a folder inside Houdini's user preference directory."""

from __future__ import annotations

from pathlib import Path

import hou

DATA_FOLDER = "MotionAtelier"


def user_data_dir() -> Path:
    """``$HOUDINI_USER_PREF_DIR/MotionAtelier`` (``hou.homeHoudiniDirectory()`` honours the
    variable)."""
    return Path(hou.homeHoudiniDirectory()) / DATA_FOLDER

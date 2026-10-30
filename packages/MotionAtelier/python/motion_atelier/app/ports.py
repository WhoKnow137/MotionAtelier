# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""The interface the application layer needs from a scene host (Houdini or a test fake)."""

from __future__ import annotations

from typing import Protocol

from motion_atelier.app.snapshots import SelectionSnapshot

# Read purposes: Get Keys and Commit read key selections only; tools may fall back to whole
# channels selected in the Channel List.
PURPOSE_GET_KEYS = "get_keys"
PURPOSE_COMMIT = "commit"
PURPOSE_TOOL = "tool"


class HostPort(Protocol):
    def read_selection(self, purpose: str) -> SelectionSnapshot: ...

    def execute(self, plans, label: str):
        """Apply a sequence of ``ChannelPlan`` as one undoable transaction; returns an
        ``ExecutionReport``."""
        ...

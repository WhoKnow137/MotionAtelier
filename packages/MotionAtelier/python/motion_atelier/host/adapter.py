# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""The Houdini implementation of :class:`motion_atelier.app.ports.HostPort`."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import hou

from motion_atelier.app.results import ExecutionReport
from motion_atelier.app.snapshots import SelectionSnapshot
from motion_atelier.host import transaction
from motion_atelier.host.selection import snapshot_selection


class HoudiniHost:
    """Reads selections from Houdini and executes validated plans in undoable transactions."""

    def __init__(self, fault: transaction.Fault | None = None) -> None:
        self._explicit: Mapping[str, Sequence[float] | None] | None = None
        # Test-only failure injection: ``fault(stage, channel_path)`` may raise (see
        # tests/houdini/test_commit.py). Never set by the panel.
        self.fault = fault

    def use_explicit_selection(self, selection: Mapping[str, Sequence[float] | None] | None
                               ) -> None:
        """Make subsequent reads return ``selection`` (path → times) instead of the UI's."""
        self._explicit = dict(selection) if selection is not None else None

    def read_selection(self, purpose: str) -> SelectionSnapshot:
        return snapshot_selection(purpose, self._explicit)

    def fps(self) -> float:
        return float(hou.fps())

    def playhead_time(self) -> float:
        return float(hou.time())

    def execute(self, plans, label: str) -> ExecutionReport:
        """Run channel plans as one verified, undoable transaction (see host.transaction)."""
        return transaction.execute(tuple(plans), label, self.fault)

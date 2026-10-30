# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Persistent services shared by every panel of one Houdini session (per data directory)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from motion_atelier.app.history import HistoryService
from motion_atelier.app.library import LibraryService
from motion_atelier.app.preferences import Preferences
from motion_atelier.store.atomic import StoreWarning


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Services:
    data_dir: Path | None
    library: LibraryService
    preferences: Preferences
    history: HistoryService
    clock: Callable[[], datetime]

    @property
    def warnings(self) -> tuple[StoreWarning, ...]:
        return (*self.library.warnings, *self.preferences.warnings, *self.history.warnings)


def create_services(data_dir: Path | None, clock: Callable[[], datetime] = utc_now) -> Services:
    """Load (or create) the user's library and settings. ``None`` keeps everything in memory."""
    if data_dir is None:
        return Services(None, LibraryService(None), Preferences(None),
                        HistoryService(None, clock), clock)
    data_dir = Path(data_dir)
    return Services(
        data_dir,
        LibraryService(LibraryService.make_store(data_dir, clock)),
        Preferences(Preferences.make_store(data_dir, clock)),
        HistoryService(HistoryService.make_store(data_dir, clock), clock),
        clock,
    )

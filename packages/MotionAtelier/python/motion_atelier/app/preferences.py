# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Settings remembered between sessions, with deferred (debounced) writes."""

from __future__ import annotations

from pathlib import Path

from motion_atelier.records.settings import Settings, settings_from_data
from motion_atelier.store.atomic import JsonStore, StoreWarning

SETTINGS_FILE = "settings.json"


class Preferences:
    """Holds the current :class:`Settings`; ``update`` marks dirty, ``flush`` writes once."""

    def __init__(self, store: JsonStore | None) -> None:
        self.store = store
        self.warnings: tuple[StoreWarning, ...] = ()
        if store is None:
            self.settings = Settings()
        else:
            loaded = store.load()
            self.settings = loaded.value
            self.warnings = loaded.warnings
        self.dirty = False

    @staticmethod
    def make_store(directory: Path, clock) -> JsonStore:
        return JsonStore(Path(directory) / SETTINGS_FILE, settings_from_data,
                         lambda s: s.to_data(), Settings, clock)

    def update(self, **changes) -> None:
        new = self.settings.with_(**changes)
        if new != self.settings:
            self.settings = new
            self.dirty = True

    def flush(self) -> bool:
        """Write if anything changed. Returns True if a write happened."""
        if not self.dirty or self.store is None:
            return False
        self.store.save(self.settings)
        self.dirty = False
        return True

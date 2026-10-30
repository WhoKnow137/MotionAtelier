# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""The applied-curve history service."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from motion_atelier.curve.errors import MotionAtelierError
from motion_atelier.curve.model import Curve
from motion_atelier.generators.base import ShapeKind
from motion_atelier.records.history import (
    MAX_AGE_DAYS,
    MAX_ENTRIES,
    MAX_LABEL,
    History,
    HistoryEntry,
    curve_identity,
    history_from_data,
)
from motion_atelier.store.atomic import JsonStore, StoreWarning

HISTORY_FILE = "history.json"
VIEW_RECENT = "recent"
VIEW_MOST_USED = "most_used"
VIEW_NAME = "name"
VIEWS = (VIEW_RECENT, VIEW_MOST_USED, VIEW_NAME)

UNKNOWN_ENTRY = "unknown_history_entry"


class HistoryError(MotionAtelierError):
    pass


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


class HistoryService:
    def __init__(self, store: JsonStore | None, clock: Callable[[], datetime]) -> None:
        self.store = store
        self.clock = clock
        self.warnings: tuple[StoreWarning, ...] = ()
        self._listeners: list[Callable[[], None]] = []
        if store is None:
            self.history = History()
        else:
            loaded = store.load()
            self.history = loaded.value
            self.warnings = loaded.warnings

    @staticmethod
    def make_store(directory: Path, clock) -> JsonStore:
        return JsonStore(Path(directory) / HISTORY_FILE, history_from_data,
                         lambda h: h.to_data(), History, clock)

    def subscribe(self, listener: Callable[[], None]) -> None:
        self._listeners.append(listener)

    def unsubscribe(self, listener: Callable[[], None]) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def _retain(self, entries: list[HistoryEntry]) -> list[HistoryEntry]:
        cutoff = self.clock() - timedelta(days=MAX_AGE_DAYS)
        kept = [e for e in entries if parse_iso(e.last_used) >= cutoff]
        return kept[:MAX_ENTRIES]

    def _commit(self, entries: list[HistoryEntry]) -> None:
        """Apply retention, save, then (only on success) replace the in-memory history."""
        history = History(tuple(self._retain(entries)))
        if self.store is not None:
            self.store.save(history)
        self.history = history
        for listener in list(self._listeners):
            listener()

    def record(self, curve: Curve, source_kind: ShapeKind, label: str) -> HistoryEntry:
        """Record an applied curve; identical content updates the existing entry."""
        now = iso(self.clock())
        identity = curve_identity(curve)
        label = " ".join(label.split())[:MAX_LABEL] or source_kind.value
        entries = list(self.history.entries)
        existing = next((e for e in entries if e.id == identity), None)
        if existing is not None:
            entries.remove(existing)
            entry = existing.with_(label=label, last_used=now, use_count=existing.use_count + 1,
                                   source_kind=source_kind)
        else:
            entry = HistoryEntry(identity, source_kind, label, now, now, 1, curve)
        self._commit([entry, *entries])
        return entry

    def view(self, mode: str = VIEW_RECENT) -> list[HistoryEntry]:
        entries = list(self.history.entries)
        if mode == VIEW_MOST_USED:
            entries.sort(key=lambda e: -e.use_count)  # stable: ties stay most recent first
        elif mode == VIEW_NAME:
            entries.sort(key=lambda e: (e.label.casefold(), e.label, e.id))
        return entries

    def get(self, entry_id: str) -> HistoryEntry:
        entry = next((e for e in self.history.entries if e.id == entry_id), None)
        if entry is None:
            raise HistoryError(UNKNOWN_ENTRY, entry_id)
        return entry

    def delete(self, entry_ids) -> None:
        ids = set(entry_ids)
        for entry_id in ids:
            self.get(entry_id)
        self._commit([e for e in self.history.entries if e.id not in ids])

    def clear(self) -> None:
        self._commit([])

    def restore_into(self, session, entry_id: str) -> None:
        """Load the entry's curve into Ease as one local undo step (never into the scene)."""
        entry = self.get(entry_id)
        ease = session.documents[ShapeKind.EASE]
        ease.cancel_gesture()
        ease.apply(f"Restore {entry.label}", ease.committed_content.with_curve(entry.curve),
                   frozenset())
        session.switch_shape(ShapeKind.EASE)

# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Applied-curve history records."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

from motion_atelier.curve.errors import MotionAtelierError
from motion_atelier.curve.model import Curve
from motion_atelier.curve.serialize import curve_from_data, curve_to_data
from motion_atelier.generators.base import ShapeKind
from motion_atelier.records.common import (
    InvalidDocument,
    content_digest,
    document,
    exact_keys,
    expect_int,
    expect_list,
    expect_str,
)

HISTORY_SCHEMA = "motion_atelier.history"
HISTORY_VERSION = 1
# Retention: enough for weeks of work, small enough to browse and load instantly.
MAX_ENTRIES = 200
MAX_AGE_DAYS = 90
MAX_LABEL = 120
ISO_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

_ENTRY_KEYS = {"id", "source_kind", "label", "first_used", "last_used", "use_count", "curve"}


def curve_identity(curve: Curve) -> str:
    """Content identity of an applied curve (independent of label, times and counts)."""
    return content_digest(ShapeKind.EASE.value, {"curve": curve_to_data(curve)}, None)


@dataclass(frozen=True)
class HistoryEntry:
    id: str  # the curve's content digest
    source_kind: ShapeKind
    label: str
    first_used: str  # ISO-8601 UTC, e.g. 2026-10-26T12:00:00Z
    last_used: str
    use_count: int
    curve: Curve

    def with_(self, **changes) -> HistoryEntry:
        return replace(self, **changes)

    def to_data(self) -> dict[str, Any]:
        return {"id": self.id, "source_kind": self.source_kind.value, "label": self.label,
                "first_used": self.first_used, "last_used": self.last_used,
                "use_count": self.use_count, "curve": curve_to_data(self.curve)}


def _timestamp(value: Any, what: str) -> str:
    text = expect_str(value, what, 32)
    if not ISO_UTC.match(text):
        raise InvalidDocument(f"{what} must be an ISO-8601 UTC time like 2026-01-31T12:00:00Z")
    return text


def entry_from_data(data: Any) -> HistoryEntry:
    data = exact_keys(data, _ENTRY_KEYS, "history entry")
    try:
        kind = ShapeKind(data["source_kind"])
        curve = curve_from_data(data["curve"])
    except (ValueError, MotionAtelierError) as exc:
        raise InvalidDocument(f"history entry: {exc}") from exc
    entry = HistoryEntry(
        id=expect_str(data["id"], "id", 64),
        source_kind=kind,
        label=expect_str(data["label"], "label", MAX_LABEL),
        first_used=_timestamp(data["first_used"], "first_used"),
        last_used=_timestamp(data["last_used"], "last_used"),
        use_count=expect_int(data["use_count"], "use_count", 1, 10**9),
        curve=curve,
    )
    if entry.id != curve_identity(curve):
        raise InvalidDocument("history entry id does not match its curve")
    return entry


@dataclass(frozen=True)
class History:
    entries: tuple[HistoryEntry, ...] = ()  # most recently used first

    def __post_init__(self) -> None:
        ids = [e.id for e in self.entries]
        if len(set(ids)) != len(ids):
            raise InvalidDocument("duplicate history entries")

    def to_data(self) -> dict[str, Any]:
        return {"schema": HISTORY_SCHEMA, "version": HISTORY_VERSION,
                "entries": [e.to_data() for e in self.entries]}


def history_from_data(data: Any) -> History:
    data = document(data, HISTORY_SCHEMA, HISTORY_VERSION, {"entries"})
    return History(tuple(entry_from_data(e) for e in
                         expect_list(data["entries"], "entries", MAX_ENTRIES)))

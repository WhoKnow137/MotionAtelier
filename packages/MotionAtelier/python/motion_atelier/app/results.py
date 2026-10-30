# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Structured results of application commands (rendered by :mod:`app.status`)."""

from __future__ import annotations

from dataclasses import dataclass, field

from motion_atelier.curve.model import Curve


@dataclass(frozen=True, slots=True)
class Issue:
    """A skipped channel or a warning. ``code`` is stable; ``detail`` is context."""

    code: str
    detail: str = ""
    channel: str | None = None


@dataclass(frozen=True, slots=True)
class GetKeysResult:
    loaded: bool
    source: str
    channel: str | None = None
    curve: Curve | None = None
    key_count: int = 0
    channels_selected: int = 0
    skips: tuple[Issue, ...] = ()
    warnings: tuple[Issue, ...] = ()


@dataclass(frozen=True, slots=True)
class ExecutionReport:
    """What the host did with a plan. ``committed`` means applied *and* verified."""

    committed: bool
    channels_written: tuple[str, ...] = ()
    skips: tuple[Issue, ...] = ()
    error: Issue | None = None
    rolled_back: bool = False
    rollback_method: str = ""  # "undo", "manual", or "" (nothing to restore)
    critical: bool = False


@dataclass(frozen=True, slots=True)
class ApplyResult:
    committed: bool
    source: str = "none"
    channels_selected: int = 0
    channels_valid: int = 0
    intervals: int = 0
    keys_reused: int = 0
    keys_created: int = 0
    keys_removed: int = 0
    skips: tuple[Issue, ...] = ()
    warnings: tuple[Issue, ...] = ()
    error: Issue | None = None
    critical: bool = False


@dataclass(frozen=True, slots=True)
class ToolResult:
    tool: str
    committed: bool
    read_only: bool = False
    source: str = "none"
    channels: int = 0
    keys_changed: int = 0
    keys_created: int = 0
    keys_removed: int = 0
    keys_overwritten: int = 0
    skips: tuple[Issue, ...] = ()
    warnings: tuple[Issue, ...] = ()
    error: Issue | None = None
    critical: bool = False
    report: tuple[tuple[str, str], ...] = field(default=())  # Inspect: (label, value) rows

# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""One-line status messages built from results and the string catalogue.

Every message says what changed with counts, puts the first actionable detail first, and
states explicitly when the scene was not touched.
"""

from __future__ import annotations

from motion_atelier.app.results import ApplyResult, GetKeysResult, Issue, ToolResult
from motion_atelier.app.strings import has, text

NOTHING_TO_DO = "nothing_to_do"

# Outcome kinds shown next to every status message, so the artist can tell at a glance whether
# the scene changed. Ordered from harmless to severe.
KIND_LOCAL = "local"  # editor-only change; the scene is untouched
KIND_INFO = "info"  # read-only report or view change
KIND_SCENE = "scene"  # the scene changed and the change was verified
KIND_WARNING = "warning"  # nothing was done (nothing selected, every channel skipped)
KIND_ERROR = "error"  # refused or failed; the scene is unchanged
KIND_CRITICAL = "critical"  # a rollback could not be verified
KINDS = (KIND_LOCAL, KIND_INFO, KIND_SCENE, KIND_WARNING, KIND_ERROR, KIND_CRITICAL)


def issue_text(issue: Issue) -> str:
    key = f"issue.{issue.code}"
    if not has(key):
        key = "issue.unknown"
    message = text(key, detail=issue.detail, code=issue.code)
    if issue.channel:
        return text("issue.on_channel", channel=issue.channel, message=message)
    return message


def _first_detail(issues) -> str:
    return issue_text(issues[0]) if issues else ""


def _note(issues) -> str:
    """'Note: <first detail>.' plus how many more, if any."""
    if len(issues) == 1:
        return text("status.note_one", detail=_first_detail(issues))
    return text("status.note", detail=_first_detail(issues), more=len(issues) - 1)


def source_text(source: str) -> str:
    key = f"source.{source}"
    return text(key) if has(key) else source


def get_keys_status(result: GetKeysResult) -> str:
    if not result.loaded:
        return text("status.get_keys.failed", reason=_first_detail(result.skips))
    message = text("status.get_keys.loaded", keys=result.key_count, channel=result.channel,
                   source=source_text(result.source))
    extra = [*result.warnings, *result.skips]
    if extra:
        message += _note(extra)
    return message


def apply_status(result: ApplyResult) -> str:
    if result.critical and result.error is not None:
        return text("status.commit.critical", reason=issue_text(result.error))
    if not result.committed:
        reason = result.error or (result.skips[0] if result.skips else Issue(NOTHING_TO_DO))
        return text("status.commit.failed", reason=issue_text(reason))
    message = text(
        "status.commit.done",
        channels=result.channels_valid,
        selected=result.channels_selected,
        intervals=result.intervals,
        created=result.keys_created,
        removed=result.keys_removed,
        reused=result.keys_reused,
    )
    extra = [*result.warnings, *result.skips]
    if extra:
        message += _note(extra)
    return message


def tool_status(result: ToolResult) -> str:
    name = text(f"tool.{result.tool}")
    if result.critical and result.error is not None:
        return text("status.commit.critical", reason=issue_text(result.error))
    if result.read_only:
        return text("status.tool.read_only", tool=name, channels=result.channels)
    if not result.committed:
        reason = result.error or (result.skips[0] if result.skips else Issue(NOTHING_TO_DO))
        return text("status.tool.failed", tool=name, reason=issue_text(reason))
    message = text(
        "status.tool.done",
        tool=name,
        channels=result.channels,
        changed=result.keys_changed,
        created=result.keys_created,
        removed=result.keys_removed,
        overwritten=result.keys_overwritten,
    )
    extra = [*result.warnings, *result.skips]
    if extra:
        message += _note(extra)
    return message


def get_keys_kind(result: GetKeysResult) -> str:
    """Get Keys only reads the scene, so success is an editor change."""
    return KIND_LOCAL if result.loaded else KIND_WARNING


def _write_kind(committed: bool, error: Issue | None, critical: bool) -> str:
    if critical:
        return KIND_CRITICAL
    if committed:
        return KIND_SCENE
    return KIND_ERROR if error is not None else KIND_WARNING


def apply_kind(result: ApplyResult) -> str:
    return _write_kind(result.committed, result.error, result.critical)


def tool_kind(result: ToolResult) -> str:
    if result.read_only and not result.critical:
        return KIND_INFO
    return _write_kind(result.committed, result.error, result.critical)

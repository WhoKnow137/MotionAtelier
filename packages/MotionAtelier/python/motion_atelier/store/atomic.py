# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Atomic versioned JSON files with one backup and recovery.

Save: serialise → write a temporary file in the same directory → flush → ``fsync`` → copy the
current file to ``<name>.bak`` (and ``fsync`` it) → ``os.replace(tmp, path)``. The main file is
therefore always a complete old or complete new document; a failure at any step leaves the
previous file intact and removes the temporary file.

Load: parse and validate the main file. A file that cannot be read, parsed or validated is
set aside as ``<name>.corrupt-<UTC>``; a file with an unknown schema or a future version is
set aside as ``<name>.unsupported-<UTC>`` (kept for a newer build, never guessed at). Recovery
then tries the backup, then the defaults, and every step is reported as a warning.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from motion_atelier.curve.errors import MotionAtelierError
from motion_atelier.records.common import InvalidDocument, UnsupportedDocument

BACKUP_SUFFIX = ".bak"
TEMP_PREFIX = "."
TEMP_SUFFIX = ".tmp"
# Largest document we read (bytes): a full library of thousands of recipes is a few MB.
MAX_FILE_BYTES = 64 * 1024 * 1024

# Stable codes.
WRITE_FAILED = "store_write_failed"
SET_ASIDE = "file_set_aside"
RESTORED_FROM_BACKUP = "restored_from_backup"
RESET_TO_DEFAULTS = "reset_to_defaults"


class StoreError(MotionAtelierError):
    pass


@dataclass(frozen=True, slots=True)
class StoreWarning:
    code: str
    detail: str = ""


@dataclass(frozen=True)
class Loaded:
    value: Any
    warnings: tuple[StoreWarning, ...] = ()
    source: str = "file"  # "file", "backup", or "defaults"


def utc_stamp(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def dumps(data: Any) -> str:
    """Deterministic, readable JSON (sorted keys, LF, trailing newline)."""
    return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


class JsonStore:
    """One JSON document on disk with atomic writes, a backup, and recovery."""

    def __init__(self, path: Path, parse: Callable[[Any], Any], serialise: Callable[[Any], Any],
                 default: Callable[[], Any], clock: Callable[[], datetime]) -> None:
        self.path = Path(path)
        self.backup = self.path.with_name(self.path.name + BACKUP_SUFFIX)
        self._parse = parse
        self._serialise = serialise
        self._default = default
        self._clock = clock

    # -- writing -----------------------------------------------------------------------------

    def save(self, value: Any) -> None:
        text = dumps(self._serialise(value))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f"{TEMP_PREFIX}{self.path.name}.{os.getpid()}{TEMP_SUFFIX}")
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            if self.path.exists():
                self._copy_backup()
            os.replace(tmp, self.path)
        except OSError as exc:
            raise StoreError(WRITE_FAILED, f"{self.path.name}: {exc}") from exc
        finally:
            if tmp.exists():
                with contextlib.suppress(OSError):
                    tmp.unlink()

    def _copy_backup(self) -> None:
        tmp = self.backup.with_name(self.backup.name + TEMP_SUFFIX)
        shutil.copyfile(self.path, tmp)
        with open(tmp, "rb+") as handle:
            os.fsync(handle.fileno())
        os.replace(tmp, self.backup)

    # -- reading -----------------------------------------------------------------------------

    def _read(self, path: Path) -> Any:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise InvalidDocument(f"{path.name} is larger than {MAX_FILE_BYTES} bytes")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise InvalidDocument(f"{path.name}: {exc}") from exc
        return self._parse(data)

    def _set_aside(self, path: Path, kind: str) -> Path:
        target = path.with_name(f"{path.name}.{kind}-{utc_stamp(self._clock())}")
        n = 1
        while target.exists():
            target = path.with_name(f"{path.name}.{kind}-{utc_stamp(self._clock())}-{n}")
            n += 1
        os.replace(path, target)
        return target

    def load(self) -> Loaded:
        warnings: list[StoreWarning] = []
        for path, source in ((self.path, "file"), (self.backup, "backup")):
            if not path.exists():
                continue
            try:
                value = self._read(path)
            except UnsupportedDocument as exc:
                moved = self._set_aside(path, "unsupported")
                warnings.append(StoreWarning(SET_ASIDE, f"{exc.detail} -> {moved.name}"))
                continue
            except MotionAtelierError as exc:
                moved = self._set_aside(path, "corrupt")
                warnings.append(StoreWarning(SET_ASIDE, f"{exc.detail} -> {moved.name}"))
                continue
            if source == "backup":
                warnings.append(StoreWarning(RESTORED_FROM_BACKUP, path.name))
            return Loaded(value, tuple(warnings), source)
        if warnings:
            warnings.append(StoreWarning(RESET_TO_DEFAULTS, self.path.name))
        return Loaded(self._default(), tuple(warnings), "defaults")

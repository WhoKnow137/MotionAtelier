# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Shared strict-validation helpers and document errors for persisted records."""

from __future__ import annotations

import math
from typing import Any

from motion_atelier.curve.errors import MotionAtelierError
from motion_atelier.curve.serialize import digest

UNSUPPORTED_DOCUMENT = "unsupported_document"
INVALID_DOCUMENT = "invalid_document"


class InvalidDocument(MotionAtelierError):
    """A document that is malformed (set aside as corrupt)."""

    def __init__(self, detail: str = "") -> None:
        super().__init__(INVALID_DOCUMENT, detail)


class UnsupportedDocument(MotionAtelierError):
    """Unknown schema or a version this build does not read (never guessed at)."""

    def __init__(self, detail: str = "") -> None:
        super().__init__(UNSUPPORTED_DOCUMENT, detail)


def document(data: Any, schema: str, version: int, keys: set[str]) -> dict:
    """Validate the envelope ``{"schema", "version", ...}`` with an exact key set."""
    if not isinstance(data, dict):
        raise InvalidDocument("document must be an object")
    if data.get("schema") != schema:
        raise UnsupportedDocument(f"schema {data.get('schema')!r}, expected {schema!r}")
    found = data.get("version")
    if isinstance(found, bool) or not isinstance(found, int):
        raise InvalidDocument("version must be an integer")
    if found != version:
        raise UnsupportedDocument(f"{schema} version {found} (this build reads {version})")
    expected = keys | {"schema", "version"}
    if set(data) != expected:
        extra = sorted(set(data) - expected)
        missing = sorted(expected - set(data))
        raise InvalidDocument(f"keys: unexpected {extra}, missing {missing}")
    return data


def exact_keys(data: Any, keys: set[str], what: str) -> dict:
    if not isinstance(data, dict) or set(data) != keys:
        raise InvalidDocument(f"{what} must have exactly the keys {sorted(keys)}")
    return data


def expect_str(value: Any, what: str, max_len: int = 1000, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not value.strip() and not allow_empty) or \
            len(value) > max_len:
        raise InvalidDocument(f"{what} must be a string of 1..{max_len} characters")
    return value


def expect_bool(value: Any, what: str) -> bool:
    if not isinstance(value, bool):
        raise InvalidDocument(f"{what} must be a boolean")
    return value


def expect_int(value: Any, what: str, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise InvalidDocument(f"{what} must be an integer in [{lo}, {hi}]")
    return value


def expect_float(value: Any, what: str, lo: float, hi: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or \
            not math.isfinite(value) or not lo <= value <= hi:
        raise InvalidDocument(f"{what} must be a number in [{lo}, {hi}]")
    return float(value)


def expect_list(value: Any, what: str, max_len: int) -> list:
    if not isinstance(value, list) or len(value) > max_len:
        raise InvalidDocument(f"{what} must be a list of at most {max_len} items")
    return value


def content_digest(kind: str, payload: dict, effects: dict | None) -> str:
    """Content identity: kind + payload + effects; excludes name, tags, favourite."""
    return digest({"kind": kind, "payload": payload, "effects": effects})

# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""The single English string catalogue (``resources/strings.json``).

Every user-visible string is looked up by id with :func:`text`. Tests verify that every id
used in the source exists and that every catalogue entry is used.
"""

from __future__ import annotations

import functools
import json
import string
from pathlib import Path
from typing import Any

CATALOG_PATH = Path(__file__).resolve().parents[1] / "resources" / "strings.json"


@functools.lru_cache(maxsize=1)
def catalog() -> dict[str, str]:
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not all(isinstance(v, str) for v in data.values()):
        raise ValueError("strings.json must map ids to strings")
    return data


def has(key: str) -> bool:
    return key in catalog()


def text(key: str, **values: Any) -> str:
    """Look up ``key`` and format it with ``values`` (``str.format`` fields)."""
    template = catalog()[key]
    return template.format(**values) if values else template


def fields(key: str) -> set[str]:
    return {f for _, f, _, _ in string.Formatter().parse(catalog()[key]) if f}

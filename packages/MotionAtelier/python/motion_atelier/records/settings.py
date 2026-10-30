# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""User settings remembered between sessions."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from motion_atelier.fitting.fit import DEFAULT_TOLERANCE_PCT, MAX_TOLERANCE_PCT, MIN_TOLERANCE_PCT
from motion_atelier.generators.base import ShapeKind
from motion_atelier.records.common import (
    InvalidDocument,
    document,
    expect_bool,
    expect_float,
    expect_int,
    expect_str,
)

SETTINGS_SCHEMA = "motion_atelier.settings"
# Version 2 adds the inspector width and the History drawer state; version 1 files are read
# with their defaults.
SETTINGS_VERSION = 2
V1_KEYS = frozenset({"last_shape", "apply_mode", "fit_tolerance_pct", "preview_seconds",
                     "view_mode"})
V2_KEYS = V1_KEYS | {"inspector_width", "history_open"}

# Mirrors of app-level identifiers (records cannot import app; a test pins the equality).
APPLY_MODES = ("each_interval", "whole_range")
VIEW_MODES = ("value", "velocity")
MIN_PREVIEW_SECONDS = 0.1
MAX_PREVIEW_SECONDS = 60.0
DEFAULT_PREVIEW_SECONDS = 1.5
# Inspector width range and default in logical pixels.
MIN_INSPECTOR_WIDTH = 220
MAX_INSPECTOR_WIDTH = 330
DEFAULT_INSPECTOR_WIDTH = 275


@dataclass(frozen=True)
class Settings:
    last_shape: str = ShapeKind.EASE.value
    apply_mode: str = APPLY_MODES[0]
    fit_tolerance_pct: float = DEFAULT_TOLERANCE_PCT
    preview_seconds: float = DEFAULT_PREVIEW_SECONDS
    view_mode: str = VIEW_MODES[0]
    inspector_width: int = DEFAULT_INSPECTOR_WIDTH
    history_open: bool = False

    def with_(self, **changes) -> Settings:
        return replace(self, **changes)

    def to_data(self) -> dict[str, Any]:
        return {"schema": SETTINGS_SCHEMA, "version": SETTINGS_VERSION,
                "last_shape": self.last_shape, "apply_mode": self.apply_mode,
                "fit_tolerance_pct": self.fit_tolerance_pct,
                "preview_seconds": self.preview_seconds, "view_mode": self.view_mode,
                "inspector_width": self.inspector_width, "history_open": self.history_open}


def settings_from_data(data: Any) -> Settings:
    version = data.get("version") if isinstance(data, dict) else None
    if version == 1:
        data = document(data, SETTINGS_SCHEMA, 1, set(V1_KEYS))
    else:  # the current version; anything else is rejected as unsupported
        data = document(data, SETTINGS_SCHEMA, SETTINGS_VERSION, set(V2_KEYS))
    shape = expect_str(data["last_shape"], "last_shape", 32)
    if shape not in {k.value for k in ShapeKind}:
        raise InvalidDocument(f"unknown shape {shape!r}")
    if data["apply_mode"] not in APPLY_MODES:
        raise InvalidDocument(f"unknown apply mode {data['apply_mode']!r}")
    if data["view_mode"] not in VIEW_MODES:
        raise InvalidDocument(f"unknown view mode {data['view_mode']!r}")
    return Settings(
        last_shape=shape,
        apply_mode=data["apply_mode"],
        fit_tolerance_pct=expect_float(data["fit_tolerance_pct"], "fit_tolerance_pct",
                                       MIN_TOLERANCE_PCT, MAX_TOLERANCE_PCT),
        preview_seconds=expect_float(data["preview_seconds"], "preview_seconds",
                                     MIN_PREVIEW_SECONDS, MAX_PREVIEW_SECONDS),
        view_mode=data["view_mode"],
        inspector_width=expect_int(data["inspector_width"], "inspector_width",
                                   MIN_INSPECTOR_WIDTH, MAX_INSPECTOR_WIDTH)
        if "inspector_width" in data else DEFAULT_INSPECTOR_WIDTH,
        history_open=expect_bool(data["history_open"], "history_open")
        if "history_open" in data else False,
    )

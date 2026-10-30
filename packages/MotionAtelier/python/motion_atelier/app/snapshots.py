# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Immutable plain-data snapshots of Houdini channels and selections.

The host adapter copies every ``hou.Keyframe`` into a :class:`KeySnapshot` immediately; no
native object is ever retained by the application layer. Times are seconds (``time()``,
which round-trips exactly; ``frame()`` is rounded). An in-side that Houdini
reports as unset (tied) is stored as ``None``.
"""

from __future__ import annotations

from dataclasses import dataclass

BEZIER = "bezier()"
HSCRIPT = "hscript"
PYTHON = "python"


def normalise_expression(text: str) -> str:
    return "".join(text.split())


@dataclass(frozen=True, slots=True)
class KeySnapshot:
    time: float
    value: float
    in_value: float | None
    slope: float
    in_slope: float | None
    accel: float
    in_accel: float | None
    slope_auto: bool
    in_slope_auto: bool
    accel_ratio: bool
    expression: str
    language: str
    value_set: bool = True
    slope_set: bool = True
    accel_set: bool = True
    value_used: bool = True
    slope_used: bool = True
    accel_used: bool = True

    @property
    def eff_in_value(self) -> float:
        return self.value if self.in_value is None else self.in_value

    @property
    def eff_in_slope(self) -> float:
        return self.slope if self.in_slope is None else self.in_slope

    @property
    def eff_in_accel(self) -> float:
        return self.accel if self.in_accel is None else self.in_accel

    @property
    def is_bezier(self) -> bool:
        """True if the segment *starting* at this key is a Bezier segment."""
        return self.language == HSCRIPT and normalise_expression(self.expression) == BEZIER

    @property
    def has_discontinuity(self) -> bool:
        return self.in_value is not None and self.in_value != self.value


@dataclass(frozen=True, slots=True)
class ChannelSnapshot:
    """One animated parameter: every key (time-sorted) and which of them are selected."""

    path: str
    keys: tuple[KeySnapshot, ...]
    selected: tuple[int, ...]
    locked: bool = False
    editable: bool = True
    numeric: bool = True

    def selected_keys(self) -> tuple[KeySnapshot, ...]:
        return tuple(self.keys[i] for i in self.selected)

    @property
    def sort_key(self) -> tuple[str, str]:
        return (self.path.casefold(), self.path)


@dataclass(frozen=True, slots=True)
class SelectionSnapshot:
    """What was selected, where it came from, and the scene frame rate at that moment."""

    source: str  # "graph", "playbar", "channel_list", "explicit", or "none"
    channels: tuple[ChannelSnapshot, ...]
    fps: float
    playhead_time: float = 0.0
    notices: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not any(c.selected for c in self.channels)


def ordered_channels(channels) -> tuple[ChannelSnapshot, ...]:
    """Deterministic channel order: case-folded path, then raw path."""
    return tuple(sorted(channels, key=lambda c: c.sort_key))


SOURCE_GRAPH = "graph"
SOURCE_PLAYBAR = "playbar"
SOURCE_CHANNEL_LIST = "channel_list"
SOURCE_EXPLICIT = "explicit"
SOURCE_NONE = "none"

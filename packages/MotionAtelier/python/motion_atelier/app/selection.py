# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Deterministic choice between selection sources.

The host lists every Channel Editor graph it can see as a :class:`GraphCandidate` (plain
data: whether it is under the cursor, whether it is its pane's current tab, and the set of
selected ``(parameter path, key time)`` pairs). Resolution never depends on the order in
which panes were enumerated: it compares the *contents* of the selections.

Order: (1) the graph under the cursor, if it has a selection; (2) otherwise the current-tab
graphs with a selection, if they all agree; (3) otherwise any graphs with a selection, if they
all agree; (4) otherwise — no graph selection, or disagreeing graphs — the playbar.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

SelectionKey = frozenset[tuple[str, float]]

GRAPH = "graph"
PLAYBAR = "playbar"
AMBIGUOUS = "graph_selection_ambiguous"


@dataclass(frozen=True, slots=True)
class GraphCandidate:
    under_cursor: bool
    current_tab: bool
    keys: SelectionKey


@dataclass(frozen=True, slots=True)
class Resolution:
    source: str  # GRAPH or PLAYBAR
    keys: SelectionKey | None  # the graph selection to use, or None for the playbar
    notice: str | None = None


def _agreeing(selections: list[SelectionKey]) -> SelectionKey | None:
    distinct = set(selections)
    return next(iter(distinct)) if len(distinct) == 1 else None


def resolve(candidates: Iterable[GraphCandidate]) -> Resolution:
    with_keys = [c for c in candidates if c.keys]
    under = [c.keys for c in with_keys if c.under_cursor]
    if under:
        chosen = _agreeing(under)
        if chosen is not None:
            return Resolution(GRAPH, chosen)
    current = [c.keys for c in with_keys if c.current_tab]
    if current:
        chosen = _agreeing(current)
        if chosen is not None:
            return Resolution(GRAPH, chosen)
        return Resolution(PLAYBAR, None, AMBIGUOUS)
    if with_keys:
        chosen = _agreeing([c.keys for c in with_keys])
        if chosen is not None:
            return Resolution(GRAPH, chosen)
        return Resolution(PLAYBAR, None, AMBIGUOUS)
    return Resolution(PLAYBAR, None)

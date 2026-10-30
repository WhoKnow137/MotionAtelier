# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Read the user's key selection from Houdini and turn it into snapshots.

Graph selection needs ``hou.ui``, which exists only in graphical Houdini; without it
the playbar is tried, and without the playbar UI (``hou.NotAvailable``) the selection
is empty with a notice. The choice between several Channel Editors is made by the pure
:func:`motion_atelier.app.selection.resolve`, which compares selection contents and never
depends on pane enumeration order.
"""

from __future__ import annotations

import contextlib
from collections.abc import Mapping, Sequence

import hou

from motion_atelier.app.ports import PURPOSE_TOOL
from motion_atelier.app.selection import GRAPH, GraphCandidate, resolve
from motion_atelier.app.snapshots import (
    SOURCE_CHANNEL_LIST,
    SOURCE_EXPLICIT,
    SOURCE_GRAPH,
    SOURCE_NONE,
    SOURCE_PLAYBAR,
    SelectionSnapshot,
    ordered_channels,
)
from motion_atelier.host.snapshot import channel_snapshot

NOTICE_NO_UI = "no_ui"
NOTICE_PLAYBAR_UNAVAILABLE = "playbar_unavailable"


def _channel_editor_tabs() -> tuple[list, object]:
    ui = getattr(hou, "ui", None)
    if ui is None:
        return [], None
    tabs: list = []

    def add(tab) -> None:
        if tab is not None and not any(tab == t for t in tabs):
            tabs.append(tab)

    for tab in ui.paneTabs():
        add(tab)
    floating = getattr(ui, "floatingPanels", None)
    if floating is not None:
        for panel in floating():
            for tab in panel.paneTabs():
                add(tab)
    try:
        under = ui.paneTabUnderCursor()
    except hou.Error:
        under = None
    editors = [t for t in tabs if t.type() == hou.paneTabType.ChannelEditor]
    return editors, under


def _selected_by_parm(selection: Mapping) -> dict[str, tuple[object, list[float]]]:
    out: dict[str, tuple[object, list[float]]] = {}
    for parm, keys in selection.items():
        times = [float(k.time()) for k in keys if isinstance(k, hou.BaseKeyframe)]
        if times:
            path = parm.path()
            out.setdefault(path, (parm, []))[1].extend(times)
    return out


def _graph_selection() -> tuple[str, dict[str, tuple[object, list[float]]], tuple[str, ...]]:
    editors, under = _channel_editor_tabs()
    if getattr(hou, "ui", None) is None:
        return SOURCE_NONE, {}, (NOTICE_NO_UI,)
    candidates: list[GraphCandidate] = []
    by_key: dict[frozenset, dict] = {}
    for tab in editors:
        try:
            raw = tab.graph().selectedKeyframes()
        except hou.Error:
            continue
        mapping = _selected_by_parm(raw)
        keyset = frozenset((path, t) for path, (_, times) in mapping.items() for t in times)
        by_key[keyset] = mapping
        candidates.append(GraphCandidate(
            under_cursor=under is not None and tab == under,
            current_tab=bool(tab.isCurrentTab()),
            keys=keyset,
        ))
    resolution = resolve(candidates)
    notices = (resolution.notice,) if resolution.notice else ()
    if resolution.source == GRAPH and resolution.keys is not None:
        return SOURCE_GRAPH, by_key[resolution.keys], notices
    try:
        raw = hou.playbar.selectedKeyframes()
    except hou.NotAvailable:
        return SOURCE_NONE, {}, (*notices, NOTICE_PLAYBAR_UNAVAILABLE)
    return SOURCE_PLAYBAR, _selected_by_parm(raw), notices


def _channel_list_parms() -> list:
    lists = []
    editors, _ = _channel_editor_tabs()
    lists.extend(tab.channelList() for tab in editors if tab.isCurrentTab())
    with contextlib.suppress(hou.Error):
        lists.append(hou.playbar.channelList())
    parms: dict[str, object] = {}
    for channel_list in lists:
        try:
            for parm in channel_list.selected():
                parms.setdefault(parm.path(), parm)
        except hou.Error:
            continue
    return [parms[p] for p in sorted(parms, key=lambda p: (p.casefold(), p))]


def snapshot_selection(purpose: str,
                       explicit: Mapping[str, Sequence[float] | None] | None = None
                       ) -> SelectionSnapshot:
    """Snapshot the current selection for ``purpose``.

    ``explicit`` (path → selected times, or ``None`` for every key) bypasses the UI; it is
    used by scripted callers and by the hython test-suite.
    """
    fps = float(hou.fps())
    playhead = float(hou.time())
    if explicit is not None:
        channels = []
        for path, times in explicit.items():
            parm = hou.parm(path)
            if parm is None:
                continue
            if times is None:
                times = [k.time() for k in parm.keyframes()]
            channels.append(channel_snapshot(parm, times))
        return SelectionSnapshot(SOURCE_EXPLICIT, ordered_channels(channels), fps, playhead)
    source, mapping, notices = _graph_selection()
    channels = [channel_snapshot(parm, times) for parm, times in mapping.values()]
    if purpose == PURPOSE_TOOL and not channels:
        parms = _channel_list_parms()
        if parms:
            whole = [channel_snapshot(p, [k.time() for k in p.keyframes()]) for p in parms]
            return SelectionSnapshot(SOURCE_CHANNEL_LIST, ordered_channels(whole), fps,
                                     playhead, notices)
    return SelectionSnapshot(source, ordered_channels(channels), fps, playhead, notices)

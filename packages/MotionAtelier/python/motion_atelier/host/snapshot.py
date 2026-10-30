# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Copy native Houdini keys and parameters into immutable application snapshots."""

from __future__ import annotations

from collections.abc import Iterable

import hou

from motion_atelier.app.snapshots import HSCRIPT, PYTHON, ChannelSnapshot, KeySnapshot

# Matching selected keys to the parameter's key list uses exact times (both come from the same
# stored doubles); this tolerance only guards against representation differences
# between key objects. 1e-9 s is far below Houdini's own key merge distance (~1e-3 frame).
SELECTED_TIME_MATCH = 1e-9


def _get(key, name: str):
    try:
        return getattr(key, name)()
    except hou.KeyframeValueNotSet:
        return None


def _language(key) -> str:
    try:
        lang = key.expressionLanguage()
    except hou.KeyframeValueNotSet:
        return HSCRIPT
    return PYTHON if lang == hou.exprLanguage.Python else HSCRIPT


def key_snapshot(key) -> KeySnapshot:
    """Snapshot one ``hou.Keyframe``. Unset (tied) in-sides become ``None``."""
    in_value = None if key.isValueTied() else _get(key, "inValue")
    in_slope = None if key.isSlopeTied() else _get(key, "inSlope")
    in_accel = None if key.isAccelTied() else _get(key, "inAccel")
    return KeySnapshot(
        time=float(key.time()),
        value=float(_get(key, "value") or 0.0),
        in_value=None if in_value is None else float(in_value),
        slope=float(_get(key, "slope") or 0.0),
        in_slope=None if in_slope is None else float(in_slope),
        accel=float(_get(key, "accel") or 0.0),
        in_accel=None if in_accel is None else float(in_accel),
        slope_auto=bool(key.isSlopeAuto()),
        in_slope_auto=bool(key.isInSlopeAuto()),
        accel_ratio=bool(key.isAccelInterpretedAsRatio()),
        expression=str(_get(key, "expression") or ""),
        language=_language(key),
        value_set=bool(key.isValueSet()),
        slope_set=bool(key.isSlopeSet()),
        accel_set=bool(key.isAccelSet()),
        value_used=bool(key.isValueUsed()),
        slope_used=bool(key.isSlopeUsed()),
        accel_used=bool(key.isAccelUsed()),
    )


def is_numeric_parm(parm) -> bool:
    try:
        return parm.parmTemplate().type() in (hou.parmTemplateType.Float,
                                               hou.parmTemplateType.Int)
    except hou.Error:
        return False


def parm_editable(parm) -> bool:
    node = parm.node()
    is_editable = getattr(node, "isEditable", None)
    return bool(is_editable()) if is_editable is not None else True


def channel_snapshot(parm, selected_times: Iterable[float] = ()) -> ChannelSnapshot:
    numeric = is_numeric_parm(parm)
    raw = parm.keyframes() if numeric else ()
    keys = tuple(sorted((key_snapshot(k) for k in raw if isinstance(k, hou.Keyframe)),
                        key=lambda k: k.time))
    wanted = sorted(set(float(t) for t in selected_times))
    selected: list[int] = []
    for t in wanted:
        for index, k in enumerate(keys):
            if abs(k.time - t) <= SELECTED_TIME_MATCH:
                selected.append(index)
                break
    return ChannelSnapshot(
        path=parm.path(),
        keys=keys,
        selected=tuple(sorted(set(selected))),
        locked=bool(parm.isLocked()),
        editable=parm_editable(parm),
        numeric=numeric,
    )

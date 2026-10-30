# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Transactional execution of channel plans in Houdini.

1. **Pre-flight** (nothing modified): undo must be enabled; every channel is re-resolved by
   path and must still equal its snapshot exactly, and be unlocked and editable. Channels
   failing pre-flight are skipped and reported.
2. **Apply** inside one ``hou.undos.group``: deletions, then key writes in time order.
3. **Verify** inside the group: every written key is read back and compared with what was
   written; every other key must still equal its snapshot exactly; and the channel must
   evaluate *identically* (same evaluator, bitwise) outside the committed ranges.
4. **On any failure** the group is closed and, if the scene changed, the group is undone with
   ``hou.undos.performUndo()`` (exact), then a no-op undo entry clears the stale redo. If undo
   is impossible, keys are rebuilt from the snapshots as a fallback. Either way every touched
   channel is compared with its snapshot; any difference is reported as a **critical**
   failure naming the channels. Success is never reported for unverified data.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from itertools import pairwise

import hou

from motion_atelier.app.commit import ChannelPlan, KeyWrite, expected_keys, readback_mismatches
from motion_atelier.app.results import ExecutionReport, Issue
from motion_atelier.app.snapshots import PYTHON, KeySnapshot
from motion_atelier.host.snapshot import channel_snapshot, parm_editable

# Stable codes.
UNDO_DISABLED = "undo_disabled"
CHANNEL_MISSING = "channel_missing"
STALE_SNAPSHOT = "stale_snapshot"
CHANNEL_LOCKED = "channel_locked"
CHANNEL_NOT_EDITABLE = "channel_not_editable"
NOTHING_VALID = "no_valid_channels"
WRITE_FAILED = "write_failed"
READBACK_MISMATCH = "readback_mismatch"
OUTSIDE_CHANGED = "outside_region_changed"
ROLLBACK_FAILED = "rollback_failed"

# Probe times per untouched segment for the outside-evaluation check, plus extrapolation
# probes one second beyond each end. Eight interior points catch any change of a cubic.
OUTSIDE_PROBES_PER_SEGMENT = 8
EXTRAPOLATION_PROBE_SECONDS = 1.0
# Relative tolerance of the outside-evaluation check. Re-writing a boundary key with the
# values Houdini already holds can move evaluation by ~1e-15 (measured); 1e-9 of the
# value magnitude is far below any visible change and far above that noise.
OUTSIDE_EVAL_REL_TOL = 1e-9
# Stand-in for a zero acceleration beside a non-zero one. Evaluation moves by about
# 3x the acceleration relative to the value span (measured), i.e. ~3e-15: below both the
# outside check above and the read-back tolerance, so writes still verify exactly.
ZERO_ACCEL = 1e-15

Fault = Callable[[str, str], None]


class VerificationError(Exception):
    def __init__(self, issue: Issue) -> None:
        super().__init__(f"{issue.code}: {issue.detail}")
        self.issue = issue


class _NoRedo:
    """A no-op undo entry: pushing it clears the redo stack."""

    def undo(self) -> None:
        pass

    def redo(self) -> None:
        pass


def _language(name: str):
    return hou.exprLanguage.Python if name == PYTHON else hou.exprLanguage.Hscript


def _storable_accel(accel: float, other: float) -> float:
    """Houdini treats an acceleration of 0 as unset and ties it to the other side's,
    so a 0 next to a non-zero side is written as :data:`ZERO_ACCEL` (same evaluation)."""
    return ZERO_ACCEL if accel == 0.0 and other != 0.0 else accel


def build_keyframe(write: KeyWrite):
    """A fully explicit ``hou.Keyframe`` for a :class:`KeyWrite` (auto off, absolute accel)."""
    k = hou.Keyframe()
    k.setTime(write.time)
    k.setValue(write.value)
    if write.in_value is not None:
        k.setInValue(write.in_value)
    k.setExpression(write.expression, _language(write.language))
    k.setSlopeAuto(False)
    k.setInSlopeAuto(False)
    k.setSlope(write.out_half.slope)
    k.setInSlope(write.in_half.slope)
    out_accel, in_accel = write.out_half.accel, write.in_half.accel
    k.setAccel(_storable_accel(out_accel, in_accel))
    k.setInAccel(_storable_accel(in_accel, out_accel))
    k.interpretAccelAsRatio(False)  # after setAccel, which would enable ratio mode
    return k


def keyframe_from_snapshot(snap: KeySnapshot):
    """Best-effort rebuild of a snapshot key (fallback restore only; not exact)."""
    k = hou.Keyframe()
    k.setTime(snap.time)
    if snap.value_set:
        k.setValue(snap.value)
    if snap.in_value is not None:
        k.setInValue(snap.in_value)
    k.setExpression(snap.expression, _language(snap.language))
    if snap.slope_set:
        k.setSlope(snap.slope)
    if snap.in_slope is not None:
        k.setInSlope(snap.in_slope)
    if snap.accel_set:
        k.setAccel(snap.accel)
    if snap.in_accel is not None:
        k.setInAccel(snap.in_accel)
    k.setSlopeAuto(snap.slope_auto)
    k.setInSlopeAuto(snap.in_slope_auto)
    k.interpretAccelAsRatio(snap.accel_ratio)
    return k


def _outside_probe_times(plan: ChannelPlan) -> list[float]:
    """Times whose evaluation must not change: untouched segments and extrapolation."""
    ranges = plan.intervals

    def inside(t: float) -> bool:
        return any(a <= t <= b for a, b in ranges)

    keys = plan.snapshot.keys
    times: list[float] = []
    if keys and plan.check_extrapolation:
        times.append(keys[0].time - EXTRAPOLATION_PROBE_SECONDS)
        times.append(keys[-1].time + EXTRAPOLATION_PROBE_SECONDS)
    for a, b in pairwise(keys):
        lo, hi = a.time, b.time
        # A segment is untouched only if no committed range overlaps it.
        if any(not (hi <= ra or lo >= rb) for ra, rb in ranges):
            continue
        for j in range(1, OUTSIDE_PROBES_PER_SEGMENT + 1):
            t = lo + (hi - lo) * j / (OUTSIDE_PROBES_PER_SEGMENT + 1)
            if not inside(t):
                times.append(t)
    return times


def _evaluate(parm, times: Sequence[float]) -> list[float]:
    return [parm.evalAtTime(t) for t in times]


def _apply(parm, plan: ChannelPlan, fault: Fault | None) -> None:
    """Deletions, then writes, then the same writes again.

    Deleting a key rescales its neighbours' facing accelerations, and appending a key
    after the current last key rewrites that key's outgoing acceleration. The second pass
    replaces keys at times that already exist — a structure-free operation that disturbs no
    neighbour — so every planned key ends exactly as written regardless of write order.
    """
    for t in plan.deletions:
        parm.deleteKeyframeAtFrame(hou.timeToFrame(t))
    if fault:
        fault("after_delete", plan.path)
    keys = [build_keyframe(write) for write in plan.writes]
    for k in keys:
        parm.setKeyframe(k)
    for k in keys:
        parm.setKeyframe(k)
    if fault:
        fault("after_write", plan.path)


def _verify(parm, plan: ChannelPlan, probe_times: list[float], before: list[float]) -> None:
    actual = channel_snapshot(parm).keys
    expected = expected_keys(plan)
    path = plan.path
    if len(actual) != len(expected):
        raise VerificationError(Issue(
            READBACK_MISMATCH, f"{len(actual)} keys found, {len(expected)} expected", path))
    previous_bezier = False
    for want, got in zip(expected, actual, strict=True):
        if isinstance(want, KeySnapshot):
            if want != got:
                raise VerificationError(Issue(
                    READBACK_MISMATCH, f"untouched key at {want.time:.6g}s changed", path))
            previous_bezier = want.is_bezier
            continue
        problems = readback_mismatches(
            want, got, check_in=previous_bezier,
            check_out=want.expression.replace(" ", "") == "bezier()",
        )
        if problems:
            raise VerificationError(Issue(
                READBACK_MISMATCH, f"key at {want.time:.6g}s: {'; '.join(problems)}", path))
        previous_bezier = got.is_bezier
    after = _evaluate(parm, probe_times)
    worst = 0.0
    for a, b in zip(after, before, strict=True):
        change = abs(a - b)
        if change > OUTSIDE_EVAL_REL_TOL * max(1.0, abs(a), abs(b)):
            worst = max(worst, change)
    if worst:
        raise VerificationError(Issue(OUTSIDE_CHANGED, f"max change {worst:.3g}", path))


def _restore(items, label: str, labels_before: list[str]) -> tuple[bool, str, list[str]]:
    """Undo our group (or rebuild as a fallback) and verify. Returns (ok, method, bad)."""
    changed = [plan.path for plan, parm in items if channel_snapshot(parm).keys
               != plan.snapshot.keys]
    if not changed:
        return True, "", []
    method = "manual"
    labels = list(hou.undos.undoLabels())
    if labels[:1] == [label] and labels[1:] == labels_before[: len(labels) - 1]:
        try:
            hou.undos.performUndo()
            hou.undos.add(_NoRedo(), f"{label} (rolled back)")
            method = "undo"
        except hou.Error:
            method = "manual"
    if method == "manual":
        with hou.undos.group(f"{label} (restore)"):
            for plan, parm in items:
                if channel_snapshot(parm).keys != plan.snapshot.keys:
                    parm.deleteAllKeyframes()
                    parm.setKeyframes([keyframe_from_snapshot(k) for k in plan.snapshot.keys])
    bad = [plan.path for plan, parm in items if channel_snapshot(parm).keys
           != plan.snapshot.keys]
    return not bad, method, bad


def execute(plans: Sequence[ChannelPlan], label: str, fault: Fault | None = None
            ) -> ExecutionReport:
    """Apply ``plans`` as one undoable, verified, all-or-nothing transaction."""
    if not hou.undos.areEnabled():
        return ExecutionReport(False, error=Issue(UNDO_DISABLED))
    items = []
    skips: list[Issue] = []
    for plan in plans:
        parm = hou.parm(plan.path)
        if parm is None:
            skips.append(Issue(CHANNEL_MISSING, "", plan.path))
            continue
        if parm.isLocked():
            skips.append(Issue(CHANNEL_LOCKED, "", plan.path))
            continue
        if not parm_editable(parm):
            skips.append(Issue(CHANNEL_NOT_EDITABLE, "", plan.path))
            continue
        if channel_snapshot(parm).keys != plan.snapshot.keys:
            skips.append(Issue(STALE_SNAPSHOT, "keys changed since they were read", plan.path))
            continue
        items.append((plan, parm))
    if not items:
        return ExecutionReport(False, skips=tuple(skips),
                               error=skips[0] if skips else Issue(NOTHING_VALID))
    probes = {}
    for plan, parm in items:
        times = _outside_probe_times(plan)
        probes[plan.path] = (times, _evaluate(parm, times))
    labels_before = list(hou.undos.undoLabels())
    failure: Issue | None = None
    try:
        with hou.undos.group(label):
            for plan, parm in items:
                if fault:
                    fault("before_channel", plan.path)
                _apply(parm, plan, fault)
            for plan, parm in items:
                if fault:
                    fault("before_verify", plan.path)
                _verify(parm, plan, *probes[plan.path])
    except VerificationError as exc:
        failure = exc.issue
    except Exception as exc:  # any hou or Python error while applying
        failure = Issue(WRITE_FAILED, f"{type(exc).__name__}: {exc}")
    if failure is None:
        return ExecutionReport(True, tuple(p.path for p, _ in items), tuple(skips))
    ok, method, bad = _restore(items, label, labels_before)
    if not ok:
        return ExecutionReport(
            False, (), tuple(skips),
            Issue(ROLLBACK_FAILED, f"{failure.code}; channels not restored: {', '.join(bad)}"),
            rolled_back=False, rollback_method=method, critical=True)
    return ExecutionReport(False, (), tuple(skips), failure, rolled_back=bool(method),
                           rollback_method=method)

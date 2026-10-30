# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""The editor session: one document per shape, the current shape, and user commands.

This is the application service the UI talks to. It never touches ``hou``; scene access goes
through the injected :class:`~motion_atelier.app.ports.HostPort`.
"""

from __future__ import annotations

from collections.abc import Callable

from motion_atelier.app.commit import APPLY_EACH, APPLY_MODES, plan_commit
from motion_atelier.app.document import ShapeDocument
from motion_atelier.app.getkeys import plan_get_keys
from motion_atelier.app.ports import PURPOSE_COMMIT, PURPOSE_GET_KEYS, PURPOSE_TOOL, HostPort
from motion_atelier.app.results import ApplyResult, GetKeysResult, Issue, ToolResult
from motion_atelier.app.snapshots import SOURCE_NONE
from motion_atelier.app.status import (
    KIND_INFO,
    apply_kind,
    apply_status,
    get_keys_kind,
    get_keys_status,
    tool_kind,
    tool_status,
)
from motion_atelier.app.strings import text
from motion_atelier.app.tools import plan_tool
from motion_atelier.curve.errors import MotionAtelierError
from motion_atelier.curve.model import Curve
from motion_atelier.effects.stack import evaluate as evaluate_effects
from motion_atelier.effects.stack import is_effective, needs_fit
from motion_atelier.fitting.fit import DEFAULT_TOLERANCE_PCT, fit, tolerance_from_pct
from motion_atelier.generators.base import ShapeKind

SHAPE_ORDER: tuple[ShapeKind, ...] = (
    ShapeKind.EASE, ShapeKind.BOUNCE, ShapeKind.ELASTIC, ShapeKind.WAVE, ShapeKind.NOISE,
)

HOST_ERROR = "host_error"
FIT_FAILED = "fit_failed"
EFFECTS_FITTED = "effects_fitted"


class EditorSession:
    def __init__(self, host: HostPort | None, evaluator=evaluate_effects) -> None:
        self.host = host
        self.documents: dict[ShapeKind, ShapeDocument] = {
            kind: ShapeDocument(kind, evaluator=evaluator) for kind in SHAPE_ORDER
        }
        self.current_kind = ShapeKind.EASE
        self.apply_mode = APPLY_EACH
        self.fit_tolerance_pct = DEFAULT_TOLERANCE_PCT
        self.status = ""
        self.status_kind = KIND_INFO
        self._listeners: list[Callable[[str], None]] = []
        # Called after every verified commit with (document, committed curve, result); used by
        # the applied-curve history.
        self.commit_listeners: list[Callable[[ShapeDocument, Curve, ApplyResult], None]] = []

    # -- observation -------------------------------------------------------------------------

    def subscribe(self, listener: Callable[[str], None]) -> None:
        """``listener(event)`` with event in {"shape", "status"}."""
        self._listeners.append(listener)

    def unsubscribe(self, listener: Callable[[str], None]) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def _emit(self, event: str) -> None:
        for listener in list(self._listeners):
            listener(event)

    def set_status(self, message: str, kind: str = KIND_INFO) -> None:
        self.status = message
        self.status_kind = kind
        self._emit("status")

    # -- shapes ------------------------------------------------------------------------------

    @property
    def current(self) -> ShapeDocument:
        return self.documents[self.current_kind]

    def switch_shape(self, kind: ShapeKind) -> None:
        if kind == self.current_kind:
            return
        self.current.cancel_gesture()
        self.current_kind = kind
        self._emit("shape")

    # -- commands ----------------------------------------------------------------------------

    def get_keys(self) -> GetKeysResult:
        """Read the selected keys into the Ease document (one local undo step)."""
        if self.host is None:
            result = GetKeysResult(False, SOURCE_NONE, skips=(Issue(HOST_ERROR, "no host"),))
        else:
            try:
                selection = self.host.read_selection(PURPOSE_GET_KEYS)
            except MotionAtelierError as exc:
                result = GetKeysResult(False, SOURCE_NONE,
                                       skips=(Issue(exc.code, exc.detail),))
            else:
                result = plan_get_keys(selection)
        if result.loaded and result.curve is not None:
            ease = self.documents[ShapeKind.EASE]
            ease.cancel_gesture()
            ease.apply("Get Keys", ease.committed_content.with_curve(result.curve), frozenset())
            self.switch_shape(ShapeKind.EASE)
        self.set_status(get_keys_status(result), get_keys_kind(result))
        return result

    def set_apply_mode(self, mode: str) -> None:
        if mode not in APPLY_MODES:
            raise ValueError(f"unknown apply mode {mode!r}")
        if mode != self.apply_mode:
            self.apply_mode = mode
            self._emit("apply_mode")

    def effects_active(self) -> bool:
        return is_effective(self.current.content.effects)

    def can_commit(self) -> bool:
        """Whether a commit can be attempted (a host is available)."""
        return self.host is not None

    def commit_curve(self) -> tuple[Curve | None, tuple[Issue, ...]]:
        """The curve a commit writes.

        Without sampled effects this is the evaluated curve itself (already sparse). With
        sampled or follower effects the dense result goes through the sparse fitter at the
        session's tolerance; if no verified reduction exists the commit is refused (``None``)
        rather than writing one key per sample.
        """
        doc = self.current
        curve = doc.evaluated_curve()
        if not needs_fit(doc.content.effects):
            return curve, ()
        result = fit(curve, tolerance_from_pct(curve, self.fit_tolerance_pct))
        if not result.reduced:
            return None, (Issue(FIT_FAILED, result.reason or ""),)
        detail = f"{result.knots_in} -> {result.knots_out}"
        return result.curve, (Issue(EFFECTS_FITTED, detail),)

    def commit_label(self) -> str:
        return text("undo.commit", shape=text(f"shape.{self.current_kind.value}"))

    def commit_keys(self) -> ApplyResult:
        """Write the current curve onto the selected keys: one Houdini undo step, verified,
        rolled back completely on any failure."""
        doc = self.current
        doc.cancel_gesture()
        if self.host is None:
            result = ApplyResult(False, error=Issue(HOST_ERROR, "no host"))
            self.set_status(apply_status(result), apply_kind(result))
            return result
        curve, curve_issues = self.commit_curve()
        if curve is None:
            result = ApplyResult(False, error=curve_issues[0])
            self.set_status(apply_status(result), apply_kind(result))
            return result
        try:
            selection = self.host.read_selection(PURPOSE_COMMIT)
        except MotionAtelierError as exc:
            result = ApplyResult(False, error=Issue(exc.code, exc.detail))
            self.set_status(apply_status(result), apply_kind(result))
            return result
        plan = plan_commit(selection, curve, self.apply_mode)
        if not plan.channels:
            result = ApplyResult(False, plan.source, plan.channels_selected, skips=plan.skips,
                                 warnings=(*curve_issues, *plan.warnings))
            self.set_status(apply_status(result), apply_kind(result))
            return result
        report = self.host.execute(plan.channels, self.commit_label())
        written = set(report.channels_written)
        done = [c for c in plan.channels if c.path in written]
        warnings = (*curve_issues, *(w for w in plan.warnings
                                     if w.channel is None or w.channel in written))
        result = ApplyResult(
            committed=report.committed,
            source=plan.source,
            channels_selected=plan.channels_selected,
            channels_valid=len(done),
            intervals=sum(len(c.intervals) for c in done),
            keys_reused=sum(c.keys_reused for c in done),
            keys_created=sum(c.keys_created for c in done),
            keys_removed=sum(c.keys_removed for c in done),
            skips=(*plan.skips, *report.skips),
            warnings=warnings if report.committed else (),
            error=report.error,
            critical=report.critical,
        )
        self.set_status(apply_status(result), apply_kind(result))
        if result.committed:
            for listener in list(self.commit_listeners):
                listener(doc, curve, result)
        return result

    def run_tool(self, tool: str, options: dict | None = None) -> ToolResult:
        """Plan and execute a key tool as one verified, undoable transaction."""
        self.current.cancel_gesture()
        if self.host is None:
            result = ToolResult(tool, False, error=Issue(HOST_ERROR, "no host"))
            self.set_status(tool_status(result), tool_kind(result))
            return result
        try:
            selection = self.host.read_selection(PURPOSE_TOOL)
        except MotionAtelierError as exc:
            result = ToolResult(tool, False, error=Issue(exc.code, exc.detail))
            self.set_status(tool_status(result), tool_kind(result))
            return result
        plan = plan_tool(tool, selection, options)
        if plan.read_only:
            result = ToolResult(tool, False, read_only=True, source=plan.source,
                                channels=plan.channels_seen, report=plan.report)
        elif not plan.channels:
            result = ToolResult(tool, False, source=plan.source, skips=plan.skips,
                                warnings=plan.warnings)
        else:
            report = self.host.execute(plan.channels, text("undo.tool", tool=text(f"tool.{tool}")))
            written = set(report.channels_written)
            done = [plan.counts.get(p, (0, 0, 0)) for p in written]
            result = ToolResult(
                tool,
                committed=report.committed,
                source=plan.source,
                channels=len(written),
                keys_changed=sum(c[0] for c in done),
                keys_created=sum(c[1] for c in done),
                keys_removed=sum(c[2] for c in done),
                skips=(*plan.skips, *report.skips),
                warnings=tuple(w for w in plan.warnings
                               if w.channel is None or w.channel in written),
                error=report.error,
                critical=report.critical,
            )
        self.set_status(tool_status(result), tool_kind(result))
        return result

    def convert_to_ease(self) -> bool:
        """Copy the current generator's evaluated curve into Ease as one local undo step."""
        source = self.current
        if source.kind == ShapeKind.EASE:
            return False
        curve = source.evaluated_curve()
        ease = self.documents[ShapeKind.EASE]
        ease.cancel_gesture()
        changed = ease.apply("Convert to Ease", ease.committed_content.with_curve(curve),
                             frozenset())
        self.switch_shape(ShapeKind.EASE)
        return changed

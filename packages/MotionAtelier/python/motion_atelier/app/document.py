# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Shape documents and local undo.

One :class:`ShapeDocument` per shape kind lives for the panel session. Its undoable content
is an immutable :class:`ShapeContent`; undo stores before/after snapshots of that immutable
data, so undo is exact and cheap. Gestures preview content without touching the undo stack;
releasing records exactly one command, cancelling restores the pre-gesture content and
records nothing. Selection changes are not undoable.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, replace

from motion_atelier.curve.model import Curve, Knot, Tangent
from motion_atelier.generators import registry
from motion_atelier.generators.base import GeneratorRecord, ShapeKind

# Local undo depth per shape. 200 gestures comfortably covers a working session on one
# shape; each entry holds two references to immutable content (a few kB at most).
# Local undo entries kept per shape.
MAX_UNDO_DEPTH = 100

# Default Ease: the "Sine S-Curve" factory recipe — flat ends with 36.19 % handles and a
# smooth middle knot of slope π/2 with 32.61 % handles.
DEFAULT_EASE_END_WEIGHT = 0.3619
DEFAULT_EASE_MID_WEIGHT = 0.3261


def default_ease_curve() -> Curve:
    we, wm, s = DEFAULT_EASE_END_WEIGHT, DEFAULT_EASE_MID_WEIGHT, math.pi / 2
    return Curve((Knot(0.0, 0.0, None, Tangent(0.0, we)),
                  Knot(0.5, 0.5, Tangent(s, wm), Tangent(s, wm)),
                  Knot(1.0, 1.0, Tangent(0.0, we), None)))


@dataclass(frozen=True, slots=True)
class ShapeContent:
    """Undoable content of a shape: an editable curve (Ease) or a generator record."""

    kind: ShapeKind
    curve: Curve | None = None
    record: GeneratorRecord | None = None
    effects: object | None = None  # an EffectStack (effects layer); None means no stack

    def __post_init__(self) -> None:
        if self.kind == ShapeKind.EASE:
            if self.curve is None or self.record is not None:
                raise ValueError("Ease content needs a curve and no generator record")
        elif self.record is None or self.record.kind != self.kind or self.curve is not None:
            raise ValueError("generator content needs a matching record and no curve")

    def with_curve(self, curve: Curve) -> ShapeContent:
        return replace(self, curve=curve)

    def with_record(self, record: GeneratorRecord) -> ShapeContent:
        return replace(self, record=record)

    def with_effects(self, effects) -> ShapeContent:
        return replace(self, effects=effects)


def default_content(kind: ShapeKind) -> ShapeContent:
    if kind == ShapeKind.EASE:
        return ShapeContent(kind, curve=default_ease_curve())
    return ShapeContent(kind, record=registry.default_record(kind))


@dataclass(frozen=True, slots=True)
class Command:
    label: str
    before: ShapeContent
    after: ShapeContent


class UndoStack:
    def __init__(self, depth: int = MAX_UNDO_DEPTH) -> None:
        self._commands: list[Command] = []
        self._index = 0  # number of applied commands
        self._depth = depth

    def push(self, command: Command) -> None:
        del self._commands[self._index :]
        self._commands.append(command)
        if len(self._commands) > self._depth:
            del self._commands[0]
        self._index = len(self._commands)

    @property
    def can_undo(self) -> bool:
        return self._index > 0

    @property
    def can_redo(self) -> bool:
        return self._index < len(self._commands)

    @property
    def undo_label(self) -> str | None:
        return self._commands[self._index - 1].label if self.can_undo else None

    @property
    def redo_label(self) -> str | None:
        return self._commands[self._index].label if self.can_redo else None

    def undo(self) -> Command | None:
        if not self.can_undo:
            return None
        self._index -= 1
        return self._commands[self._index]

    def redo(self) -> Command | None:
        if not self.can_redo:
            return None
        command = self._commands[self._index]
        self._index += 1
        return command

    def __len__(self) -> int:
        return len(self._commands)


class ShapeDocument:
    """Editable state of one shape kind, with its own local undo history."""

    def __init__(self, kind: ShapeKind, content: ShapeContent | None = None,
                 evaluator: Callable[[object, Curve], tuple[Curve, tuple]] | None = None,
                 ) -> None:
        self.kind = kind
        self._content = content if content is not None else default_content(kind)
        self._preview: ShapeContent | None = None
        self._gesture_start: ShapeContent | None = None
        self.selection: frozenset[int] = frozenset()
        self.undo_stack = UndoStack()
        self._evaluator = evaluator
        self._listeners: list[Callable[[ShapeDocument], None]] = []
        self._cache_key: ShapeContent | None = None
        self._cache_value: tuple[Curve, Curve] | None = None
        self._base_key: object | None = None
        self._base_value: tuple[Curve, tuple] = (None, ())  # type: ignore[assignment]
        self._partial: tuple[ShapeContent, int, Curve] | None = None
        self.notices: tuple = ()  # notices from the last evaluation (generator / effects)

    # -- observation -------------------------------------------------------------------------

    def subscribe(self, listener: Callable[[ShapeDocument], None]) -> None:
        self._listeners.append(listener)

    def unsubscribe(self, listener: Callable[[ShapeDocument], None]) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def _changed(self) -> None:
        for listener in list(self._listeners):
            listener(self)

    # -- content -----------------------------------------------------------------------------

    @property
    def content(self) -> ShapeContent:
        """Displayed content: the gesture preview while a gesture is in progress."""
        return self._preview if self._preview is not None else self._content

    @property
    def committed_content(self) -> ShapeContent:
        return self._content

    @property
    def in_gesture(self) -> bool:
        return self._gesture_start is not None

    def set_selection(self, selection) -> None:
        count = len(self.base_curve().knots)
        self.selection = frozenset(i for i in selection if 0 <= i < count)
        self._changed()

    def apply(self, label: str, content: ShapeContent, selection=None) -> bool:
        """Record one undoable edit. Returns False (and records nothing) if unchanged."""
        if self.in_gesture:
            raise RuntimeError("apply() during a gesture; use end_gesture()")
        if content == self._content:
            if selection is not None:
                self.set_selection(selection)
            return False
        self.undo_stack.push(Command(label, self._content, content))
        self._content = content
        if selection is not None:
            self.selection = frozenset(selection)
        self._clamp_selection()
        self._changed()
        return True

    # -- gestures ----------------------------------------------------------------------------

    def begin_gesture(self) -> None:
        self._gesture_start = self._content
        self._preview = self._content

    def update_gesture(self, content: ShapeContent, selection=None) -> None:
        if not self.in_gesture:
            raise RuntimeError("update_gesture() outside a gesture")
        self._preview = content
        if selection is not None:
            self.selection = frozenset(selection)
        self._changed()

    def end_gesture(self, label: str) -> bool:
        """Finish a gesture: one command if the content changed, nothing otherwise."""
        if not self.in_gesture:
            return False
        start, final = self._gesture_start, self._preview
        self._gesture_start = None
        self._preview = None
        assert start is not None and final is not None
        if final == start:
            self._changed()
            return False
        self.undo_stack.push(Command(label, start, final))
        self._content = final
        self._clamp_selection()
        self._changed()
        return True

    def cancel_gesture(self) -> None:
        if not self.in_gesture:
            return
        self._content = self._gesture_start  # type: ignore[assignment]
        self._gesture_start = None
        self._preview = None
        self._clamp_selection()
        self._changed()

    # -- undo --------------------------------------------------------------------------------

    def undo(self) -> str | None:
        if self.in_gesture:
            self.cancel_gesture()
        command = self.undo_stack.undo()
        if command is None:
            return None
        self._content = command.before
        self._clamp_selection()
        self._changed()
        return command.label

    def redo(self) -> str | None:
        if self.in_gesture:
            self.cancel_gesture()
        command = self.undo_stack.redo()
        if command is None:
            return None
        self._content = command.after
        self._clamp_selection()
        self._changed()
        return command.label

    def _clamp_selection(self) -> None:
        count = len(self.base_curve().knots)
        self.selection = frozenset(i for i in self.selection if 0 <= i < count)

    # -- evaluation --------------------------------------------------------------------------

    def _base(self) -> tuple[Curve, tuple]:
        """The base curve and its generator notices, without evaluating any effect (cheap:
        selection, inspector and toolbar queries never pay for the effect stack)."""
        content = self.content
        if content.curve is not None:
            return content.curve, ()
        record = content.record
        assert record is not None
        if self._base_key is not record:
            out = registry.build(record)
            self._base_key = record
            self._base_value = (out.curve, tuple(out.notices))
        return self._base_value

    def _evaluate(self) -> tuple[Curve, Curve]:
        content = self.content
        if self._cache_key is content and self._cache_value is not None:
            return self._cache_value
        base, base_notices = self._base()
        notices: list = list(base_notices)
        if self._evaluator is not None and content.effects is not None:
            evaluated, more = self._evaluator(content.effects, base)
            notices.extend(more)
        else:
            evaluated = base
        self._cache_key = content
        self._cache_value = (base, evaluated)
        self.notices = tuple(notices)
        return self._cache_value

    def base_curve(self) -> Curve:
        return self._base()[0]

    def evaluated_curve(self) -> Curve:
        return self._evaluate()[1]

    def evaluated_through(self, count: int | None) -> Curve:
        """Output of the first ``count`` effect layers: a viewing aid for inspecting the
        stack. Commits always use :meth:`evaluated_curve` (the full stack)."""
        effects = self.content.effects
        if (count is None or effects is None or self._evaluator is None
                or count >= len(effects.layers)):
            return self.evaluated_curve()
        base = self.base_curve()
        if count <= 0:
            return base
        content = self.content
        cached = self._partial
        if cached is not None and cached[0] is content and cached[1] == count:
            return cached[2]
        curve, _notices = self._evaluator(effects.with_layers(effects.layers[:count]), base)
        self._partial = (content, count, curve)
        return curve

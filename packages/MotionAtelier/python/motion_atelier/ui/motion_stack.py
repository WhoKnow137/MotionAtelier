# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""The Motion Stack tab: generator parameters, the effect
layer cards, and the knot disclosure with the numeric knot inspector.

Every change is a local undo step on the current shape's document; a slider drag is one step
previewed live. The scene never changes here.
"""

from __future__ import annotations

from collections.abc import Callable

from motion_atelier.app.document import ShapeDocument
from motion_atelier.app.strings import text
from motion_atelier.curve import ops
from motion_atelier.curve.errors import MotionAtelierError
from motion_atelier.effects.model import EffectLayer, EffectStack
from motion_atelier.effects.stack import effect_def, make_layer
from motion_atelier.generators import registry
from motion_atelier.generators.base import GeneratorRecord, ShapeKind
from motion_atelier.generators.params import ParamError, ParamSpec, with_values
from motion_atelier.ui import controls, presentation, qt, style
from motion_atelier.ui.qt import QtCore, QtGui, QtWidgets

Qt = QtCore.Qt


def _field_width(fmt: presentation.FieldFormat, metrics: QtGui.QFontMetrics) -> int:
    def shown(v: float) -> str:
        return str(int(round(v))) if fmt.decimals == 0 else f"{v:.{fmt.decimals}f}"
    widest = max(metrics.horizontalAdvance(shown(fmt.lo) + fmt.suffix),
                 metrics.horizontalAdvance(shown(fmt.hi) + fmt.suffix))
    return max(40, widest + 24)


class ParamRow(QtWidgets.QWidget):
    """Seek slider (soft range) + numeric field (hard range), kept in sync."""

    began = QtCore.Signal()
    previewed = QtCore.Signal(object)  # model value while dragging
    ended = QtCore.Signal()
    committed = QtCore.Signal(object)  # model value from the field

    def __init__(self, spec: ParamSpec, shown: presentation.Shown | None, value,
                 field_width: int | None = None, dark: bool | None = None, parent=None) -> None:
        super().__init__(parent)
        self.spec = spec
        self.fmt = presentation.field_format(spec, shown)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        f = self.fmt
        self.slider = controls.SeekSlider(f.lo, f.soft_hi, value * f.scale, self)
        is_dark = f.integer if dark is None else dark
        self.field = controls.NumberField(value * f.scale, f.lo, f.hi, decimals=f.decimals,
                                          step=f.step, suffix=f.suffix, dark=is_dark, parent=self)
        width = field_width or _field_width(f, self.field.fontMetrics())
        self.field.setFixedWidth(width)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.field)
        self.slider.pressed.connect(self.began)
        self.slider.seeked.connect(self._seeked)
        self.slider.released.connect(self.ended)
        self.field.valueChanged.connect(self._field_changed)

    def _seeked(self, shown_value: float) -> None:
        f = self.fmt
        rounded = round(shown_value) if f.decimals == 0 else round(shown_value, f.decimals)
        self.field.set_value(rounded, silent=True)
        self.previewed.emit(presentation.to_model(f, rounded, self.spec))

    def _field_changed(self, shown_value: float) -> None:
        self.slider.set_value(min(self.fmt.soft_hi, shown_value))
        self.committed.emit(presentation.to_model(self.fmt, shown_value, self.spec))

    def set_model_value(self, value) -> None:
        shown_value = value * self.fmt.scale
        if not self.field.hasFocus():
            self.field.set_value(shown_value, silent=True)
        self.slider.set_value(min(self.fmt.soft_hi, shown_value))


class GeneratorSection(QtWidgets.QWidget):
    """``<Shape> parameters`` heading, the parameter form and the note."""

    status = QtCore.Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 13, 0, 0)
        layout.setSpacing(0)
        self.title = controls.heading("", self)
        layout.addWidget(self.title)
        layout.addSpacing(22)
        self.form = QtWidgets.QGridLayout()
        self.form.setHorizontalSpacing(6)
        self.form.setVerticalSpacing(7)
        self.form.setColumnStretch(1, 1)
        layout.addLayout(self.form)
        layout.addSpacing(18)
        self.note = QtWidgets.QLabel(text("stack.generator_note"), self)
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.notice = QtWidgets.QLabel(self)
        self.notice.setWordWrap(True)
        self.notice.setObjectName("GeneratorNotice")
        layout.addWidget(self.notice)
        layout.addSpacing(6)
        self.rows: dict[str, ParamRow] = {}
        self._doc: ShapeDocument | None = None

    def set_document(self, doc: ShapeDocument | None) -> None:
        if self._doc is not None:
            self._doc.unsubscribe(self._on_doc)
        self._doc = doc
        while self.form.count():
            widget = self.form.takeAt(0).widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self.rows.clear()
        if doc is None or doc.kind == ShapeKind.EASE:
            return
        doc.subscribe(self._on_doc)
        shape = text(f"shape.{doc.kind.value}")
        self.title.setText(text("stack.generator_title", shape=shape))
        record = doc.content.record
        for n, spec in enumerate(registry.generator_for(doc.kind).params_schema):
            label = QtWidgets.QLabel(text(spec.label), self)
            row = ParamRow(spec, presentation.GENERATORS.get((doc.kind.value, spec.name)),
                           dict(record.params)[spec.name], parent=self)
            controls.set_tip(row, text(f"{spec.label}.tip"), text(spec.label))
            controls.set_tip(row.field, text(f"{spec.label}.tip"), text(spec.label))
            row.began.connect(self._begin)
            row.previewed.connect(lambda v, s=spec: self._preview(s, v))
            row.ended.connect(lambda s=spec: self._end(s))
            row.committed.connect(lambda v, s=spec: self._commit(s, v))
            self.form.addWidget(label, n, 0)
            self.form.addWidget(row, n, 1)
            self.rows[spec.name] = row
        self._on_doc(doc)

    def _on_doc(self, doc: ShapeDocument) -> None:
        record = doc.content.record
        if record is None:
            return
        for name, row in self.rows.items():
            row.set_model_value(dict(record.params)[name])
        doc.evaluated_curve()
        from motion_atelier.app.results import Issue
        from motion_atelier.app.status import issue_text
        self.notice.setText("\n".join(issue_text(Issue(n.code, n.detail)) for n in doc.notices))
        self.notice.setVisible(bool(doc.notices))

    def _record(self, spec: ParamSpec, value) -> GeneratorRecord:
        base = self._doc.committed_content.record
        gen = registry.generator_for(base.kind)
        return GeneratorRecord(base.kind, base.version,
                               with_values(gen.params_schema, base.params, **{spec.name: value}))

    def _changed_status(self) -> None:
        self.status.emit(text("status.generator_changed",
                              shape=text(f"shape.{self._doc.kind.value}")))

    def _begin(self) -> None:
        if self._doc is not None and not self._doc.in_gesture:
            self._doc.begin_gesture()

    def _preview(self, spec: ParamSpec, value) -> None:
        doc = self._doc
        if doc is None:
            return
        if not doc.in_gesture:
            doc.begin_gesture()
        try:
            doc.update_gesture(doc.committed_content.with_record(self._record(spec, value)))
        except ParamError:
            return

    def _end(self, spec: ParamSpec) -> None:
        doc = self._doc
        label = text("undo.param", param=text(spec.label))
        if doc is not None and doc.in_gesture and doc.end_gesture(label):
            self._changed_status()

    def _commit(self, spec: ParamSpec, value) -> None:
        doc = self._doc
        if doc is None or doc.in_gesture:
            return
        try:
            record = self._record(spec, value)
        except ParamError:
            return
        if doc.apply(text("undo.param", param=text(spec.label)),
                     doc.committed_content.with_record(record)):
            self._changed_status()


class LayerCard(QtWidgets.QFrame):
    """One effect layer: header, collapsible body, card menu."""

    def __init__(self, stack_section: StackSection, layer: EffectLayer, index: int,
                 count: int, expanded: bool, current: bool) -> None:
        super().__init__()
        self.section = stack_section
        self.layer = layer
        self.index = index
        self.setObjectName("LayerCard")
        self._current = current
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(8, 7, 8, 7)
        outer.setSpacing(7)
        head = QtWidgets.QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(4)
        self.expand = controls.ToolButton("chevron_down" if expanded else "chevron_right", self,
                                          small=True)
        controls.set_tip(self.expand, text("stack.card.expand.tip"),
                         text("stack.card.expand", name=layer.name))
        self.expand.clicked.connect(lambda: self.section.toggle_expanded(self.index))
        head.addWidget(self.expand)
        self.enable = controls.CheckBox("", self, checked=layer.enabled)
        controls.set_tip(self.enable, text("stack.card.enable.tip"),
                         text("stack.card.enable", name=layer.name))
        self.enable.toggled.connect(lambda on: self.section.set_enabled(self.index, on))
        head.addWidget(self.enable)
        self.grip = QtWidgets.QLabel("⠿", self)
        self.grip.setFixedWidth(10)
        self.grip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.grip.setCursor(Qt.CursorShape.OpenHandCursor)
        controls.set_tip(self.grip, text("stack.card.drag.tip"))
        self.grip.installEventFilter(self)
        head.addWidget(self.grip)
        self.name = controls.ElidedLabel(self)
        self.name.set_full_text(layer.name)
        self.name.setToolTip(text("stack.card.name.tip"))
        self.name.setMinimumWidth(40)
        head.addWidget(self.name, 1)
        self.mix = controls.NumberField(round(layer.mix * 100), 0, 100, decimals=0, step=1,
                                        suffix="%", dark=True, parent=self)
        self.mix.setFixedWidth(52)
        controls.set_tip(self.mix, text("stack.card.mix.tip"), text("stack.card.mix"))
        self.mix.valueChanged.connect(lambda v: self.section.set_mix(self.index, v / 100.0))
        head.addWidget(self.mix)
        outer.addLayout(head)
        self.body = QtWidgets.QWidget(self)
        body = QtWidgets.QVBoxLayout(self.body)
        body.setContentsMargins(4, 2, 2, 2)
        body.setSpacing(6)
        d = effect_def(layer.effect)
        self.rows: dict[str, ParamRow] = {}
        for spec in d.params:
            body.addWidget(QtWidgets.QLabel(text(spec.label), self.body))
            row = ParamRow(spec, presentation.EFFECTS.get((layer.effect, spec.name)),
                           dict(layer.params)[spec.name], field_width=68, dark=False,
                           parent=self.body)
            row.layout().setSpacing(8)
            controls.set_tip(row.field, text(f"{spec.label}.tip"), text(spec.label))
            controls.set_tip(row, text(f"{spec.label}.tip"), text(spec.label))
            row.began.connect(self.section.begin_drag)
            row.previewed.connect(lambda v, s=spec: self.section.preview_param(self.index,
                                                                               s.name, v))
            row.ended.connect(self.section.end_drag)
            row.committed.connect(lambda v, s=spec: self.section.set_param(self.index, s.name, v))
            body.addWidget(row)
            self.rows[spec.name] = row
        ends = QtWidgets.QHBoxLayout()
        ends.addWidget(QtWidgets.QLabel(text("stack.card.boundary"), self.body))
        ends.addStretch(1)
        preserve = QtWidgets.QLabel(text("stack.card.preserve"), self.body)
        controls.set_tip(preserve, text("stack.card.preserve.tip"), text("stack.card.preserve"))
        ends.addWidget(preserve)
        body.addLayout(ends)
        actions = QtWidgets.QHBoxLayout()
        actions.setSpacing(2)
        for icon, key, slot, enabled in (
            ("arrow_up", "up", lambda: self.section.move(self.index, -1), index > 0),
            ("arrow_down", "down", lambda: self.section.move(self.index, 1), index < count - 1),
            ("duplicate", "duplicate", lambda: self.section.duplicate(self.index), True),
            ("delete", "delete", lambda: self.section.delete(self.index), True),
        ):
            button = controls.ToolButton(icon, self.body)
            controls.set_tip(button, text(f"stack.card.{key}.tip"), text(f"stack.card.{key}"))
            button.setEnabled(enabled)
            button.clicked.connect(slot)
            actions.addWidget(button)
        actions.addStretch(1)
        body.addLayout(actions)
        self.body.setVisible(expanded)
        outer.addWidget(self.body)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)

    def paintEvent(self, _event) -> None:
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.setPen(QtGui.QPen(QtGui.QColor(style.MID), 1))
        p.setBrush(QtGui.QColor(style.ALT_BASE))
        p.drawRoundedRect(QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
        p.end()

    def mousePressEvent(self, event) -> None:
        self.section.select(self.index)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        self.section.expand_only(self.index)

    def eventFilter(self, obj, event) -> bool:
        if obj is self.grip and event.type() == QtCore.QEvent.Type.MouseButtonPress \
                and event.button() == Qt.MouseButton.LeftButton:
            self.section.start_drag(self.index)
            return True
        return super().eventFilter(obj, event)

    def _menu(self, pos: QtCore.QPoint) -> None:
        self.section.select(self.index)
        menu = QtWidgets.QMenu(self)
        menu.addAction(text("stack.menu.rename"), lambda: self.section.rename(self.index))
        menu.addAction(text("stack.menu.duplicate"), lambda: self.section.duplicate(self.index))
        menu.addAction(text("stack.menu.reset"), lambda: self.section.reset(self.index))
        menu.addSeparator()
        menu.addAction(text("stack.menu.delete"), lambda: self.section.delete(self.index))
        menu.exec(self.mapToGlobal(pos))
        menu.deleteLater()


class _DropLine(QtWidgets.QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(2)

    def paintEvent(self, _event) -> None:
        p = QtGui.QPainter(self)
        p.fillRect(self.rect().adjusted(3, 0, -3, 0), QtGui.QColor(style.HIGHLIGHT))
        p.end()


class _StackList(QtWidgets.QFrame):
    def paintEvent(self, _event) -> None:
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), QtGui.QColor(style.BASE))
        p.setPen(QtGui.QColor(style.LIST_BORDER))
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        p.end()


class StackSection(QtWidgets.QWidget):
    """``Motion Stack`` heading and count, the layer list, and Add Effect."""

    status = QtCore.Signal(str)
    layersShown = QtCore.Signal(object)
    renameRequested = QtCore.Signal(str, object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        row, self.count = controls.heading_row(text("stack.heading"), self)
        layout.addLayout(row)
        self.list = _StackList(self)
        self.list.setObjectName("EffectLayers")
        self.list.setAccessibleName(text("stack.list"))
        self.list_layout = QtWidgets.QVBoxLayout(self.list)
        self.list_layout.setContentsMargins(3, 3, 3, 3)
        self.list_layout.setSpacing(6)
        layout.addWidget(self.list)
        layout.addSpacing(4)
        self.add = controls.PushButton(text("stack.add"), "plus", self)
        self.add.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                               QtWidgets.QSizePolicy.Policy.Fixed)
        controls.set_tip(self.add, text("stack.add.tip"), text("stack.add"))
        self.add.clicked.connect(self._add_menu)
        layout.addWidget(self.add)
        self._doc: ShapeDocument | None = None
        self._current: dict[ShapeKind, int] = {}
        self._expanded: dict[ShapeKind, set[int]] = {}
        self.cards: list[LayerCard] = []
        self._drag_from: int | None = None
        self._drop_line = _DropLine()
        self._refreshing = False
        self._built_for: tuple | None = None

    # document
    def set_document(self, doc: ShapeDocument | None) -> None:
        if self._doc is not None:
            self._doc.unsubscribe(self._on_doc)
        self._doc = doc
        if doc is not None:
            doc.subscribe(self._on_doc)
        self.rebuild()

    def _on_doc(self, _doc) -> None:
        # Rebuild the cards only when the stack itself changed (undo, redo, loads); selection
        # changes and gestures leave them alone.
        if self._refreshing or (self._doc and self._doc.in_gesture):
            return
        if self._built_for != (self._doc.kind, self.stack()):
            self.rebuild()

    def stack(self) -> EffectStack:
        effects = self._doc.committed_content.effects if self._doc is not None else None
        return effects if effects is not None else EffectStack()

    def current(self) -> int:
        if self._doc is None:
            return -1
        layers = len(self.stack().layers)
        value = self._current.get(self._doc.kind, layers - 1)
        return min(value, layers - 1)

    def shown_layers(self) -> int | None:
        layers = len(self.stack().layers)
        current = self.current()
        return None if current < 0 or current >= layers - 1 else current + 1

    def rebuild(self) -> None:
        for card in self.cards:
            card.hide()
            card.setParent(None)
            card.deleteLater()
        self.cards = []
        while self.list_layout.count():
            self.list_layout.takeAt(0)
        stack = self.stack()
        n = len(stack.layers)
        kind = self._doc.kind if self._doc is not None else None
        expanded = self._expanded.get(kind, set())
        current = self.current()
        for i, layer in enumerate(stack.layers):
            card = LayerCard(self, layer, i, n, i in expanded, i == current)
            self.list_layout.addWidget(card)
            self.cards.append(card)
        self._built_for = (kind, stack)
        self.list.setVisible(n > 0)
        self.count.setText(text("stack.count.one") if n == 1 else text("stack.count", count=n))
        self.layersShown.emit(self.shown_layers())

    # selection and expansion
    def select(self, index: int) -> None:
        if self._doc is None:
            return
        if self._current.get(self._doc.kind) != index:
            self._current[self._doc.kind] = index
            self.layersShown.emit(self.shown_layers())

    def toggle_expanded(self, index: int) -> None:
        exp = self._expanded.setdefault(self._doc.kind, set())
        exp.symmetric_difference_update({index})
        self.rebuild()

    def expand_only(self, index: int) -> None:
        exp = self._expanded.setdefault(self._doc.kind, set())
        on = index not in exp
        exp.clear()
        if on:
            exp.add(index)
        self.rebuild()

    # structural edits
    def _apply(self, label_key: str, stack: EffectStack, current: int | None = None) -> None:
        doc = self._doc
        if doc is None:
            return
        if current is not None:
            self._current[doc.kind] = current
        content = doc.committed_content.with_effects(stack if stack.layers else None)
        self._refreshing = True
        try:
            doc.apply(text(label_key), content)
        finally:
            self._refreshing = False
        self.rebuild()
        self.status.emit(text("status.stack", count=len(stack.layers)))

    def _shift_expanded(self, mapping: Callable[[int], int | None]) -> None:
        kind = self._doc.kind
        exp = self._expanded.get(kind, set())
        self._expanded[kind] = {j for j in (mapping(i) for i in exp) if j is not None}

    def _add_menu(self) -> None:
        menu = QtWidgets.QMenu(self)
        for effect in presentation.EFFECT_MENU_ORDER:
            menu.addAction(text(f"effect.{effect}"), lambda e=effect: self.add_layer(e))
        menu.exec(self.add.mapToGlobal(QtCore.QPoint(0, self.add.height())))
        menu.deleteLater()

    def add_layer(self, effect: str) -> None:
        stack = self.stack()
        layer = make_layer(effect, text(f"effect.{effect}"))
        self._apply("undo.effect_add", stack.insert(len(stack.layers), layer), len(stack.layers))

    def duplicate(self, index: int) -> None:
        stack = self.stack()
        self._shift_expanded(lambda i: i if i <= index else i + 1)
        self._apply("undo.effect_duplicate", stack.insert(index + 1, stack.layers[index]),
                    self.current())

    def delete(self, index: int) -> None:
        stack = self.stack()
        self._shift_expanded(lambda i: None if i == index else (i if i < index else i - 1))
        current = min(self.current(), len(stack.layers) - 2)
        self._apply("undo.effect_remove", stack.remove(index), current)

    def move(self, index: int, delta: int) -> None:
        stack = self.stack()
        to = index + delta
        if 0 <= to < len(stack.layers):
            self.move_to(index, to)

    def move_to(self, index: int, to: int) -> None:
        """A moved layer stays expanded and becomes the current one."""
        stack = self.stack()
        exp = self._expanded.setdefault(self._doc.kind, set())
        order = list(range(len(stack.layers)))
        order.insert(to, order.pop(index))
        self._expanded[self._doc.kind] = {order.index(i) for i in exp} | {to}
        self._apply("undo.effect_move", stack.move(index, to), to)

    def set_enabled(self, index: int, on: bool) -> None:
        stack = self.stack()
        layer = stack.layers[index]
        if layer.enabled != on:
            self._apply("undo.effect_enable", stack.replace_layer(index, layer.with_(enabled=on)))

    def set_mix(self, index: int, mix: float) -> None:
        stack = self.stack()
        layer = stack.layers[index]
        if layer.mix != mix:
            self._apply("undo.effect_param", stack.replace_layer(index, layer.with_(mix=mix)))

    def _with_param(self, index: int, name: str, value) -> EffectStack:
        stack = self.stack()
        layer = stack.layers[index]
        d = effect_def(layer.effect)
        return stack.replace_layer(index, layer.with_(
            params=with_values(d.params, layer.params, **{name: value})))

    def set_param(self, index: int, name: str, value) -> None:
        try:
            stack = self._with_param(index, name, value)
        except ParamError:
            return
        self._apply("undo.effect_param", stack)

    def begin_drag(self) -> None:
        if self._doc is not None and not self._doc.in_gesture:
            self._doc.begin_gesture()

    def preview_param(self, index: int, name: str, value) -> None:
        doc = self._doc
        if doc is None:
            return
        if not doc.in_gesture:
            doc.begin_gesture()
        try:
            stack = self._with_param(index, name, value)
        except ParamError:
            return
        doc.update_gesture(doc.committed_content.with_effects(stack))

    def end_drag(self) -> None:
        doc = self._doc
        if doc is not None and doc.in_gesture:
            self._refreshing = True
            try:
                changed = doc.end_gesture(text("undo.effect_param"))
            finally:
                self._refreshing = False
            if changed:
                self.status.emit(text("status.stack", count=len(self.stack().layers)))

    def reset(self, index: int) -> None:
        stack = self.stack()
        layer = stack.layers[index]
        fresh = make_layer(layer.effect, layer.name).with_(enabled=layer.enabled)
        self._apply("undo.effect_reset", stack.replace_layer(index, fresh))

    def rename(self, index: int) -> None:
        layer = self.stack().layers[index]

        def done(name: str) -> None:
            stack = self.stack()
            if name and name != stack.layers[index].name:
                self._apply("undo.effect_rename",
                            stack.replace_layer(index, stack.layers[index].with_(name=name)))

        self.renameRequested.emit(layer.name, done)

    # drag reorder by the handle: a 2 px highlight line marks the drop point
    def start_drag(self, index: int) -> None:
        self.select(index)
        self._drag_from = index
        self.cards[index].setWindowOpacity(0.35)
        self.grabMouse(Qt.CursorShape.ClosedHandCursor)

    def _target(self, global_pos: QtCore.QPoint) -> int:
        for i, card in enumerate(self.cards):
            top = card.mapToGlobal(QtCore.QPoint(0, 0)).y()
            if global_pos.y() < top + card.height() / 2:
                return i
        return len(self.cards)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_from is None:
            return
        target = self._target(qt.global_pos(event).toPoint())
        self.list_layout.removeWidget(self._drop_line)
        self._drop_line.setParent(self.list)
        self.list_layout.insertWidget(self._layout_index(target), self._drop_line)
        self._drop_line.show()

    def _layout_index(self, target: int) -> int:
        if target >= len(self.cards):
            return self.list_layout.count()
        return self.list_layout.indexOf(self.cards[target])

    def mouseReleaseEvent(self, event) -> None:
        if self._drag_from is None:
            return
        self.releaseMouse()
        index = self._drag_from
        self._drag_from = None
        target = self._target(qt.global_pos(event).toPoint())
        self.list_layout.removeWidget(self._drop_line)
        self._drop_line.hide()
        to = target - 1 if target > index else target
        if to != index:
            self.move_to(index, to)
        else:
            self.rebuild()


class KnotDisclosure(QtWidgets.QWidget):
    """Collapsible numeric knot inspector for Ease."""

    status = QtCore.Signal(str)

    ROWS = (("x", "knot.time", 6, None), ("y", "knot.value", 6, None),
            ("in_slope", "knot.in_slope", 6, None), ("in_weight", "knot.in_influence", 3, " %"),
            ("out_slope", "knot.out_slope", 6, None), ("out_weight", "knot.out_influence", 3,
                                                        " %"))

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        head = QtWidgets.QHBoxLayout()
        head.setSpacing(4)
        self.toggle = controls.PushButton(text("knot.none"), "chevron_right", self, left=True,
                                          flat=True, icon_size=style.ICON_SMALL)
        self.toggle.setToolTip(text("knot.toggle.tip"))
        self.toggle.clicked.connect(lambda: self.set_expanded(not self.expanded))
        head.addWidget(self.toggle, 1)
        self.position = QtWidgets.QLabel(self)
        head.addWidget(self.position)
        layout.addLayout(head)
        self.body = QtWidgets.QWidget(self)
        body = QtWidgets.QVBoxLayout(self.body)
        body.setContentsMargins(0, 2, 0, 0)
        body.setSpacing(6)
        self.hint = QtWidgets.QLabel(text("knot.hint"), self.body)
        self.hint.setWordWrap(True)
        body.addWidget(self.hint)
        self.form = QtWidgets.QGridLayout()
        self.form.setHorizontalSpacing(6)
        self.form.setVerticalSpacing(8)
        self.form.setColumnStretch(1, 1)
        self.fields: dict[str, controls.NumberField] = {}
        self.labels: dict[str, QtWidgets.QLabel] = {}
        for row, (key, label_key, decimals, suffix) in enumerate(self.ROWS):
            label = QtWidgets.QLabel(text(label_key), self.body)
            lo, hi = (0.0, 100.0) if suffix else (-1e6, 1e6)
            field = controls.NumberField(0.0, lo, hi, decimals=decimals,
                                         step=1.0 if suffix else 0.01, suffix=suffix or "",
                                         parent=self.body)
            field.setMinimumWidth(64)
            field.setAccessibleName(text(label_key))
            field.setToolTip(text(f"{label_key}.tip"))
            field.valueChanged.connect(lambda _v, k=key: self._edited(k))
            self.form.addWidget(label, row, 0)
            self.form.addWidget(field, row, 1)
            self.fields[key] = field
            self.labels[key] = label
        self.continuity = controls.Combo([text("knot.smooth"), text("knot.broken")], self.body,
                                         fill=True)
        controls.set_tip(self.continuity, text("knot.continuity.tip"), text("knot.continuity"))
        self.continuity.currentIndexChanged.connect(lambda _i: self._edited("continuity"))
        self.labels["continuity"] = QtWidgets.QLabel(text("knot.continuity"), self.body)
        self.form.addWidget(self.labels["continuity"], len(self.ROWS), 0)
        self.form.addWidget(self.continuity, len(self.ROWS), 1)
        body.addLayout(self.form)
        self.error = QtWidgets.QLabel(self.body)
        self.error.setWordWrap(True)
        self.error.setStyleSheet(f"color: {style.HIGHLIGHTED_TEXT};")
        body.addWidget(self.error)
        layout.addWidget(self.body)
        self.expanded = False
        self.body.hide()
        self._doc: ShapeDocument | None = None
        self._index: int | None = None
        self._syncing = False

    def set_expanded(self, on: bool) -> None:
        self.expanded = on
        self.body.setVisible(on)
        self.toggle.set_icon("chevron_down" if on else "chevron_right")

    def set_document(self, doc: ShapeDocument | None) -> None:
        if self._doc is not None:
            self._doc.unsubscribe(self._on_doc)
        self._doc = doc if doc is not None and doc.kind == ShapeKind.EASE else None
        if self._doc is not None:
            self._doc.subscribe(self._on_doc)
        self.sync()

    def _on_doc(self, _doc) -> None:
        self.sync()

    def selected(self) -> int | None:
        doc = self._doc
        if doc is None or len(doc.selection) != 1:
            return None
        return next(iter(doc.selection))

    def sync(self) -> None:
        doc = self._doc
        i = self.selected()
        self._index = i
        self.error.clear()
        if doc is None:
            return
        curve = doc.base_curve()
        last = len(curve.knots) - 1
        role = ("knot.none" if i is None else "knot.start" if i == 0
                else "knot.end" if i == last else "knot.knot")
        self.toggle.setText(text(role))
        self.toggle.setAccessibleName(text("knot.toggle", role=text(role)))
        self.position.setText("" if i is None else text("knot.position", index=i + 1,
                                                        count=last + 1))
        self.hint.setVisible(i is None)
        enabled = i is not None and not doc.in_gesture
        for field in self.fields.values():
            field.setEnabled(enabled)
        self.continuity.setEnabled(enabled)
        if i is None:
            for key in ("in_slope", "in_weight", "out_slope", "out_weight"):
                self.fields[key].setVisible(True)
                self.labels[key].setVisible(True)
            return
        k = curve.knots[i]
        has_in, has_out = i > 0, i < last
        self._syncing = True
        try:
            values = {"x": k.x, "y": k.y,
                      "in_slope": k.tin.slope if k.tin else 0.0,
                      "in_weight": (k.tin.weight if k.tin else 0.0) * 100,
                      "out_slope": k.tout.slope if k.tout else 0.0,
                      "out_weight": (k.tout.weight if k.tout else 0.0) * 100}
            for key, value in values.items():
                self.fields[key].set_value(value, silent=True)
            self.continuity.set_current_index(0 if k.smooth else 1, silent=True)
        finally:
            self._syncing = False
        self.fields["x"].setEnabled(enabled and has_in and has_out)
        self.continuity.setEnabled(enabled and has_in and has_out)
        for key, visible in (("in_slope", has_in), ("in_weight", has_in), ("out_slope", has_out),
                             ("out_weight", has_out)):
            self.fields[key].setVisible(visible)
            self.labels[key].setVisible(visible)
        self.fields["out_slope"].setEnabled(enabled and not (k.smooth and has_in and has_out))

    def focus_first(self) -> None:
        field = self.fields["x"] if self.fields["x"].isEnabled() else self.fields["y"]
        field.setFocus(Qt.FocusReason.OtherFocusReason)
        field.selectAll()

    def _edited(self, key: str) -> None:
        doc, i = self._doc, self._index
        if self._syncing or doc is None or i is None or doc.in_gesture:
            return
        curve = doc.base_curve()
        f = self.fields
        last = len(curve.knots) - 1
        try:
            if key == "x":
                if not 0 < i < last:
                    return  # endpoint times are fixed
                if not curve.knots[i - 1].x < f["x"].value() < curve.knots[i + 1].x:
                    self.error.setText(text("knot.time_between"))
                    self.sync_values_only()
                    return
                result = ops.set_knot(curve, i, x=f["x"].value()).curve
            elif key == "y":
                result = ops.set_knot(curve, i, y=f["y"].value()).curve
            elif key == "continuity":
                result = ops.set_continuity(curve, [i], self.continuity.current_index() == 0).curve
            elif key.endswith("slope"):
                result = ops.set_tangent(curve, i, key.split("_")[0], slope=f[key].value()).curve
            else:
                result = ops.set_tangent(curve, i, key.split("_")[0],
                                         weight=f[key].value() / 100.0).curve
        except MotionAtelierError as exc:
            self.error.setText(text("knot.rejected", reason=text(f"curve_error.{exc.code}")))
            self.sync_values_only()
            return
        self.error.clear()
        if doc.apply(text("undo.edit_knot"), doc.content.with_curve(result), {i}):
            self.status.emit(text("status.knot_edited"))

    def sync_values_only(self) -> None:
        error = self.error.text()
        self.sync()
        self.error.setText(error)


class MotionStackPage(QtWidgets.QWidget):
    """Page 1 of the inspector."""

    status = QtCore.Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("MotionStackPage")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        self.generator = GeneratorSection(self)
        layout.addWidget(self.generator)
        self.params_rule = controls.Rule(style.TEXT, self)
        layout.addWidget(self.params_rule)
        self.stack = StackSection(self)
        layout.addWidget(self.stack)
        self.knot_rule = controls.Rule(style.TEXT, self)
        layout.addSpacing(-5)
        layout.addWidget(self.knot_rule)
        self.knot = KnotDisclosure(self)
        layout.addWidget(self.knot)
        layout.addStretch(1)
        for child in (self.generator, self.stack, self.knot):
            child.status.connect(self.status)

    def set_document(self, doc: ShapeDocument | None) -> None:
        ease = doc is not None and doc.kind == ShapeKind.EASE
        self.generator.set_document(doc)
        self.generator.setVisible(not ease and doc is not None)
        self.params_rule.setVisible(not ease and doc is not None)
        self.stack.set_document(doc)
        self.knot.set_document(doc)
        self.knot_rule.setVisible(ease)
        self.knot.setVisible(ease)

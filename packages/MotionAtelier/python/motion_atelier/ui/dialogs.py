# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Dialogs: modal over a 25 % black shade, a 30 px title
bar with a close button that turns red on hover, 11 px body padding, a form layout and right
aligned buttons at least 75 px wide. Enter accepts and Escape cancels."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from motion_atelier.app.strings import text
from motion_atelier.ui import controls, qt, style
from motion_atelier.ui.qt import QtCore, QtGui, QtWidgets

Qt = QtCore.Qt


class _Shade(QtWidgets.QWidget):
    """A translucent black layer over the panel while a dialog is open."""

    def __init__(self, parent: QtWidgets.QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setGeometry(parent.rect())
        self.show()
        self.raise_()

    def paintEvent(self, _event) -> None:
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), QtGui.QColor(0, 0, 0, style.DIALOG_SHADE_ALPHA))
        p.end()


class _TitleBar(QtWidgets.QWidget):
    def __init__(self, title: str, dialog: QtWidgets.QDialog) -> None:
        super().__init__(dialog)
        self.dialog = dialog
        self.setFixedHeight(style.DIALOG_TITLE_H)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 4, 0)
        self.label = QtWidgets.QLabel(title, self)
        layout.addWidget(self.label, 1)
        self.close_button = _CloseButton(self)
        self.close_button.clicked.connect(dialog.reject)
        layout.addWidget(self.close_button)
        self._press: QtCore.QPoint | None = None

    def paintEvent(self, _event) -> None:
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), QtGui.QColor(style.DIALOG_TITLE))
        p.end()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = qt.global_pos(event).toPoint() - self.dialog.pos()

    def mouseMoveEvent(self, event) -> None:
        if self._press is not None:
            self.dialog.move(qt.global_pos(event).toPoint() - self._press)

    def mouseReleaseEvent(self, _event) -> None:
        self._press = None


class _CloseButton(controls._Hover, QtWidgets.QAbstractButton):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._init_hover()
        self.setFixedSize(28, 22)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAccessibleName(text("dialog.close"))
        self.setToolTip(text("dialog.close"))

    def paintEvent(self, _event) -> None:
        p = QtGui.QPainter(self)
        if self._hover:
            p.fillRect(self.rect(), QtGui.QColor(style.DIALOG_CLOSE_HOVER))
        p.setPen(QtGui.QColor("#ffffff" if self._hover else "#bbbbbb"))
        font = QtGui.QFont(self.font())
        font.setPointSizeF(font.pointSizeF() * 1.25)
        p.setFont(font)
        p.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter), "✕")
        p.end()


class FramedDialog(QtWidgets.QDialog):
    """Base dialog: title bar, body, OK / Cancel aligned right."""

    def __init__(self, title: str, parent: QtWidgets.QWidget | None, *, ok_text: str | None = None,
                 cancel: bool = True) -> None:
        super().__init__(parent, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("MAFramedDialog")
        self.setWindowTitle(title)
        self.setAccessibleName(title)
        self.setModal(True)
        style.apply(self)
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)
        outer.setSpacing(0)
        self.title_bar = _TitleBar(title, self)
        outer.addWidget(self.title_bar)
        self.body = QtWidgets.QWidget(self)
        self.body_layout = QtWidgets.QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(style.DIALOG_PAD, style.DIALOG_PAD, style.DIALOG_PAD,
                                            style.DIALOG_PAD)
        self.body_layout.setSpacing(10)
        outer.addWidget(self.body)
        self.buttons = QtWidgets.QHBoxLayout()
        self.buttons.setSpacing(6)
        self.buttons.addStretch(1)
        self.ok = controls.PushButton(ok_text or text("dialog.ok"), None, self)
        self.ok.setMinimumWidth(style.DIALOG_BUTTON_MIN)
        self.ok.set_fixed_width(max(style.DIALOG_BUTTON_MIN, self.ok.natural_width()))
        self.ok.setAccessibleName(self.ok.text())
        self.ok.clicked.connect(self.accept)
        self.buttons.addWidget(self.ok)
        self.cancel_button = None
        if cancel:
            self.cancel_button = controls.PushButton(text("dialog.cancel"), None, self)
            self.cancel_button.set_fixed_width(max(style.DIALOG_BUTTON_MIN,
                                                   self.cancel_button.natural_width()))
            self.cancel_button.setAccessibleName(text("dialog.cancel"))
            self.cancel_button.clicked.connect(self.reject)
            self.buttons.addWidget(self.cancel_button)
        self._can_accept: Callable[[], bool] = lambda: True
        self._shade: _Shade | None = None

    def finish_layout(self) -> None:
        self.body_layout.addLayout(self.buttons)
        self.sync()

    def set_accept_rule(self, rule: Callable[[], bool]) -> None:
        self._can_accept = rule
        self.sync()

    def sync(self) -> None:
        self.ok.setEnabled(bool(self._can_accept()))

    def paintEvent(self, _event) -> None:
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), QtGui.QColor(style.WINDOW))
        p.setPen(QtGui.QColor(style.TOOLTIP_BORDER))
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        p.end()

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            focus = QtWidgets.QApplication.focusWidget()
            if isinstance(focus, controls.Combo):
                focus.show_popup()
                return
            if isinstance(focus, controls.NumberField):
                focus.commit()
            if self.ok.isEnabled():
                self.accept()
            return
        super().keyPressEvent(event)

    def accept(self) -> None:
        if self._can_accept():
            super().accept()

    def run(self, shade_over: QtWidgets.QWidget | None) -> bool:
        """Show modally, centred over ``shade_over`` (shaded), and return acceptance."""
        if shade_over is not None:
            self._shade = _Shade(shade_over)
            self.adjustSize()
            center = shade_over.mapToGlobal(shade_over.rect().center())
            self.move(center - QtCore.QPoint(self.width() // 2, self.height() // 2))
        try:
            return qt.exec_dialog(self) == QtWidgets.QDialog.DialogCode.Accepted
        finally:
            if self._shade is not None:
                self._shade.hide()
                self._shade.deleteLater()
                self._shade = None


def form(rows: Sequence[tuple[str, QtWidgets.QWidget]], parent: QtWidgets.QWidget
         ) -> QtWidgets.QGridLayout:
    grid = QtWidgets.QGridLayout()
    grid.setHorizontalSpacing(6)
    grid.setVerticalSpacing(8)
    grid.setColumnStretch(1, 1)
    for n, (label, widget) in enumerate(rows):
        grid.addWidget(QtWidgets.QLabel(label, parent), n, 0)
        grid.addWidget(widget, n, 1)
    return grid


class TextPrompt(FramedDialog):
    """A label above a dark 240 px line edit; OK is disabled while the text is empty."""

    def __init__(self, title: str, label: str, initial: str, parent, *,
                 allow_empty: bool = False) -> None:
        super().__init__(title, parent)
        box = QtWidgets.QVBoxLayout()
        box.setSpacing(6)
        box.addWidget(QtWidgets.QLabel(label, self.body))
        self.field = QtWidgets.QLineEdit(initial, self.body)
        self.field.setProperty("prompt", "true")
        self.field.setFixedWidth(style.PROMPT_FIELD_W)
        self.field.setAccessibleName(label)
        self.field.textChanged.connect(lambda _t: self.sync())
        box.addWidget(self.field)
        self.body_layout.addLayout(box)
        self.set_accept_rule(lambda: allow_empty or bool(self.field.text().strip()))
        self.finish_layout()
        self.field.selectAll()
        self.field.setFocus()

    def value(self) -> str:
        return self.field.text().strip()


class Confirm(FramedDialog):
    def __init__(self, title: str, message: str, parent) -> None:
        super().__init__(title, parent, ok_text=text("dialog.yes"))
        label = QtWidgets.QLabel(message, self.body)
        label.setWordWrap(True)
        label.setMaximumWidth(340)
        self.body_layout.addWidget(label)
        self.finish_layout()


def spin(value: float, decimals: int, suffix: str, lo: float = -1e6, hi: float = 1e6,
         step: float = 1.0, width: int = 150) -> controls.SpinBox:
    return controls.SpinBox(value, lo, hi, decimals=decimals, step=step, suffix=suffix,
                            width=width)


class ScaleDialog(FramedDialog):
    def __init__(self, parent) -> None:
        super().__init__(text("tool.scale"), parent)
        self.time = spin(100.0, 1, " %", -100000, 100000, 5)
        self.value = spin(100.0, 1, " %", -100000, 100000, 5)
        controls.set_tip(self.time, text("tool.scale.time.tip"), text("tool.scale.time"))
        controls.set_tip(self.value, text("tool.scale.value.tip"), text("tool.scale.value"))
        self.time_pivot = controls.Combo([text("tool.pivot.first"), text("tool.pivot.center"),
                                          text("tool.pivot.playhead"), text("tool.pivot.last")],
                                         self.body, fill=True, index=1)
        self.time_pivot.set_items(self.time_pivot.items(), ["first", "center", "playhead", "last"])
        controls.set_tip(self.time_pivot, text("tool.scale.time_pivot.tip"),
                         text("tool.scale.time_pivot"))
        self.value_pivot = controls.Combo([text("tool.pivot.minimum"), text("tool.pivot.center"),
                                           text("tool.pivot.zero"), text("tool.pivot.maximum")],
                                          self.body, fill=True, index=1)
        self.value_pivot.set_items(self.value_pivot.items(), ["minimum", "center", "zero",
                                                              "maximum"])
        controls.set_tip(self.value_pivot, text("tool.scale.value_pivot.tip"),
                         text("tool.scale.value_pivot"))
        self.lock = controls.CheckBox(text("tool.scale.lock"), self.body, checked=False)
        controls.set_tip(self.lock, text("tool.scale.lock.tip"), text("tool.scale.lock"))
        self.time.valueChanged.connect(self._time_changed)
        self.value.valueChanged.connect(self._value_changed)
        self.body_layout.addLayout(form([
            (text("tool.scale.time"), self.time), (text("tool.scale.value"), self.value),
            (text("tool.scale.time_pivot"), self.time_pivot),
            (text("tool.scale.value_pivot"), self.value_pivot), ("", self.lock)], self.body))
        self.set_accept_rule(lambda: self.time.value() != 0)
        self.finish_layout()

    def _time_changed(self, v: float) -> None:
        if self.lock.isChecked():
            self.value.set_value(v, silent=True)
        self.sync()

    def _value_changed(self, v: float) -> None:
        if self.lock.isChecked():
            self.time.set_value(v, silent=True)
        self.sync()

    def options(self) -> dict:
        return {"time_scale": self.time.value() / 100.0, "value_scale": self.value.value() / 100.0,
                "time_pivot": self.time_pivot.current_data(),
                "value_pivot": self.value_pivot.current_data()}


class OffsetDialog(FramedDialog):
    def __init__(self, parent) -> None:
        super().__init__(text("tool.offset_channels"), parent)
        self.offset = spin(3.0, 3, " f")
        self.offset.setAccessibleName(text("tool.offset.offset"))
        self.order = controls.Combo([text("tool.offset.order.listed"),
                                     text("tool.offset.order.reverse")], self.body, fill=True)
        self.order.set_items(self.order.items(), ["listed", "reverse"])
        controls.set_tip(self.order, text("tool.offset.order.tip"), text("tool.offset.order"))
        self.body_layout.addLayout(form([(text("tool.offset.offset"), self.offset),
                                         (text("tool.offset.order"), self.order)], self.body))
        self.finish_layout()

    def options(self) -> dict:
        return {"frames": self.offset.value(), "order": self.order.current_data()}


class ShiftDialog(FramedDialog):
    def __init__(self, parent) -> None:
        super().__init__(text("tool.shift_following"), parent)
        self.delta = spin(10.0, 3, " f")
        self.delta.setAccessibleName(text("tool.shift.delta"))
        self.body_layout.addLayout(form([
            (text("tool.shift.direction"), QtWidgets.QLabel(text("tool.shift.after"), self.body)),
            (text("tool.shift.delta"), self.delta)], self.body))
        self.finish_layout()

    def options(self) -> dict:
        return {"frames": self.delta.value()}


class MatchDialog(FramedDialog):
    def __init__(self, parent) -> None:
        super().__init__(text("tool.match_durations"), parent)
        self.target = controls.Combo([text("tool.match.active"), text("tool.match.longest"),
                                      text("tool.match.shortest"), text("tool.match.custom")],
                                     self.body, fill=True)
        self.target.set_items(self.target.items(), ["first", "longest", "shortest", "frames"])
        controls.set_tip(self.target, text("tool.match.target.tip"), text("tool.match.target"))
        self.anchor = controls.Combo([text("tool.match.start"), text("tool.match.end"),
                                      text("tool.match.center")], self.body, fill=True)
        self.anchor.set_items(self.anchor.items(), ["start", "end", "center"])
        self.anchor.setAccessibleName(text("tool.match.anchor"))
        self.custom = spin(30.0, 3, " f", 0.001, 1e6)
        self.custom.setAccessibleName(text("tool.match.custom"))
        self.custom.setEnabled(False)
        self.target.currentIndexChanged.connect(lambda i: self.custom.setEnabled(i == 3))
        self.body_layout.addLayout(form([(text("tool.match.target"), self.target),
                                         (text("tool.match.anchor"), self.anchor),
                                         (text("tool.match.custom"), self.custom)], self.body))
        self.finish_layout()

    def options(self) -> dict:
        return {"target": self.target.current_data(), "anchor": self.anchor.current_data(),
                "frames": self.custom.value()}


class ReduceDialog(FramedDialog):
    def __init__(self, parent) -> None:
        super().__init__(text("tool.reduce"), parent, ok_text=text("tool.reduce"))
        self.error = spin(0.8, 3, "%", 0.001, 100.0, 0.1)
        controls.set_tip(self.error, text("tool.reduce.error.tip"), text("tool.reduce.error"))
        self.extrema = controls.CheckBox("", self.body, checked=True)
        self.extrema.setEnabled(False)  # the fitter always keeps prominent extrema
        controls.set_tip(self.extrema, text("tool.reduce.extrema.tip"),
                         text("tool.reduce.extrema"))
        self.body_layout.addLayout(form([(text("tool.reduce.error"), self.error),
                                         (text("tool.reduce.extrema"), self.extrema)], self.body))
        self.finish_layout()

    def options(self) -> dict:
        return {"tolerance_pct": self.error.value()}


TOOL_DIALOGS = {"scale": ScaleDialog, "offset_channels": OffsetDialog,
                "shift_following": ShiftDialog, "match_durations": MatchDialog,
                "reduce": ReduceDialog}

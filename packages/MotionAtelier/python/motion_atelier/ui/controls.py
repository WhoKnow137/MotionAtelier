# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Painted controls of the MotionAtelier interface.

Push buttons, tool buttons, combo boxes, seek sliders, check boxes, tab bars and numeric
fields draw the specified bevels themselves, so they look the same whatever Qt style the host
uses. Every control takes keyboard focus and shows a dotted focus outline.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence

from motion_atelier.ui import icons, qt, style
from motion_atelier.ui.qt import QtCore, QtGui, QtWidgets

Qt = QtCore.Qt


def _gradient(rect: QtCore.QRectF, top: str, bottom: str) -> QtGui.QLinearGradient:
    g = QtGui.QLinearGradient(rect.topLeft(), rect.bottomLeft())
    g.setColorAt(0.0, QtGui.QColor(top))
    g.setColorAt(1.0, QtGui.QColor(bottom))
    return g


def _focus(painter: QtGui.QPainter, rect: QtCore.QRectF) -> None:
    pen = QtGui.QPen(QtGui.QColor(style.TEXT), 1, Qt.PenStyle.DotLine)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRect(rect.adjusted(4.5, 4.5, -4.5, -4.5))


def paint_push_bevel(painter: QtGui.QPainter, rect: QtCore.QRectF, down: bool,
                     hover: bool) -> None:
    """Push button / combo bevel: 1 px outline radius 2, gradient, inner top/bottom edges."""
    outline, top, bottom, inner_top, inner_bottom = (
        style.PUSH_DOWN if down else style.PUSH_HOVER if hover else style.PUSH)
    r = rect.adjusted(0.5, 0.5, -0.5, -0.5)
    painter.setPen(QtGui.QPen(QtGui.QColor(outline), 1))
    painter.setBrush(_gradient(r, top, bottom))
    painter.drawRoundedRect(r, 2, 2)
    if down:
        painter.setPen(QtGui.QPen(QtGui.QColor(inner_top), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(r.adjusted(1, 1, -1, -1))
    else:
        painter.setPen(QtGui.QPen(QtGui.QColor(inner_top), 1))
        painter.drawLine(QtCore.QPointF(r.left() + 1.5, r.top() + 1),
                         QtCore.QPointF(r.right() - 1.5, r.top() + 1))
        painter.setPen(QtGui.QPen(QtGui.QColor(inner_bottom), 1))
        painter.drawLine(QtCore.QPointF(r.left() + 1.5, r.bottom() - 1),
                         QtCore.QPointF(r.right() - 1.5, r.bottom() - 1))


class _Hover:
    """Tracks hover for painted buttons."""

    def _init_hover(self) -> None:
        self._hover = False
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)


class PushButton(_Hover, QtWidgets.QAbstractButton):
    """Text push button with an optional 16 px icon 4 px before it."""

    def __init__(self, text: str = "", icon: str | None = None, parent=None, *,
                 icon_color: str | None = None, left: bool = False, flat: bool = False,
                 height: int = style.PUSH_H, icon_size: int = style.ICON) -> None:
        super().__init__(parent)
        self._init_hover()
        self.setText(text)
        self._icon = icon
        self._icon_color = icon_color
        self._icon_size = icon_size
        self._left = left
        self._flat = flat
        self._height = height
        self._fixed_width: int | None = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Fixed if not left
                           else QtWidgets.QSizePolicy.Policy.Expanding,
                           QtWidgets.QSizePolicy.Policy.Fixed)

    def set_icon(self, name: str | None) -> None:
        self._icon = name
        self.updateGeometry()
        self.update()

    def set_fixed_width(self, width: int | None) -> None:
        if width != self._fixed_width:
            self._fixed_width = width
            self.updateGeometry()

    def natural_width(self) -> int:
        metrics = self.fontMetrics()
        pad = 8 if self._left else 10
        gap = (6 if self._left else 4) if self._icon and self.text() else 0
        icon = self._icon_size if self._icon else 0
        return pad * 2 + icon + gap + metrics.horizontalAdvance(self.text()) + 2

    def sizeHint(self) -> QtCore.QSize:
        width = self._fixed_width if self._fixed_width is not None else self.natural_width()
        if self._left:
            width = max(96, width)
        return QtCore.QSize(width, self._height)

    def minimumSizeHint(self) -> QtCore.QSize:
        if self._left:
            return QtCore.QSize(96, self._height)
        return self.sizeHint()

    def paintEvent(self, _event) -> None:
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        rect = QtCore.QRectF(self.rect())
        down = self.isDown() or self.isChecked()
        enabled = self.isEnabled()
        if self._flat:
            if self._hover and enabled:
                p.fillRect(rect, QtGui.QColor(255, 255, 255, 10))
        else:
            paint_push_bevel(p, rect, down and enabled, self._hover and enabled)
        ratio = style.device_ratio(self)
        color = style.TEXT if enabled else style.TEXT_DISABLED
        glyph_color = self._icon_color or (style.GLYPH if enabled else style.GLYPH_DISABLED)
        metrics = self.fontMetrics()
        text = self.text()
        icon_w = self._icon_size if self._icon else 0
        gap = (6 if self._left else 4) if self._icon and text else 0
        if self._left:
            x = 8.0
            avail = rect.width() - x - icon_w - gap - 8
            text = metrics.elidedText(text, Qt.TextElideMode.ElideRight, max(0, int(avail)))
        else:
            content = icon_w + gap + metrics.horizontalAdvance(text)
            x = (rect.width() - content) / 2
        cy = rect.height() / 2
        if self._icon:
            icons.draw(p, icons.glyph(self._icon, self._icon_size, glyph_color, ratio),
                       QtCore.QPointF(x + icon_w / 2, cy))
            x += icon_w + gap
        p.setPen(QtGui.QColor(color))
        p.setFont(self.font())
        p.drawText(QtCore.QRectF(x, 0, rect.width() - x, rect.height()),
                   int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), text)
        if self.hasFocus() and self.focusPolicy() != Qt.FocusPolicy.NoFocus and \
                self.window().isActiveWindow() and not self._flat:
            _focus(p, rect)
        p.end()


class ToolButton(_Hover, QtWidgets.QAbstractButton):
    """Icon-only tool button, 26 × 26 (or 24 × 24 with a 14 px icon)."""

    def __init__(self, icon: str | None = None, parent=None, *, small: bool = False,
                 accent_checked: bool = False, pixmap: Callable[[float], QtGui.QPixmap] | None
                 = None) -> None:
        super().__init__(parent)
        self._init_hover()
        self._icon = icon
        self._small = small
        self._accent = accent_checked
        self._pixmap = pixmap
        size = style.TOOL_BUTTON_SMALL if small else style.TOOL_BUTTON
        self.setFixedSize(size, size)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set_icon(self, name: str) -> None:
        self._icon = name
        self.update()

    def icon_name(self) -> str | None:
        return self._icon

    def paintEvent(self, _event) -> None:
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        rect = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        enabled = self.isEnabled()
        checked = self.isChecked()
        if self._accent and checked:
            p.setPen(QtGui.QPen(QtGui.QColor(style.HIGHLIGHT), 1))
            p.setBrush(QtGui.QColor(style.HIGHLIGHT))
        else:
            outline, top, bottom = (style.TOOL_DOWN if (self.isDown() or checked) and enabled
                                    else style.TOOL_HOVER if self._hover and enabled
                                    else style.TOOL)
            p.setPen(QtGui.QPen(QtGui.QColor(outline), 1))
            p.setBrush(_gradient(rect, top, bottom))
        p.drawRoundedRect(rect, 3, 3)
        ratio = style.device_ratio(self)
        center = QtCore.QPointF(self.width() / 2, self.height() / 2)
        if self._pixmap is not None:
            icons.draw(p, self._pixmap(ratio), center)
        elif self._icon:
            size = style.ICON_SMALL if self._small else style.ICON
            color = style.GLYPH if enabled else style.GLYPH_DISABLED
            icons.draw(p, icons.glyph(self._icon, size, color, ratio), center)
        if self.hasFocus() and self.window().isActiveWindow():
            _focus(p, QtCore.QRectF(self.rect()))
        p.end()


class Combo(_Hover, QtWidgets.QAbstractButton):
    """Combo box whose popup is a menu at the combo's width with the current item
    highlighted; Up/Down change the value; width fits the widest item plus the arrow."""

    currentIndexChanged = QtCore.Signal(int)

    def __init__(self, items: Sequence[str] = (), parent=None, *, fill: bool = False,
                 index: int = 0, height: int = style.COMBO_H) -> None:
        super().__init__(parent)
        self._init_hover()
        self._items = list(items)
        self._data: list[object] = list(items)
        self._index = index if self._items else -1
        self._fill = fill
        self._height = height
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding if fill
                           else QtWidgets.QSizePolicy.Policy.Fixed,
                           QtWidgets.QSizePolicy.Policy.Fixed)
        self.clicked.connect(self.show_popup)
        self._popup: QtWidgets.QMenu | None = None

    # model
    def set_items(self, items: Sequence[str], data: Sequence[object] | None = None) -> None:
        self._items = list(items)
        self._data = list(data) if data is not None else list(items)
        self._index = min(max(self._index, 0), len(self._items) - 1) if self._items else -1
        self.updateGeometry()
        self.update()

    def items(self) -> list[str]:
        return list(self._items)

    def count(self) -> int:
        return len(self._items)

    def current_index(self) -> int:
        return self._index

    def current_text(self) -> str:
        return self._items[self._index] if 0 <= self._index < len(self._items) else ""

    def current_data(self):
        return self._data[self._index] if 0 <= self._index < len(self._data) else None

    def find_data(self, value) -> int:
        try:
            return self._data.index(value)
        except ValueError:
            return -1

    def set_current_index(self, index: int, silent: bool = False) -> None:
        if not self._items:
            return
        index = max(0, min(len(self._items) - 1, index))
        changed = index != self._index
        self._index = index
        self.update()
        if changed and not silent:
            self.currentIndexChanged.emit(index)

    # geometry
    def sizeHint(self) -> QtCore.QSize:
        metrics = self.fontMetrics()
        widest = max((metrics.horizontalAdvance(t) for t in self._items), default=0)
        return QtCore.QSize(widest + 8 + 34, self._height)

    def minimumSizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(40, self._height) if self._fill else self.sizeHint()

    # interaction
    def show_popup(self) -> None:
        if not self._items or not self.isEnabled():
            return
        menu = QtWidgets.QMenu(self)
        menu.setObjectName("ComboPopup")
        menu.setMinimumWidth(self.width())
        actions = []
        for i, item in enumerate(self._items):
            action = menu.addAction(item)
            action.triggered.connect(lambda _c=False, n=i: self.set_current_index(n))
            actions.append(action)
        if 0 <= self._index < len(actions):
            menu.setActiveAction(actions[self._index])
        self._popup = menu
        menu.exec(self.mapToGlobal(QtCore.QPoint(0, self.height())))
        self._popup = None
        menu.deleteLater()

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Down:
            self.set_current_index(self._index + 1)
        elif event.key() == Qt.Key.Key_Up:
            self.set_current_index(self._index - 1)
        elif event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.show_popup()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, _event) -> None:
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        rect = QtCore.QRectF(self.rect())
        paint_push_bevel(p, rect, False, self._hover and self.isEnabled())
        p.setPen(QtGui.QColor(style.TEXT))
        p.setFont(self.font())
        text = self.fontMetrics().elidedText(self.current_text(), Qt.TextElideMode.ElideRight,
                                             max(0, self.width() - 38))
        p.drawText(rect.adjusted(8, 0, -30, 0),
                   int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), text)
        arrow = QtGui.QColor("#c8c8c8" if self.isEnabled() else "#8a8a8a")
        cx, cy = rect.right() - 13, rect.center().y()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(arrow)
        p.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(cx - 4, cy - 2),
                                       QtCore.QPointF(cx + 4, cy - 2),
                                       QtCore.QPointF(cx, cy + 2)]))
        if self.hasFocus() and self.window().isActiveWindow():
            _focus(p, rect)
        p.end()


class NumberField(QtWidgets.QLineEdit):
    """Numeric field without arrows: right-aligned value, Up/Down and the wheel (while
    focused) step, Enter or leaving commits, invalid text reverts, a click selects all."""

    valueChanged = QtCore.Signal(float)
    stepped = QtCore.Signal(float)  # value after Up/Down/wheel (already committed)

    def __init__(self, value: float = 0.0, lo: float = -1e9, hi: float = 1e9, *,
                 decimals: int = 2, step: float = 1.0, suffix: str = "", dark: bool = False,
                 keyboard_tracking: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._lo, self._hi = lo, hi
        self._decimals = decimals
        self._step = step
        self._suffix = suffix
        self._tracking = keyboard_tracking
        self._value = self._clamp(value)
        self.setProperty("dark", "true" if dark else "false")
        self.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.setFixedHeight(style.FIELD_H)
        self.editingFinished.connect(self.commit)
        self.textEdited.connect(self._track)
        self._select_on_click = False
        self.setText(self.format(self._value))

    # value
    def _clamp(self, v: float) -> float:
        v = min(self._hi, max(self._lo, float(v)))
        return round(v, self._decimals) if self._decimals else float(round(v))

    def format(self, v: float) -> str:
        body = f"{v:.{self._decimals}f}" if self._decimals else str(int(round(v)))
        return body + self._suffix

    def value(self) -> float:
        return self._value

    def set_range(self, lo: float, hi: float) -> None:
        self._lo, self._hi = lo, hi

    def set_value(self, v: float, silent: bool = False) -> None:
        v = self._clamp(v)
        changed = v != self._value
        self._value = v
        if not self.hasFocus() or silent:
            self.setText(self.format(v))
        if changed and not silent:
            self.valueChanged.emit(v)

    def parse(self) -> float | None:
        raw = self.text().strip()
        if self._suffix.strip():
            raw = raw.replace(self._suffix.strip(), "")
        raw = raw.replace(",", ".").strip()
        try:
            v = float(raw)
        except ValueError:
            return None
        return v if math.isfinite(v) else None

    def commit(self) -> None:
        v = self.parse()
        if v is not None:
            self.set_value(v)
        self.setText(self.format(self._value))

    def _track(self, _text: str) -> None:
        if not self._tracking:
            return
        v = self.parse()
        if v is not None and self._lo <= v <= self._hi:
            self.set_value(v)

    def step_by(self, steps: float) -> None:
        self.set_value(self._value + steps * self._step)
        self.setText(self.format(self._value))
        self.stepped.emit(self._value)

    # interaction
    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.commit()
            self.selectAll()
            return
        if key == Qt.Key.Key_Up:
            self.step_by(1)
            return
        if key == Qt.Key.Key_Down:
            self.step_by(-1)
            return
        if key == Qt.Key.Key_Escape:
            self.setText(self.format(self._value))
            self.clearFocus()
            return
        super().keyPressEvent(event)

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        if not self.hasFocus() or not self.isEnabled():
            event.ignore()
            return
        self.step_by(1 if event.angleDelta().y() > 0 else -1)
        event.accept()

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self._select_on_click = True
        QtCore.QTimer.singleShot(0, self.selectAll)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        if self._select_on_click:
            self._select_on_click = False
            self.selectAll()


class SpinBox(QtWidgets.QWidget):
    """A light numeric field with an up/down arrow column (preview duration, dialogs)."""

    valueChanged = QtCore.Signal(float)

    ARROWS_W = 14

    def __init__(self, value: float, lo: float, hi: float, *, decimals: int = 2,
                 step: float = 1.0, suffix: str = "", width: int = 60, parent=None) -> None:
        super().__init__(parent)
        self.field = NumberField(value, lo, hi, decimals=decimals, step=step, suffix=suffix,
                                 parent=self)
        self.field.valueChanged.connect(self.valueChanged)
        self.field.setStyleSheet("QLineEdit { border-right: 0; }")
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, self.ARROWS_W, 0)
        layout.setSpacing(0)
        layout.addWidget(self.field)
        self.setFixedSize(width, style.FIELD_H)
        self._pressed: int = 0

    def value(self) -> float:
        return self.field.value()

    def set_value(self, v: float, silent: bool = False) -> None:
        self.field.set_value(v, silent)

    def setEnabled(self, on: bool) -> None:
        super().setEnabled(on)
        self.field.setEnabled(on)

    def _arrow_rect(self, up: bool) -> QtCore.QRect:
        h = self.height()
        x = self.width() - self.ARROWS_W
        return QtCore.QRect(x, 0 if up else h // 2, self.ARROWS_W, h // 2 if up else h - h // 2)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        pos = qt.event_pos(event).toPoint()
        if self._arrow_rect(True).contains(pos):
            self.field.step_by(1)
        elif self._arrow_rect(False).contains(pos):
            self.field.step_by(-1)
        else:
            super().mousePressEvent(event)

    def paintEvent(self, _event) -> None:
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        border = QtGui.QColor(style.FIELD_BORDER)
        for up in (True, False):
            r = QtCore.QRectF(self._arrow_rect(up))
            p.fillRect(r, QtGui.QColor(style.WINDOW))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QtGui.QColor("#aaaaaa"))
            cx, cy = r.center().x(), r.center().y()
            tri = ([QtCore.QPointF(cx - 3, cy + 2), QtCore.QPointF(cx + 3, cy + 2),
                    QtCore.QPointF(cx, cy - 2)] if up else
                   [QtCore.QPointF(cx - 3, cy - 2), QtCore.QPointF(cx + 3, cy - 2),
                    QtCore.QPointF(cx, cy + 2)])
            p.drawPolygon(QtGui.QPolygonF(tri))
        x = self.width() - self.ARROWS_W - 0.5
        p.setPen(QtGui.QPen(border, 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QtCore.QPointF(x, 0), QtCore.QPointF(x, self.height()))
        p.drawRect(QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5))
        p.end()


class SeekSlider(QtWidgets.QWidget):
    """A press anywhere on the groove jumps the handle there; dragging seeks."""

    pressed = QtCore.Signal()
    released = QtCore.Signal()
    seeked = QtCore.Signal(float)

    HANDLE_W, HANDLE_H = 9, 18

    def __init__(self, lo: float, hi: float, value: float, parent=None) -> None:
        super().__init__(parent)
        self._lo, self._hi = lo, hi
        self._value = value
        self._dragging = False
        self.setFixedHeight(self.HANDLE_H)
        self.setMinimumWidth(24)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                           QtWidgets.QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def value(self) -> float:
        return self._value

    def set_range(self, lo: float, hi: float) -> None:
        self._lo, self._hi = lo, hi
        self.update()

    def set_value(self, v: float) -> None:
        self._value = min(self._hi, max(self._lo, v))
        self.update()

    def _fraction(self) -> float:
        span = self._hi - self._lo
        return (self._value - self._lo) / span if span else 0.0

    def _seek(self, x: float) -> None:
        w = max(1.0, self.width() - 9.0)
        f = min(1.0, max(0.0, (x - 4.0) / w))
        self._value = self._lo + f * (self._hi - self._lo)
        self.update()
        self.seeked.emit(self._value)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or not self.isEnabled():
            return
        self._dragging = True
        self.pressed.emit()
        self._seek(qt.event_pos(event).x())

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._dragging:
            self._seek(qt.event_pos(event).x())

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._dragging:
            self._seek(qt.event_pos(event).x())
            self._dragging = False
            self.released.emit()

    def paintEvent(self, _event) -> None:
        p = QtGui.QPainter(self)
        w = self.width()
        p.fillRect(QtCore.QRectF(0, 6, w, 5), QtGui.QColor("#000000"))
        px = 4 + self._fraction() * (w - 9)
        fill = style.SLIDER_FILL if self.isEnabled() else style.SLIDER_FILL_DISABLED
        p.fillRect(QtCore.QRectF(0, 7, max(0.0, px - 4), 3), QtGui.QColor(fill))
        if self.isEnabled():
            p.fillRect(QtCore.QRectF(0, 7, max(0.0, px - 4), 1),
                       QtGui.QColor(style.SLIDER_FILL_TOP))
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        outline, top_edge, top, bottom = style.SLIDER_HANDLE
        r = QtCore.QRectF(px - 4 + 0.5, 0.5, self.HANDLE_W - 1, self.HANDLE_H - 1)
        p.setBrush(_gradient(r, top, bottom))
        p.setPen(QtGui.QPen(QtGui.QColor(outline), 1))
        p.drawRoundedRect(r, 1, 1)
        p.setPen(QtGui.QPen(QtGui.QColor(top_edge), 1))
        p.drawLine(QtCore.QPointF(r.left() + 1, r.top()), QtCore.QPointF(r.right() - 1, r.top()))
        p.end()


class CheckBox(_Hover, QtWidgets.QAbstractButton):
    """13 px box (light field, 1 px #2b2b2b) with a check mark; optional 16 px swatch icon
    5 px after it and a label. The label can be hidden."""

    BOX = 13
    GAP = 5

    def __init__(self, text: str = "", parent=None, *, checked: bool = True,
                 swatch: Callable[[float], QtGui.QPixmap] | None = None) -> None:
        super().__init__(parent)
        self._init_hover()
        self.setCheckable(True)
        self.setChecked(checked)
        self.setText(text)
        self._swatch = swatch
        self._show_text = True
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Fixed, QtWidgets.QSizePolicy.Policy.Fixed)

    def set_text_visible(self, on: bool) -> None:
        if on != self._show_text:
            self._show_text = on
            self.updateGeometry()
            self.update()

    def text_visible(self) -> bool:
        return self._show_text

    def width_for(self, with_text: bool) -> int:
        width = self.BOX
        if self._swatch is not None:
            width += self.GAP + style.ICON
        if with_text and self.text():
            width += self.GAP + self.fontMetrics().horizontalAdvance(self.text())
        return width

    def sizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(self.width_for(self._show_text), 18)

    def minimumSizeHint(self) -> QtCore.QSize:
        return self.sizeHint()

    def paintEvent(self, _event) -> None:
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        cy = self.height() / 2
        box = QtCore.QRectF(0.5, cy - self.BOX / 2 + 0.5, self.BOX - 1, self.BOX - 1)
        p.setPen(QtGui.QPen(QtGui.QColor("#2b2b2b"), 1))
        p.setBrush(QtGui.QColor(style.FIELD))
        p.drawRect(box)
        ratio = style.device_ratio(self)
        if self.isChecked():
            icons.draw(p, icons.checkmark(11, ratio), box.center())
        x = float(self.BOX)
        if self._swatch is not None:
            x += self.GAP
            icons.draw(p, self._swatch(ratio), QtCore.QPointF(x + style.ICON / 2, cy))
            x += style.ICON
        if self._show_text and self.text():
            x += self.GAP
            p.setPen(QtGui.QColor(style.TEXT if self.isEnabled() else style.TEXT_DISABLED))
            p.setFont(self.font())
            p.drawText(QtCore.QRectF(x, 0, self.width() - x, self.height()),
                       int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                       self.text())
        if self.hasFocus() and self.window().isActiveWindow():
            p.setPen(QtGui.QPen(QtGui.QColor(style.TEXT), 1, Qt.PenStyle.DotLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(box.adjusted(-2, -2, 2, 2))
        p.end()


class IconTabBar(QtWidgets.QWidget):
    """Equal-width icon-only tabs: 22 px tabs over a 1 px #242424 baseline."""

    currentChanged = QtCore.Signal(int)

    def __init__(self, entries: Sequence[tuple[str, str]], parent=None) -> None:
        super().__init__(parent)
        self._entries = list(entries)  # (icon, name)
        self._current = 0
        self._hover = -1
        self.setFixedHeight(style.TAB_H + 1)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                           QtWidgets.QSizePolicy.Policy.Fixed)

    def current(self) -> int:
        return self._current

    def set_current(self, index: int) -> None:
        if index != self._current and 0 <= index < len(self._entries):
            self._current = index
            self.update()
            self.currentChanged.emit(index)

    def tab_rect(self, index: int) -> QtCore.QRectF:
        n = len(self._entries)
        w = self.width() / n
        return QtCore.QRectF(index * w, 0, w, style.TAB_H)

    def index_at(self, pos: QtCore.QPointF) -> int:
        for i in range(len(self._entries)):
            if self.tab_rect(i).contains(pos):
                return i
        return -1

    def event(self, event: QtCore.QEvent) -> bool:
        if event.type() == QtCore.QEvent.Type.ToolTip:
            i = self.index_at(QtCore.QPointF(event.pos()))
            if i >= 0:
                QtWidgets.QToolTip.showText(event.globalPos(), self._entries[i][1], self)
            else:
                QtWidgets.QToolTip.hideText()
            return True
        return super().event(event)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        i = self.index_at(qt.event_pos(event))
        if i >= 0:
            self.set_current(i)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Right:
            self.set_current(min(len(self._entries) - 1, self._current + 1))
        elif event.key() == Qt.Key.Key_Left:
            self.set_current(max(0, self._current - 1))
        else:
            super().keyPressEvent(event)

    def paintEvent(self, _event) -> None:
        p = QtGui.QPainter(self)
        ratio = style.device_ratio(self)
        p.fillRect(QtCore.QRectF(0, style.TAB_H, self.width(), 1), QtGui.QColor(style.PANE_BORDER))
        for i, (icon, _name) in enumerate(self._entries):
            r = self.tab_rect(i)
            current = i == self._current
            outline, top, bottom = style.TAB_CURRENT if current else style.TAB
            body = r.adjusted(0.5 if i == 0 else -0.5, 0.5, -0.5, 0.5 if current else -0.5)
            p.fillRect(body, _gradient(body, top, bottom))
            p.setPen(QtGui.QPen(QtGui.QColor(outline), 1))
            p.drawLine(body.topLeft(), body.topRight())
            p.drawLine(body.topLeft(), body.bottomLeft())
            p.drawLine(body.topRight(), body.bottomRight())
            if not current:
                p.drawLine(body.bottomLeft(), body.bottomRight())
            p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            icons.draw(p, icons.glyph(icon, style.TAB_ICON, style.GLYPH, ratio), r.center())
            p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, False)
            if current and self.hasFocus() and self.window().isActiveWindow():
                _focus(p, r)
        p.end()


class Divider(QtWidgets.QWidget):
    """A 9 × 20 box holding a centred 1 px vertical line in the text colour."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(style.DIVIDER_W, style.DIVIDER_H)

    def paintEvent(self, _event) -> None:
        p = QtGui.QPainter(self)
        p.fillRect(QtCore.QRectF(4, 0, 1, self.height()), QtGui.QColor(style.TEXT))
        p.end()


class Rule(QtWidgets.QWidget):
    """A 1 px horizontal line."""

    def __init__(self, color: str = style.TEXT, parent=None) -> None:
        super().__init__(parent)
        self._color = color
        self.setFixedHeight(1)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                           QtWidgets.QSizePolicy.Policy.Fixed)

    def paintEvent(self, _event) -> None:
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), QtGui.QColor(self._color))
        p.end()


class StatusDot(QtWidgets.QWidget):
    """The 8 px highlight dot before the status text."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(8 + 4, 8)

    def paintEvent(self, _event) -> None:
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        icons.draw(p, icons.glyph("dot", 8, style.HIGHLIGHT, style.device_ratio(self)),
                   QtCore.QPointF(4 + 4, 4))
        p.end()


class ElidedLabel(QtWidgets.QLabel):
    """One line that elides at the right; the full text is the tooltip and accessible name."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._full = ""
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored,
                           QtWidgets.QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)

    def set_full_text(self, text: str) -> None:
        self._full = text
        self.setToolTip(text)
        self.setAccessibleName(text)
        self._elide()

    def full_text(self) -> str:
        return self._full

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        self.setText(self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight,
                                                   max(0, self.width())))


class FlowLayout(QtWidgets.QLayout):
    """Items left to right, wrapping when the row is full."""

    def __init__(self, parent=None, h_gap: int = 6, v_gap: int = 2) -> None:
        super().__init__(parent)
        self._items: list[QtWidgets.QLayoutItem] = []
        self._h, self._v = h_gap, v_gap
        self.setContentsMargins(0, 2, 0, 2)

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._place(QtCore.QRect(0, 0, width, 0), apply=False)

    def sizeHint(self) -> QtCore.QSize:
        m = self.contentsMargins()
        w = sum(i.sizeHint().width() for i in self._items) + self._h * max(0, len(self._items) - 1)
        h = max((i.sizeHint().height() for i in self._items), default=0)
        return QtCore.QSize(w + m.left() + m.right(), h + m.top() + m.bottom())

    def minimumSize(self) -> QtCore.QSize:
        m = self.contentsMargins()
        w = max((i.minimumSize().width() for i in self._items), default=0)
        h = max((i.minimumSize().height() for i in self._items), default=0)
        return QtCore.QSize(w + m.left() + m.right(), h + m.top() + m.bottom())

    def setGeometry(self, rect) -> None:
        super().setGeometry(rect)
        self._place(rect, apply=True)

    def _place(self, rect: QtCore.QRect, apply: bool) -> int:
        m = self.contentsMargins()
        x0, y = rect.x() + m.left(), rect.y() + m.top()
        right = rect.right() - m.right()
        x, row = x0, 0
        for item in self._items:
            hint = item.sizeHint()
            if x > x0 and x + hint.width() - 1 > right:
                x, y, row = x0, y + row + self._v, 0
            if apply:
                item.setGeometry(QtCore.QRect(QtCore.QPoint(x, y), hint))
            x += hint.width() + self._h
            row = max(row, hint.height())
        return y + row - rect.y() + m.bottom()


def heading(text: str, parent=None) -> QtWidgets.QLabel:
    label = QtWidgets.QLabel(text, parent)
    label.setFont(style.semibold())
    label.setAccessibleName(text)
    return label


def heading_row(title: str, parent=None) -> tuple[QtWidgets.QHBoxLayout, QtWidgets.QLabel]:
    """A semibold heading with a regular-weight count at the right."""
    row = QtWidgets.QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(6)
    row.addWidget(heading(title, parent))
    row.addStretch(1)
    count = QtWidgets.QLabel(parent)
    row.addWidget(count)
    return row, count


def set_tip(widget: QtWidgets.QWidget, tip: str, name: str | None = None) -> None:
    """Tooltip and accessible name (the tooltip's plain text unless ``name`` is given)."""
    widget.setToolTip(tip)
    plain = QtGui.QTextDocumentFragment.fromHtml(tip).toPlainText() if "<" in tip else tip
    widget.setAccessibleName(name or plain)

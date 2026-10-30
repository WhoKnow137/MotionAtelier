# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""The Recipes tab: search, grid/list library with drag
reorder, the History drawer, import review, and the foot buttons.

Loading a recipe or restoring history changes only the editor; Commit applies it.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from motion_atelier.app import history as H
from motion_atelier.app import library as LB
from motion_atelier.app.results import Issue
from motion_atelier.app.session import EditorSession
from motion_atelier.app.status import issue_text
from motion_atelier.app.strings import text
from motion_atelier.curve.errors import MotionAtelierError
from motion_atelier.curve.model import Curve
from motion_atelier.effects.stack import evaluate as evaluate_effects
from motion_atelier.generators import registry
from motion_atelier.generators.base import ShapeKind
from motion_atelier.records.recipe import Recipe
from motion_atelier.ui import controls, dialogs, icons, qt, style
from motion_atelier.ui.canvas import Bounds as CBounds
from motion_atelier.ui.canvas import Transform, adaptive_points, handle
from motion_atelier.ui.qt import QtCore, QtGui, QtWidgets

Qt = QtCore.Qt
TILE_H, PREVIEW_H, FOOTER_H, MIN_TILE_W = 78, 50, 20, 80
GRID_CELL_TARGET = 108  # a column per this many pixels, at least two
LIST_CELL_H = 48
HISTORY_CELL = (136, 120)
ARCHIVE_FILTER = "MotionAtelier recipes (*.zip)"
MAX_IMPORT_BYTES = 16 * 1024 * 1024


def recipe_curve(recipe: Recipe) -> Curve:
    """The curve a recipe produces (generator built, effects applied) for its preview."""
    base = recipe.curve if recipe.curve is not None else registry.build(recipe.record).curve
    if recipe.effects is not None:
        return evaluate_effects(recipe.effects, base)[0]
    return base


_PREVIEW_CACHE: dict[tuple, QtGui.QPixmap] = {}


def preview_pixmap(curve: Curve, w: int, h: int, ratio: float) -> QtGui.QPixmap:
    """Tile preview: transparent, 6 px padding, x over 0..1, y over {0, 1, knots,
    handles} plus 7 %, guides at 0 and 1, the curve and both endpoints."""
    key = (curve, w, h, round(ratio, 3))
    cached = _PREVIEW_CACHE.get(key)
    if cached is not None:
        return cached
    pm = QtGui.QPixmap(max(1, round(w * ratio)), max(1, round(h * ratio)))
    pm.setDevicePixelRatio(ratio)
    pm.fill(Qt.GlobalColor.transparent)
    ys = [0.0, 1.0]
    for i, k in enumerate(curve.knots):
        ys.append(k.y)
        for side in ("in", "out"):
            hp = handle(curve, i, side)
            if hp is not None:
                ys.append(hp[1])
    lo, hi = min(ys), max(ys)
    margin = max(1.0, hi - lo) * 0.07
    x0, x1 = curve.x_start, curve.x_end
    tr = Transform(CBounds(x0, x1, lo - margin, hi + margin), w, h, 6)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    p.setPen(QtGui.QPen(style.color(style.MID, 85), 1))
    for y in (0.0, 1.0):
        p.drawLine(tr.to(x0, y), tr.to(x1, y))
    pen = QtGui.QPen(QtGui.QColor(style.HIGHLIGHT), 1.75)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.drawPolyline(adaptive_points(curve, tr, 0.55))
    p.setPen(QtGui.QPen(QtGui.QColor(style.HIGHLIGHT), 1.25))
    p.setBrush(QtGui.QColor(style.BASE))
    for k in (curve.knots[0], curve.knots[-1]):
        p.drawEllipse(tr.to(k.x, k.y), 2.5, 2.5)
    p.end()
    if len(_PREVIEW_CACHE) > 512:
        _PREVIEW_CACHE.clear()
    _PREVIEW_CACHE[key] = pm
    return pm


class _TileView(QtWidgets.QAbstractScrollArea):
    """Base for the painted tile grids: framed in the list border over Base."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.viewport().setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setViewportMargins(1, 1, 1, 1)
        self._pan: tuple[float, int] | None = None

    def paint_frame(self) -> None:
        p = QtGui.QPainter(self)
        p.setPen(QtGui.QColor(style.LIST_BORDER))
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        p.end()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)

    def _content_height(self) -> int:
        return 0

    def update_scroll(self) -> None:
        bar = self.verticalScrollBar()
        bar.setPageStep(self.viewport().height())
        bar.setSingleStep(20)
        bar.setRange(0, max(0, self._content_height() - self.viewport().height()))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.update_scroll()

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        self.viewport().update()

    def start_pan(self, y: float) -> None:
        self._pan = (y, self.verticalScrollBar().value())

    def pan_to(self, y: float) -> None:
        if self._pan is not None:
            y0, v0 = self._pan
            self.verticalScrollBar().setValue(int(v0 - (y - y0)))


class RecipeGrid(_TileView):
    """The library tiles (grid or list) of the current shape."""

    loadRequested = QtCore.Signal(str)
    favoriteToggled = QtCore.Signal(str)
    reordered = QtCore.Signal(list)  # visible ids in the new order
    menuRequested = QtCore.Signal(QtCore.QPoint)
    deleteRequested = QtCore.Signal()
    selectionChanged = QtCore.Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("RecipeGrid")
        self.setAccessibleName(text("recipes.list"))
        self.mode = "grid"
        self.recipes: list[Recipe] = []
        self.curves: dict[str, Curve] = {}
        self.selected: set[str] = set()
        self.current: str | None = None
        self.hover: str | None = None
        self.can_reorder = True
        self._press: tuple[QtCore.QPointF, str] | None = None
        self._drag: dict | None = None
        self._positions: dict[str, QtCore.QPointF] = {}
        self._anim_from: dict[str, QtCore.QPointF] = {}
        self._anim_start = 0.0
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self.viewport().update)

    # model
    def set_recipes(self, recipes: list[Recipe]) -> None:
        self.recipes = list(recipes)
        ids = {r.id for r in recipes}
        self.selected &= ids
        if self.current not in ids:
            self.current = None
        self.curves = {r.id: recipe_curve(r) for r in recipes}
        self._positions = {}
        self.update_scroll()
        self.viewport().update()

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self._positions = {}
        self.update_scroll()
        self.viewport().update()

    # geometry
    def cell(self) -> tuple[int, int, int]:
        avail = self.viewport().width()
        if self.mode == "list":
            return avail, LIST_CELL_H, 1
        cols = max(2, avail // GRID_CELL_TARGET)
        w = avail // cols
        if w - 4 < MIN_TILE_W:
            cols, w = 2, avail // 2
        return w, TILE_H + 4, cols

    def _content_height(self) -> int:
        w, h, cols = self.cell()
        return -(-len(self.recipes) // cols) * h

    def _order(self) -> list[str | None]:
        ids = [r.id for r in self.recipes]
        if self._drag is not None and self._drag.get("active"):
            ids = [i for i in ids if i != self._drag["id"]]
            ids.insert(self._drag["gap"], None)
        return ids

    def _targets(self) -> dict[str, QtCore.QPointF]:
        w, h, cols = self.cell()
        out = {}
        for i, rid in enumerate(self._order()):
            if rid is not None:
                out[rid] = QtCore.QPointF((i % cols) * w, (i // cols) * h)
        return out

    def _animated_positions(self) -> dict[str, QtCore.QPointF]:
        targets = self._targets()
        u = min(1.0, (time.monotonic() - self._anim_start) * 1000 / style.ANIMATION_MS)
        e = style.out_cubic(u)
        out = {}
        for rid, t in targets.items():
            a = self._anim_from.get(rid, t)
            out[rid] = QtCore.QPointF(a.x() + (t.x() - a.x()) * e, a.y() + (t.y() - a.y()) * e)
        if u >= 1.0:
            self._timer.stop()
        self._positions = out
        return out

    def _relayout_animated(self) -> None:
        self._anim_from = dict(self._positions or self._targets())
        self._anim_start = time.monotonic()
        self._timer.start()

    def tile_at(self, pos: QtCore.QPointF) -> str | None:
        w, h, _cols = self.cell()
        y = pos.y() + self.verticalScrollBar().value()
        for rid, p in self._targets().items():
            if QtCore.QRectF(p.x(), p.y(), w, h).contains(QtCore.QPointF(pos.x(), y)):
                return rid
        return None

    def _star_rect(self, origin: QtCore.QPointF) -> QtCore.QRectF:
        w, h, _ = self.cell()
        cw, ch = w - 4, h - 4
        top = ch / 2 - 8 if self.mode == "list" else ch - FOOTER_H + 3
        return QtCore.QRectF(origin.x() + 2 + cw - 19, origin.y() + 2 + top, 16, 16)

    # painting
    def paintEvent(self, _event) -> None:
        p = QtGui.QPainter(self.viewport())
        p.fillRect(self.viewport().rect(), QtGui.QColor(style.BASE))
        w, h, _cols = self.cell()
        scroll = self.verticalScrollBar().value()
        positions = self._animated_positions()
        ratio = style.device_ratio(self)
        focused = self.hasFocus()
        for r in self.recipes:
            if self._drag is not None and self._drag.get("active") and r.id == self._drag["id"]:
                continue
            origin = positions.get(r.id)
            if origin is None:
                continue
            origin = QtCore.QPointF(origin.x(), origin.y() - scroll)
            if origin.y() > self.viewport().height() or origin.y() + h < 0:
                continue
            self._paint_tile(p, r, origin, w, h, ratio, focused)
        if self._drag is not None and self._drag.get("active"):
            r = next(x for x in self.recipes if x.id == self._drag["id"])
            p.setOpacity(0.85)
            pos = self._drag["pos"]
            self._paint_tile(p, r, QtCore.QPointF(pos.x() - w / 2, pos.y() - h / 2), w, h, ratio,
                             False)
            p.setOpacity(1.0)
        p.end()
        self.paint_frame()

    def _paint_tile(self, p, r: Recipe, origin: QtCore.QPointF, w: int, h: int, ratio: float,
                    focused: bool) -> None:
        cw, ch = w - 4, h - 4
        card = QtCore.QRectF(origin.x() + 2, origin.y() + 2, cw, ch)
        selected = r.id in self.selected
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        if selected:
            p.setBrush(style.color(style.HIGHLIGHT, 28))
            p.drawRoundedRect(card, 3, 3)
        elif self.hover == r.id:
            p.setBrush(style.color(style.TEXT, 12))
            p.drawRoundedRect(card, 3, 3)
        if focused and self.current == r.id:
            pen = QtGui.QPen(QtGui.QColor(style.HIGHLIGHT), 1, Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(card.adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
        listing = self.mode == "list"
        pw = min(64, max(1, cw // 3)) if listing else cw - 8
        ph = ch - 8 if listing else PREVIEW_H
        p.drawPixmap(QtCore.QPointF(card.x() + 4, card.y() + 4),
                     preview_pixmap(self.curves[r.id], int(pw), int(ph), ratio))
        font = QtGui.QFont(self.font())
        font.setWeight(QtGui.QFont.Weight.DemiBold if selected else QtGui.QFont.Weight.Normal)
        p.setFont(font)
        p.setPen(QtGui.QColor(style.HIGHLIGHT if selected else style.TEXT))
        metrics = QtGui.QFontMetrics(font)
        if listing:
            rect = QtCore.QRectF(card.x() + pw + 12, card.y(), max(1, cw - pw - 36), ch)
        else:
            rect = QtCore.QRectF(card.x() + 6, card.y() + ch - FOOTER_H, cw - 30, FOOTER_H - 2)
        p.drawText(rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                   metrics.elidedText(r.name, Qt.TextElideMode.ElideRight, int(rect.width())))
        star = self._star_rect(origin)
        star_font = QtGui.QFont(self.font())
        star_font.setPixelSize(13)
        p.setFont(star_font)
        p.setPen(QtGui.QColor(style.FAVORITE) if r.favorite else style.color(style.TEXT, 110))
        p.drawText(star, int(Qt.AlignmentFlag.AlignCenter), "★" if r.favorite else "☆")

    # interaction
    def _ids(self) -> list[str]:
        return [r.id for r in self.recipes]

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        pos = qt.event_pos(event)
        if event.button() == Qt.MouseButton.MiddleButton:
            self.start_pan(pos.y())
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        rid = self.tile_at(pos)
        if rid is None:
            self.selected.clear()
            self.selectionChanged.emit()
            self.viewport().update()
            return
        origin = self._targets()[rid]
        star = self._star_rect(QtCore.QPointF(origin.x(), origin.y()
                                              - self.verticalScrollBar().value()))
        if star.contains(pos):
            self.favoriteToggled.emit(rid)
            return
        mods = event.modifiers()
        ids = self._ids()
        if mods & Qt.KeyboardModifier.ControlModifier:
            self.selected ^= {rid}
        elif mods & Qt.KeyboardModifier.ShiftModifier and self.current in ids:
            a, b = ids.index(self.current), ids.index(rid)
            self.selected = set(ids[min(a, b): max(a, b) + 1])
        elif rid not in self.selected:
            self.selected = {rid}
        self.current = rid
        self.selectionChanged.emit()
        self.viewport().update()
        if self.can_reorder:
            self._press = (pos, rid)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        pos = qt.event_pos(event)
        if self._pan is not None:
            self.pan_to(pos.y())
            return
        if self._press is not None:
            origin, rid = self._press
            if self._drag is None and (pos - origin).manhattanLength() >= 5:
                self._drag = {"id": rid, "gap": self._ids().index(rid), "active": True,
                              "pos": pos}
                self._relayout_animated()
            if self._drag is not None:
                self._drag["pos"] = pos
                gap = self._gap_at(pos)
                if gap != self._drag["gap"]:
                    self._drag["gap"] = gap
                    self._relayout_animated()
                self.viewport().update()
            return
        hover = self.tile_at(pos)
        if hover != self.hover:
            self.hover = hover
            self.viewport().update()

    def _gap_at(self, pos: QtCore.QPointF) -> int:
        w, h, cols = self.cell()
        y = pos.y() + self.verticalScrollBar().value()
        col = min(cols - 1, max(0, int(pos.x() // w)))
        row = max(0, int(y // h))
        right_half = (pos.x() % w) > w / 2
        return max(0, min(len(self.recipes) - 1, row * cols + col + (1 if right_half else 0)))

    def mouseReleaseEvent(self, event) -> None:
        self._pan = None
        press, drag = self._press, self._drag
        self._press = None
        self._drag = None
        if drag is not None:
            ids = [i for i in self._ids() if i != drag["id"]]
            ids.insert(drag["gap"], drag["id"])
            self._positions = {}
            self.viewport().update()
            if ids != self._ids():
                self.reordered.emit(ids)
        elif press is not None:
            self.viewport().update()

    def mouseDoubleClickEvent(self, event) -> None:
        rid = self.tile_at(qt.event_pos(event))
        if rid is None:
            return
        origin = self._targets()[rid]
        star = self._star_rect(QtCore.QPointF(origin.x(), origin.y()
                                              - self.verticalScrollBar().value()))
        if not star.contains(qt.event_pos(event)):
            self.loadRequested.emit(rid)

    def leaveEvent(self, event) -> None:
        self.hover = None
        self.viewport().update()
        super().leaveEvent(event)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        ids = self._ids()
        if not ids:
            return super().keyPressEvent(event)
        _w, _h, cols = self.cell()
        moves = {Qt.Key.Key_Right: 1, Qt.Key.Key_Left: -1, Qt.Key.Key_Down: cols,
                 Qt.Key.Key_Up: -cols}
        key = event.key()
        if key in moves:
            i = ids.index(self.current) if self.current in ids else -1
            i = 0 if i < 0 else max(0, min(len(ids) - 1, i + moves[key]))
            self.current = ids[i]
            self.selected = {ids[i]}
            self.selectionChanged.emit()
            self.viewport().update()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.current:
            self.loadRequested.emit(self.current)
        elif key == Qt.Key.Key_Delete and self.selected:
            self.deleteRequested.emit()
        else:
            super().keyPressEvent(event)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self.viewport().update()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self.viewport().update()

    def contextMenuEvent(self, event: QtGui.QContextMenuEvent) -> None:
        rid = self.tile_at(QtCore.QPointF(self.viewport().mapFrom(self, event.pos())))
        if rid is not None and rid not in self.selected:
            self.selected = {rid}
            self.current = rid
            self.selectionChanged.emit()
            self.viewport().update()
        self.menuRequested.emit(event.globalPos())


def history_caption(moment: datetime, now: datetime | None = None) -> str:
    """``Today 14:32``, ``Yesterday 09:10``, ``28 Sep`` or ``28 Sep 2025``."""
    now = now or datetime.now().astimezone()
    days = (now.date() - moment.date()).days
    hm = moment.strftime("%H:%M")
    if days == 0:
        return text("history.today", time=hm)
    if days == 1:
        return text("history.yesterday", time=hm)
    month = text(f"month.{moment.month}")
    if moment.year == now.year:
        return f"{moment.day} {month}"
    return f"{moment.day} {month} {moment.year}"


class HistoryGrid(_TileView):
    """History tiles, 136 × 120 cells."""

    selected_changed = QtCore.Signal(object)  # entry id or None
    activated = QtCore.Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("HistoryGrid")
        self.setAccessibleName(text("history.list"))
        self.entries: list[H.HistoryEntry] = []
        self.selected: str | None = None

    def set_entries(self, entries: list[H.HistoryEntry]) -> None:
        self.entries = list(entries)
        if self.selected not in {e.id for e in entries}:
            self.selected = None
        self.update_scroll()
        self.viewport().update()

    def cols(self) -> int:
        return max(1, self.viewport().width() // HISTORY_CELL[0])

    def _content_height(self) -> int:
        return -(-len(self.entries) // self.cols()) * HISTORY_CELL[1]

    def entry_at(self, pos: QtCore.QPointF) -> str | None:
        cols = self.cols()
        y = pos.y() + self.verticalScrollBar().value()
        col, row = int(pos.x() // HISTORY_CELL[0]), int(y // HISTORY_CELL[1])
        if col >= cols:
            return None
        i = row * cols + col
        return self.entries[i].id if 0 <= i < len(self.entries) else None

    def paintEvent(self, _event) -> None:
        p = QtGui.QPainter(self.viewport())
        p.fillRect(self.viewport().rect(), QtGui.QColor(style.BASE))
        cols = self.cols()
        scroll = self.verticalScrollBar().value()
        ratio = style.device_ratio(self)
        cw, chh = HISTORY_CELL
        for i, e in enumerate(self.entries):
            x = (i % cols) * cw
            y = (i // cols) * chh - scroll
            if y > self.viewport().height() or y + chh < 0:
                continue
            card = QtCore.QRectF(x + 2, y + 2, 132, 116)
            p.fillRect(card, QtGui.QColor(style.ALT_BASE))
            if e.id == self.selected:
                p.fillRect(card, style.color(style.HIGHLIGHT, 45))
            p.drawPixmap(QtCore.QPointF(card.x() + 3, card.y() + 3),
                         preview_pixmap(e.curve, 126, 72, ratio))
            if e.use_count > 1:
                p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
                label = str(e.use_count)
                bw = QtGui.QFontMetrics(self.font()).horizontalAdvance(label) + 10
                badge = QtCore.QRectF(card.right() - 4 - bw, card.y() + 5, bw, 17)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QtGui.QColor(style.HIGHLIGHT))
                p.drawRoundedRect(badge, 8.5, 8.5)
                p.setPen(QtGui.QColor(style.HIGHLIGHTED_TEXT))
                p.drawText(badge, int(Qt.AlignmentFlag.AlignCenter), label)
            metrics = QtGui.QFontMetrics(self.font())
            p.setPen(QtGui.QColor(style.TEXT))
            p.drawText(QtCore.QRectF(card.x() + 5, card.y() + 77, 122, 16),
                       int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                       metrics.elidedText(e.label, Qt.TextElideMode.ElideRight, 122))
            p.setPen(style.color(style.TEXT, 150))
            moment = H.parse_iso(e.last_used).astimezone()
            p.drawText(QtCore.QRectF(card.x() + 5, card.y() + 94, 122, 16),
                       int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                       history_caption(moment))
        p.end()
        self.paint_frame()

    def event(self, event) -> bool:
        if event.type() == QtCore.QEvent.Type.ToolTip:
            pos = QtCore.QPointF(self.viewport().mapFrom(self, event.pos()))
            rid = self.entry_at(pos)
            if rid is not None:
                e = next(x for x in self.entries if x.id == rid)
                QtWidgets.QToolTip.showText(event.globalPos(), entry_tooltip(e), self)
            else:
                QtWidgets.QToolTip.hideText()
            return True
        return super().event(event)

    def mousePressEvent(self, event) -> None:
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        if event.button() == Qt.MouseButton.MiddleButton:
            self.start_pan(qt.event_pos(event).y())
            return
        rid = self.entry_at(qt.event_pos(event))
        if rid is not None:
            self.selected = rid
            self.viewport().update()
            self.selected_changed.emit(rid)

    def mouseMoveEvent(self, event) -> None:
        self.pan_to(qt.event_pos(event).y())

    def mouseReleaseEvent(self, _event) -> None:
        self._pan = None

    def mouseDoubleClickEvent(self, event) -> None:
        rid = self.entry_at(qt.event_pos(event))
        if rid is not None:
            self.activated.emit(rid)


def entry_tooltip(e: H.HistoryEntry) -> str:
    def stamp(value: str) -> str:
        return H.parse_iso(value).astimezone().strftime("%Y-%m-%d %H:%M")
    uses = text("history.uses.one") if e.use_count == 1 else text("history.uses",
                                                                   count=e.use_count)
    return "\n".join((f"{text(f'shape.{e.source_kind.value}')} · {uses}",
                      text("history.last_used", when=stamp(e.last_used)),
                      text("history.first_used", when=stamp(e.first_used))))


STATE_LABEL = {LB.NEW: "import.state.new", LB.DUPLICATE: "import.state.duplicate",
               LB.NAME_CONFLICT: "import.state.name_conflict", LB.INVALID: "import.state.invalid"}
ACTION_LABEL = {LB.ACTION_IMPORT: "import.action.import", LB.ACTION_SKIP: "import.action.skip",
                LB.ACTION_RENAME: "import.action.rename",
                LB.ACTION_REPLACE: "import.action.replace"}


class ImportReviewDialog(dialogs.FramedDialog):
    """Review Recipe Import: Recipe / Type / Status / Action table."""

    def __init__(self, review: LB.ImportReview, parent) -> None:
        super().__init__(text("import.title"), parent, ok_text=text("import.ok"))
        self.review = review
        self.table = QtWidgets.QTableWidget(len(review.items), 4, self.body)
        self.table.setHorizontalHeaderLabels([text("import.col.recipe"), text("import.col.type"),
                                              text("import.col.status"),
                                              text("import.col.action")])
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        for col in (1, 2, 3):
            header.setSectionResizeMode(col, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.setAccessibleName(text("import.title"))
        self.combos: dict[int, controls.Combo] = {}
        for row, item in enumerate(review.items):
            self.table.setRowHeight(row, 24)
            self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(item.name))
            kind = item.recipe.kind.value if item.recipe is not None else ""
            self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(
                text(f"shape.{kind}") if kind else "—"))
            self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(text(STATE_LABEL[item.state])))
            actions = item.actions
            if item.state == LB.INVALID:
                self.table.setItem(row, 3, QtWidgets.QTableWidgetItem(text("import.blocked")))
                continue
            combo = controls.Combo([text(ACTION_LABEL[a]) for a in actions], self.table,
                                   fill=True)
            combo.set_items(combo.items(), list(actions))
            combo.setAccessibleName(text("import.col.action"))
            combo.currentIndexChanged.connect(lambda _i: self._summary())
            self.table.setCellWidget(row, 3, combo)
            self.combos[item.index] = combo
        self.table.setMinimumSize(618, 280)
        self.body_layout.addWidget(self.table)
        self.summary = QtWidgets.QLabel(self.body)
        self.body_layout.addWidget(self.summary)
        self.set_accept_rule(lambda: any(c.current_data() != LB.ACTION_SKIP
                                         for c in self.combos.values()))
        self.finish_layout()
        self.resize(640, 420)
        self._summary()

    def choices(self) -> dict[int, str]:
        return {i: c.current_data() for i, c in self.combos.items()}

    def _summary(self) -> None:
        values = [c.current_data() for c in self.combos.values()]
        add = sum(v in (LB.ACTION_IMPORT, LB.ACTION_RENAME) for v in values)
        rep = sum(v == LB.ACTION_REPLACE for v in values)
        skip = sum(v == LB.ACTION_SKIP for v in values)
        blocked = len(self.review.items) - len(values)
        line = text("import.summary", add=add, replace=rep, skip=skip)
        if blocked:
            line += text("import.summary.blocked", count=blocked)
        self.summary.setText(line)
        self.sync()


class RecipesPage(QtWidgets.QWidget):
    """Page 3 of the inspector."""

    status = QtCore.Signal(str)
    loaded = QtCore.Signal(object)  # the curve on screen before loading (for the morph)
    historyPreview = QtCore.Signal(object)
    historyRestored = QtCore.Signal(object)
    drawerToggled = QtCore.Signal(bool)

    def __init__(self, session: EditorSession, library: LB.LibraryService,
                 history: H.HistoryService, shade: Callable[[], QtWidgets.QWidget],
                 on_screen: Callable[[], Curve | None], parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("RecipesPage")
        self.session = session
        self.library = library
        self.history = history
        self.shade = shade
        self.on_screen = on_screen
        self.sort = LB.SORT_MANUAL
        # File choosers and prompts are injectable so tests and scripted sessions run headless.
        self.pick_open: Callable[[], str] = self._pick_open
        self.pick_save: Callable[[], str] = self._pick_save
        self.prompt: Callable[[str, str, str, bool], str | None] = self._prompt
        self.confirm: Callable[[str, str], bool] = self._confirm
        self.review: Callable[[LB.ImportReview], dict | None] = self._review
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(0)
        self.split = QtWidgets.QSplitter(Qt.Orientation.Vertical, self)
        self.split.setChildrenCollapsible(False)
        self.split.setHandleWidth(5)
        library_panel = QtWidgets.QWidget(self.split)
        lib = QtWidgets.QVBoxLayout(library_panel)
        lib.setContentsMargins(0, 0, 0, 0)
        lib.setSpacing(6)
        row, self.count = controls.heading_row(text("recipes.heading"), library_panel)
        lib.addLayout(row)
        self.search = QtWidgets.QLineEdit(library_panel)
        self.search.setObjectName("RecipeSearch")
        self.search.setProperty("prompt", "true")
        self.search.setFixedHeight(style.LINE_EDIT_H)
        self.search.setPlaceholderText(text("recipes.search.placeholder"))
        self.search.setAccessibleName(text("recipes.search"))
        self.search.setToolTip(text("recipes.search.placeholder"))
        self.search.setClearButtonEnabled(True)
        self.search.addAction(QtGui.QIcon(icons.glyph("search", 16, style.GLYPH, 2.0)),
                              QtWidgets.QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(lambda _t: self.refresh())
        lib.addWidget(self.search)
        bar = QtWidgets.QHBoxLayout()
        bar.setSpacing(4)
        self.grid_button = controls.ToolButton(None, library_panel,
                                               pixmap=lambda r: icons.view_icon(True, 16, r))
        self.list_button = controls.ToolButton(None, library_panel,
                                               pixmap=lambda r: icons.view_icon(False, 16, r))
        for button, mode in ((self.grid_button, "grid"), (self.list_button, "list")):
            button.setCheckable(True)
            name = text(f"recipes.view.{mode}")
            controls.set_tip(button, name, name)
            button.clicked.connect(lambda _c=False, m=mode: self.set_view(m))
            bar.addWidget(button)
        self.grid_button.setChecked(True)
        bar.addStretch(1)
        self.sort_combo = controls.Combo([text("recipes.sort.manual"), text("recipes.sort.name"),
                                          text("recipes.sort.favorites")], library_panel)
        self.sort_combo.set_items(self.sort_combo.items(), list(LB.SORT_MODES))
        self.sort_combo.setFixedWidth(110)
        controls.set_tip(self.sort_combo, text("recipes.sort.tip"), text("recipes.sort"))
        self.sort_combo.currentIndexChanged.connect(self._sort_changed)
        bar.addWidget(self.sort_combo)
        lib.addLayout(bar)
        self.grid = RecipeGrid(library_panel)
        self.grid.setMinimumHeight(120)
        self.grid.loadRequested.connect(self.load)
        self.grid.favoriteToggled.connect(self.toggle_favorite)
        self.grid.reordered.connect(self.reorder)
        self.grid.menuRequested.connect(self.menu)
        self.grid.deleteRequested.connect(self.delete_selected)
        lib.addWidget(self.grid, 1)
        self.drawer = QtWidgets.QWidget(self.split)
        drawer = QtWidgets.QVBoxLayout(self.drawer)
        drawer.setContentsMargins(4, 4, 4, 4)
        drawer.setSpacing(6)
        drawer.addWidget(controls.heading(text("history.heading"), self.drawer))
        self.history_sort = controls.Combo([text("history.view.recent"),
                                            text("history.view.most_used"),
                                            text("history.view.name")], self.drawer, fill=True)
        self.history_sort.set_items(self.history_sort.items(), list(H.VIEWS))
        controls.set_tip(self.history_sort, text("history.sort.tip"), text("history.sort"))
        self.history_sort.currentIndexChanged.connect(lambda _i: self.refresh_history())
        drawer.addWidget(self.history_sort)
        self.history_grid = HistoryGrid(self.drawer)
        self.history_grid.setMinimumHeight(120)
        self.history_grid.selected_changed.connect(self._history_selected)
        self.history_grid.activated.connect(lambda _rid: self.restore_history())
        drawer.addWidget(self.history_grid, 1)
        actions = QtWidgets.QHBoxLayout()
        actions.setSpacing(6)
        self.history_buttons: dict[str, controls.PushButton] = {}
        for key, slot in (("restore", self.restore_history), ("delete", self.delete_history),
                          ("clear", self.clear_history)):
            button = controls.PushButton(text(f"history.{key}"), None, self.drawer)
            button.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                                 QtWidgets.QSizePolicy.Policy.Fixed)
            controls.set_tip(button, text(f"history.{key}.tip"), text(f"history.{key}"))
            button.clicked.connect(slot)
            actions.addWidget(button, 1)
            self.history_buttons[key] = button
        drawer.addLayout(actions)
        self.split.addWidget(library_panel)
        self.split.addWidget(self.drawer)
        self.split.setStretchFactor(0, 3)
        self.split.setStretchFactor(1, 2)
        self.drawer.hide()
        outer.addWidget(self.split, 1)
        outer.addSpacing(6)
        outer.addWidget(controls.Rule(style.MID, self))
        outer.addSpacing(6)
        foot = QtWidgets.QHBoxLayout()
        self.history_toggle = controls.PushButton(text("recipes.history"), "history", self)
        self.history_toggle.setCheckable(True)
        controls.set_tip(self.history_toggle, text("recipes.history.tip"),
                         text("recipes.history"))
        self.history_toggle.toggled.connect(self.set_drawer)
        foot.addWidget(self.history_toggle)
        foot.addStretch(1)
        self.import_button = controls.PushButton(text("recipes.import"), "import", self)
        controls.set_tip(self.import_button, text("recipes.import.tip"),
                         text("recipes.import.name"))
        self.import_button.clicked.connect(self.import_dialog)
        foot.addWidget(self.import_button)
        outer.addLayout(foot)
        library.subscribe(self.refresh)
        history.subscribe(self.refresh_history)
        self.refresh()
        self.refresh_history()

    def dispose(self) -> None:
        self.library.unsubscribe(self.refresh)
        self.history.unsubscribe(self.refresh_history)

    def _guard(self, fn) -> None:
        try:
            fn()
        except MotionAtelierError as exc:
            self.status.emit(text("status.recipes_failed",
                                  reason=issue_text(Issue(exc.code, exc.detail))))

    # library view
    def kind(self) -> ShapeKind:
        return self.session.current_kind

    def visible(self) -> list[Recipe]:
        return [r for r in self.library.query(self.search.text(), self.sort)
                if r.kind == self.kind()]

    def refresh(self) -> None:
        recipes = self.visible()
        self.count.setText(text("recipes.count", count=len(recipes),
                                shape=text(f"shape.{self.kind().value}")))
        self.grid.can_reorder = self.sort == LB.SORT_MANUAL and not self.search.text().strip()
        self.grid.set_recipes(recipes)

    def set_view(self, mode: str) -> None:
        self.grid_button.setChecked(mode == "grid")
        self.list_button.setChecked(mode == "list")
        self.grid.set_mode(mode)

    def _sort_changed(self, _i: int) -> None:
        self.sort = self.sort_combo.current_data()
        self.refresh()

    def selected_recipes(self) -> list[Recipe]:
        return [r for r in self.grid.recipes if r.id in self.grid.selected]

    # library actions
    def load(self, recipe_id: str) -> None:
        recipe = next(r for r in self.grid.recipes if r.id == recipe_id)
        source = self.on_screen()

        def run() -> None:
            self.library.load_into(self.session, recipe_id)
            self.loaded.emit(source)
            self.status.emit(text("status.recipe_loaded", name=recipe.name))
        self._guard(run)

    def toggle_favorite(self, recipe_id: str) -> None:
        def run() -> None:
            recipe = next(r for r in self.grid.recipes if r.id == recipe_id)
            on = self.library.toggle_favorite(recipe_id)
            self.status.emit(text("status.favorited" if on else "status.unfavorited",
                                  name=recipe.name))
        self._guard(run)

    def reorder(self, visible_ids: list[str]) -> None:
        def run() -> None:
            all_ids = [r.id for r in self.library.library.recipes]
            mine = set(visible_ids)
            slots = iter(visible_ids)
            order = [next(slots) if rid in mine else rid for rid in all_ids]
            self.library.reorder(order)
            self.status.emit(text("status.recipe_order_saved"))
        try:
            run()
        except MotionAtelierError as exc:
            self.status.emit(text("status.recipe_order_failed",
                                  reason=issue_text(Issue(exc.code, exc.detail))))
            self.refresh()

    def save_current(self) -> None:
        doc = self.session.current
        name = self.prompt(text("recipes.save.title"), text("recipes.prompt.name"), "", False)
        if not name:
            return

        def run() -> None:
            recipe = self.library.save_content(doc.committed_content, name)
            self.status.emit(text("status.recipe_saved", shape=text(
                f"shape.{doc.kind.value}").lower(), name=recipe.name))
        self._guard(run)

    def rename_selected(self) -> None:
        sel = self.selected_recipes()
        if len(sel) != 1:
            return
        name = self.prompt(text("recipes.rename.title"), text("recipes.prompt.name"), sel[0].name,
                           False)
        if name:
            self._guard(lambda: (self.library.rename(sel[0].id, name),
                                 self.status.emit(text("status.recipe_renamed", name=name))))

    def edit_tags(self) -> None:
        sel = self.selected_recipes()
        if len(sel) != 1:
            return
        value = self.prompt(text("recipes.tags.title"), text("recipes.prompt.tags"),
                            ", ".join(sel[0].tags), True)
        if value is None:
            return
        tags = [t.strip() for t in value.split(",") if t.strip()]
        self._guard(lambda: (self.library.set_tags(sel[0].id, tags),
                             self.status.emit(text("status.recipe_tags", name=sel[0].name))))

    def set_favorite(self, on: bool) -> None:
        def run() -> None:
            for r in self.selected_recipes():
                if r.favorite != on:
                    self.library.toggle_favorite(r.id)
        self._guard(run)

    def delete_selected(self) -> None:
        sel = self.selected_recipes()
        if not sel:
            return
        message = (text("recipes.delete.one") if len(sel) == 1
                   else text("recipes.delete.many", count=len(sel)))
        if not self.confirm(text("recipes.delete.title"), message):
            return
        self._guard(lambda: self.status.emit(text("status.recipes_deleted",
                                                  count=self.library.delete([r.id for r in sel]))))

    def restore_defaults(self) -> None:
        if not self.confirm(text("recipes.restore.title"), text("recipes.restore.message")):
            return
        self._guard(lambda: (self.library.restore_builtins(),
                             self.status.emit(text("status.recipes_restored"))))

    def export_selected(self) -> None:
        sel = self.selected_recipes()
        if not sel:
            return
        path = self.pick_save()
        if not path:
            return

        def run() -> None:
            Path(path).write_bytes(self.library.export([r.id for r in sel]))
            self.status.emit(text("status.recipes_exported", count=len(sel)))
        self._guard(run)

    def import_dialog(self) -> None:
        path = self.pick_open()
        if not path:
            self.status.emit(text("status.import_cancelled"))
            return
        file = Path(path)
        try:
            if file.stat().st_size > MAX_IMPORT_BYTES:
                raise MotionAtelierError("archive_invalid", "too large")
            blob = file.read_bytes()
            review = self.library.review_import(blob)
        except (OSError, MotionAtelierError) as exc:
            code = getattr(exc, "code", "archive_invalid")
            detail = getattr(exc, "detail", str(exc))
            self.status.emit(text("status.import_failed",
                                  reason=issue_text(Issue(code, detail))))
            return
        self.import_review(review)

    def import_review(self, review: LB.ImportReview) -> None:
        choices = self.review(review)
        if choices is None:
            self.status.emit(text("status.import_cancelled"))
            return
        try:
            outcome = self.library.apply_import(review, choices)
        except MotionAtelierError as exc:
            self.status.emit(text("status.import_failed",
                                  reason=issue_text(Issue(exc.code, exc.detail))))
            return
        renamed = sum(1 for v in choices.values() if v == LB.ACTION_RENAME)
        blocked = sum(1 for item in review.items if item.state == LB.INVALID)
        line = text("status.imported", imported=outcome.imported, replaced=outcome.replaced,
                    skipped=outcome.skipped)
        if renamed:
            line += text("status.imported.renamed", count=renamed)
        if blocked:
            line += text("status.imported.blocked", count=blocked)
        self.status.emit(line)

    def menu(self, global_pos: QtCore.QPoint) -> None:
        menu = self.build_menu()
        menu.exec(global_pos)
        menu.deleteLater()

    def build_menu(self) -> QtWidgets.QMenu:
        """The list menu: actions are enabled only when they apply."""
        sel = self.selected_recipes()
        single = len(sel) == 1
        all_fav = bool(sel) and all(r.favorite for r in sel)
        menu = QtWidgets.QMenu(self)
        menu.setObjectName("RecipeMenu")
        menu.addAction(text("recipes.menu.save"), self.save_current)
        menu.addSeparator()
        fav = menu.addAction(text("recipes.menu.unfavorite" if all_fav
                                  else "recipes.menu.favorite"),
                             lambda: self.set_favorite(not all_fav))
        fav.setEnabled(bool(sel))
        menu.addAction(text("recipes.menu.rename"), self.rename_selected).setEnabled(single)
        menu.addAction(text("recipes.menu.tags"), self.edit_tags).setEnabled(single)
        menu.addAction(text("recipes.menu.delete.one" if single else "recipes.menu.delete.many"),
                       self.delete_selected).setEnabled(bool(sel))
        menu.addAction(text("recipes.menu.export"), self.export_selected).setEnabled(bool(sel))
        menu.addSeparator()
        menu.addAction(text("recipes.menu.restore"), self.restore_defaults)
        return menu

    # history drawer
    def set_drawer(self, on: bool) -> None:
        if self.history_toggle.isChecked() != on:
            self.history_toggle.setChecked(on)
            return
        self.drawer.setVisible(on)
        if on:
            total = max(1, self.split.height())
            self.split.setSizes([total * 3 // 5, total * 2 // 5])
        else:
            self._history_selected(None)
        self.drawerToggled.emit(on)

    def refresh_history(self) -> None:
        self.history_grid.set_entries(self.history.view(self.history_sort.current_data()
                                                        or H.VIEW_RECENT))
        self._history_buttons()

    def _history_buttons(self) -> None:
        has = self.history_grid.selected is not None
        self.history_buttons["restore"].setEnabled(has)
        self.history_buttons["delete"].setEnabled(has)
        self.history_buttons["clear"].setEnabled(bool(self.history_grid.entries))

    def _history_selected(self, entry_id: str | None) -> None:
        self._history_buttons()
        if entry_id is None:
            self.historyPreview.emit(None)
            return
        entry = self.history.get(entry_id)
        self.historyPreview.emit(entry.curve)
        self.status.emit(text("status.history_preview", label=entry.label))

    def restore_history(self) -> None:
        entry_id = self.history_grid.selected
        if entry_id is None:
            return
        entry = self.history.get(entry_id)
        source = self.on_screen()

        def run() -> None:
            self.history.restore_into(self.session, entry_id)
            self.historyRestored.emit(source)
            self.status.emit(text("status.history_restored", label=entry.label))
        self._guard(run)

    def delete_history(self) -> None:
        entry_id = self.history_grid.selected
        if entry_id is None:
            return
        self._guard(lambda: self.history.delete([entry_id]))
        self.history_grid.selected = None
        self._history_selected(None)
        self.status.emit(text("status.history_deleted"))

    def clear_history(self) -> None:
        if not self.confirm(text("history.clear.title"), text("history.clear.message")):
            return
        self._guard(self.history.clear)
        self._history_selected(None)
        self.status.emit(text("status.history_cleared"))

    # default choosers and prompts
    def _pick_open(self) -> str:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self.shade().window(),
                                                        text("recipes.import.name"), "",
                                                        ARCHIVE_FILTER)
        return path

    def _pick_save(self) -> str:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self.shade().window(),
                                                        text("recipes.menu.export"),
                                                        "recipes.zip", ARCHIVE_FILTER)
        return path

    def _prompt(self, title: str, label: str, initial: str, allow_empty: bool) -> str | None:
        dialog = dialogs.TextPrompt(title, label, initial, self.shade().window(),
                                    allow_empty=allow_empty)
        try:
            return dialog.value() if dialog.run(self.shade()) else None
        finally:
            dialog.deleteLater()

    def _confirm(self, title: str, message: str) -> bool:
        dialog = dialogs.Confirm(title, message, self.shade().window())
        try:
            return dialog.run(self.shade())
        finally:
            dialog.deleteLater()

    def _review(self, review: LB.ImportReview) -> dict | None:
        dialog = ImportReviewDialog(review, self.shade().window())
        try:
            return dialog.choices() if dialog.run(self.shade()) else None
        finally:
            dialog.deleteLater()

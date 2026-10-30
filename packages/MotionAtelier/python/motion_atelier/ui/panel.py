# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""The root panel::

    Header:   brand mark · product name
              SHAPE [combo]  ● status ..............................  VIEW [Value|Velocity]
    Workspace (splitter, children never collapse):
      Canvas column: edit row (curve toolbar · overlay toggles) / graph frame / preview
                     transport / commit row (Get Keys · Apply To pair · Commit Keys)
      Inspector (220–330 px): Motion Stack · Key Tools · Recipes

The panel renders :class:`~motion_atelier.app.session.EditorSession` state and forwards user
intent to it. Only Get Keys, Commit Keys and the Key Tools reach the scene, through the
session's qualified host services.
"""

from __future__ import annotations

import contextlib
import time
import traceback

from motion_atelier import PRODUCT_NAME, __version__
from motion_atelier.app.commit import APPLY_EACH, APPLY_WHOLE
from motion_atelier.app.document import default_content
from motion_atelier.app.preview import PreviewTransport
from motion_atelier.app.results import Issue
from motion_atelier.app.services import Services, create_services
from motion_atelier.app.session import SHAPE_ORDER, EditorSession
from motion_atelier.app.status import issue_text
from motion_atelier.app.strings import text
from motion_atelier.curve import ops
from motion_atelier.curve.errors import MotionAtelierError
from motion_atelier.generators.base import ShapeKind
from motion_atelier.ui import controls, icons, qt, style
from motion_atelier.ui.canvas import VALUE, VELOCITY, CurveCanvas
from motion_atelier.ui.key_tools import KeyToolsPage
from motion_atelier.ui.motion_stack import MotionStackPage
from motion_atelier.ui.qt import QtCore, QtGui, QtWidgets
from motion_atelier.ui.recipes import RecipesPage

Qt = QtCore.Qt
SETTINGS_DEBOUNCE_MS = 1500
PREVIEW_DEFAULT_SECONDS = 1.0  # not remembered between sessions
PREVIEW_MAX_SECONDS = 30.0
PREVIEW_STEP_SECONDS = 0.5


class _SplitterHandle(QtWidgets.QSplitterHandle):
    def paintEvent(self, _event) -> None:
        p = QtGui.QPainter(self)
        if self.orientation() == Qt.Orientation.Horizontal:
            p.fillRect(QtCore.QRect(1, 0, style.SPLITTER_BAR, self.height()),
                       QtGui.QColor(style.SPLITTER))
        else:
            p.fillRect(QtCore.QRect(0, 1, self.width(), 3), QtGui.QColor(style.SPLITTER))
        p.end()


class Splitter(QtWidgets.QSplitter):
    def createHandle(self) -> QtWidgets.QSplitterHandle:
        return _SplitterHandle(self.orientation(), self)


class _Frame(QtWidgets.QWidget):
    """The graph frame: 1 px mid border with a 4 px inner margin."""

    def paintEvent(self, _event) -> None:
        p = QtGui.QPainter(self)
        p.setPen(QtGui.QColor(style.MID))
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        p.end()


class _Pane(QtWidgets.QStackedWidget):
    """The inspector pane: 1 px #242424 border, no top."""

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        p = QtGui.QPainter(self)
        p.setPen(QtGui.QColor(style.PANE_BORDER))
        r = self.rect().adjusted(0, -1, -1, -1)
        p.drawLine(r.topLeft(), r.bottomLeft())
        p.drawLine(r.bottomLeft(), r.bottomRight())
        p.drawLine(r.topRight(), r.bottomRight())
        p.end()


class SectionAction(QtWidgets.QWidgetAction):
    """A context-menu section header: small dim text followed by a rule."""

    def __init__(self, title: str, parent) -> None:
        super().__init__(parent)
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(widget)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(6)
        label = QtWidgets.QLabel(title, widget)
        font = QtGui.QFont(widget.font())
        font.setPixelSize(11)
        label.setFont(font)
        label.setStyleSheet(f"color: {style.MENU_SECTION};")
        layout.addWidget(label)
        layout.addWidget(controls.Rule(style.MENU_SEPARATOR, widget), 1)
        widget.setFixedHeight(20)
        self.setDefaultWidget(widget)
        self.setEnabled(False)
        self.setText(title)


class MainPanel(QtWidgets.QWidget):
    """Top-level MotionAtelier widget hosted inside a Houdini Python Panel."""

    def __init__(self, session: EditorSession | None = None, services: Services | None = None,
                 dialog_parent=None, parent: QtWidgets.QWidget | None = None,
                 clock=time.monotonic) -> None:
        super().__init__(parent)
        self.setObjectName("MotionAtelierPanel")
        self.setAccessibleName(PRODUCT_NAME)
        self.setWindowTitle(f"{PRODUCT_NAME} {__version__}")
        style.apply(self)
        self.session = session if session is not None else EditorSession(None)
        self.services = services if services is not None else create_services(None)
        self._dialog_parent = dialog_parent
        self._disposed = False
        self._subscribed: list = []
        self.ease_source = text("canvas.source.default")
        self._inspector_width = style.INSPECTOR_DEFAULT
        self._fitted_for: bool | None = None
        self._settings_timer = QtCore.QTimer(self)
        self._settings_timer.setSingleShot(True)
        self._settings_timer.setInterval(SETTINGS_DEBOUNCE_MS)
        self._settings_timer.timeout.connect(self.flush_settings)
        self.transport = PreviewTransport(clock, PREVIEW_DEFAULT_SECONDS)
        self.preview_timer = QtCore.QTimer(self)
        self.preview_timer.setInterval(style.MARKER_REDRAW_MS)
        self.preview_timer.timeout.connect(self._tick_preview)

        # The panel adapts down to its layout's own minimum (about 525 × 330: it fits Houdini's
        # default dock); only hosts smaller than that scroll it instead of clipping it.
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QtWidgets.QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.content = QtWidgets.QWidget()
        self.content.setObjectName("PanelContent")
        self.scroll.setWidget(self.content)
        outer.addWidget(self.scroll)
        root = QtWidgets.QVBoxLayout(self.content)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())
        self.workspace = Splitter(Qt.Orientation.Horizontal, self.content)
        self.workspace.setObjectName("Workspace")
        self.workspace.setHandleWidth(style.SPLITTER_HANDLE)
        self.workspace.setChildrenCollapsible(False)
        self.workspace.addWidget(self._build_canvas_column())
        self.workspace.addWidget(self._build_inspector())
        self.workspace.setStretchFactor(0, 1)
        self.workspace.setStretchFactor(1, 0)
        self.workspace.splitterMoved.connect(self._splitter_moved)
        root.addWidget(self.workspace, 1)

        self._connect()
        self._apply_settings()
        self.session.subscribe(self._on_session)
        for doc in self.session.documents.values():
            doc.subscribe(self._on_document)
            self._subscribed.append(doc)
        self._show_shape(frame=False)
        warnings = self.services.warnings
        if warnings:
            self.show_status(text("status.store_warning", detail=issue_text(
                Issue(warnings[0].code, warnings[0].detail)), more=len(warnings) - 1))
        else:
            self.show_status(text("status.ready"))

    # -- construction ------------------------------------------------------------------------

    def _build_header(self) -> QtWidgets.QWidget:
        header = QtWidgets.QWidget(self.content)
        layout = QtWidgets.QVBoxLayout(header)
        layout.setContentsMargins(style.HEADER_PAD_H, style.HEADER_PAD_V, style.HEADER_PAD_H,
                                  style.HEADER_PAD_V)
        layout.setSpacing(style.HEADER_ROW_GAP)
        wrap1 = QtWidgets.QWidget(header)
        wrap1.setFixedHeight(style.HEADER_ROW1_H)
        row1 = QtWidgets.QHBoxLayout(wrap1)
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(8)
        self.brand = QtWidgets.QLabel(wrap1)
        self.brand.setFixedSize(style.BRAND_SIZE, style.BRAND_SIZE)
        self.brand.setPixmap(icons.brand_mark(style.BRAND_SIZE, 2.0))
        self.brand.setScaledContents(True)
        self.brand.setAccessibleName(PRODUCT_NAME)
        row1.addWidget(self.brand)
        title = QtWidgets.QLabel(PRODUCT_NAME, wrap1)
        title.setFont(style.semibold())
        row1.addWidget(title)
        row1.addStretch(1)
        layout.addWidget(wrap1)
        wrap2 = QtWidgets.QWidget(header)
        wrap2.setFixedHeight(style.HEADER_ROW2_H)
        row2 = QtWidgets.QHBoxLayout(wrap2)
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(6)
        row2.addWidget(QtWidgets.QLabel(text("header.shape"), wrap2))
        self.shape_combo = controls.Combo([text(f"shape.{k.value}") for k in SHAPE_ORDER], wrap2)
        self.shape_combo.set_items(self.shape_combo.items(), list(SHAPE_ORDER))
        controls.set_tip(self.shape_combo, text("header.shape.tip"), text("header.shape.name"))
        self.shape_combo.currentIndexChanged.connect(
            lambda _i: self.switch_shape(self.shape_combo.current_data()))
        row2.addWidget(self.shape_combo)
        row2.addSpacing(4)
        self.status_dot = controls.StatusDot(wrap2)
        row2.addWidget(self.status_dot)
        self.status = controls.ElidedLabel(wrap2)
        self.status.setObjectName("Status")
        self.status.setAccessibleDescription(text("header.status"))
        row2.addWidget(self.status, 1)
        row2.addSpacing(4)
        row2.addWidget(QtWidgets.QLabel(text("header.view"), wrap2))
        view = QtWidgets.QWidget(wrap2)
        view.setAccessibleName(text("header.view.group"))
        vl = QtWidgets.QHBoxLayout(view)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)
        self.view_buttons: dict[str, controls.PushButton] = {}
        for mode, key in ((VALUE, "header.view.value"), (VELOCITY, "header.view.velocity")):
            button = controls.PushButton(text(key), None, view)
            button.set_fixed_width(button.natural_width() + 8)
            button.setCheckable(True)
            controls.set_tip(button, text(f"{key}.tip"), text(key))
            button.clicked.connect(lambda _c=False, m=mode: self.set_view(m))
            vl.addWidget(button)
            self.view_buttons[mode] = button
        row2.addWidget(view)
        layout.addWidget(wrap2)
        return header

    def _tool(self, key: str, icon: str) -> controls.ToolButton:
        button = controls.ToolButton(icon, self.toolbar)
        controls.set_tip(button, text(f"toolbar.{key}.tip"), text(f"toolbar.{key}"))
        button.setObjectName(f"toolbar_{key}")
        self.toolbar_layout.addWidget(button)
        self.toolbar_buttons[key] = button
        return button

    def _divider(self) -> None:
        divider = controls.Divider(self.toolbar)
        self.toolbar_layout.addWidget(divider)
        self.toolbar_dividers.append(divider)

    def _build_canvas_column(self) -> QtWidgets.QWidget:
        column = QtWidgets.QWidget(self.content)
        column.setMinimumWidth(style.CANVAS_MIN_W)
        layout = QtWidgets.QVBoxLayout(column)
        layout.setContentsMargins(style.CANVAS_COL_PAD_H, style.CANVAS_COL_PAD_V,
                                  style.CANVAS_COL_PAD_H, style.CANVAS_COL_PAD_V)
        layout.setSpacing(style.CANVAS_COL_GAP)
        # Edit row: toolbar left, overlay toggles right, 4 px padding and a mid rule below.
        self.edit_row = QtWidgets.QWidget(column)
        er = QtWidgets.QHBoxLayout(self.edit_row)
        er.setContentsMargins(0, 0, 0, 0)
        er.setSpacing(8)
        self.toolbar = QtWidgets.QWidget(self.edit_row)
        self.toolbar.setAccessibleName(text("toolbar.name"))
        self.toolbar_layout = QtWidgets.QHBoxLayout(self.toolbar)
        self.toolbar_layout.setContentsMargins(0, 0, 0, 0)
        self.toolbar_layout.setSpacing(style.TOOLBAR_GAP)
        self.toolbar_buttons: dict[str, QtWidgets.QAbstractButton] = {}
        self.toolbar_dividers: list[controls.Divider] = []
        self._tool("frame", "frame")
        self._divider()
        self._tool("smooth", "smooth")
        self._tool("break", "break")
        self._tool("flatten", "flatten")
        self._divider()
        self._tool("flip_time", "flip_time")
        self._tool("flip_value", "flip_value")
        self._divider()
        self._tool("insert", "insert")
        self._tool("delete", "delete")
        self.convert_button = controls.PushButton(text("toolbar.convert"), "convert",
                                                  self.toolbar)
        controls.set_tip(self.convert_button, text("toolbar.convert.tip"), text("toolbar.convert"))
        self.toolbar_layout.addWidget(self.convert_button)
        er.addWidget(self.toolbar)
        er.addStretch(1)
        self.overlays = QtWidgets.QWidget(self.edit_row)
        self.overlays.setAccessibleName(text("overlay.name"))
        ol = QtWidgets.QHBoxLayout(self.overlays)
        ol.setContentsMargins(0, 0, 0, 0)
        ol.setSpacing(8)
        self.overlay_checks: dict[str, controls.CheckBox] = {}
        for key, color, dashed in (("output", style.color(style.HIGHLIGHT), False),
                                   ("base", style.color(style.TEXT, 120), True),
                                   ("velocity", style.color(style.TEXT, 130), False)):
            check = controls.CheckBox(text(f"overlay.{key}"), self.overlays, checked=True,
                                      swatch=lambda r, c=color, d=dashed: icons.swatch(c, d, r))
            controls.set_tip(check, text(f"overlay.{key}.tip"), text(f"overlay.{key}.name"))
            check.toggled.connect(lambda on, k=key: self._overlay(k, on))
            ol.addWidget(check)
            self.overlay_checks[key] = check
        er.addWidget(self.overlays)
        edit_box = QtWidgets.QVBoxLayout()
        edit_box.setSpacing(4)
        edit_box.addWidget(self.edit_row)
        edit_box.addWidget(controls.Rule(style.MID, column))
        layout.addLayout(edit_box)
        frame = _Frame(column)
        fl = QtWidgets.QVBoxLayout(frame)
        fl.setContentsMargins(5, 5, 5, 5)
        self.canvas = CurveCanvas(frame)
        fl.addWidget(self.canvas)
        layout.addWidget(frame, 1)
        # Preview transport (wraps in a narrow canvas).
        transport = QtWidgets.QWidget(column)
        tl = controls.FlowLayout(transport, 6, 2)
        self.play_button = controls.ToolButton("play", transport)
        self.play_button.setCheckable(True)
        controls.set_tip(self.play_button, text("preview.play"), text("preview.play"))
        tl.addWidget(self.play_button)
        self.restart_button = controls.ToolButton("restart", transport)
        controls.set_tip(self.restart_button, text("preview.restart.tip"),
                         text("preview.restart"))
        tl.addWidget(self.restart_button)
        label = QtWidgets.QLabel(text("preview.duration"), transport)
        label.setFixedHeight(style.TOOL_BUTTON)
        tl.addWidget(label)
        holder = QtWidgets.QWidget(transport)
        holder.setFixedHeight(style.TOOL_BUTTON)
        hl = QtWidgets.QVBoxLayout(holder)
        hl.setContentsMargins(0, 2, 0, 2)
        self.duration = controls.SpinBox(PREVIEW_DEFAULT_SECONDS, 0.1, PREVIEW_MAX_SECONDS,
                                         decimals=2, step=PREVIEW_STEP_SECONDS, suffix=" s",
                                         width=60, parent=holder)
        controls.set_tip(self.duration, text("preview.duration.tip"), text("preview.duration"))
        hl.addWidget(self.duration)
        tl.addWidget(holder)
        layout.addWidget(transport)
        # Commit row: Get Keys · stretch · Apply To pair · Commit Keys.
        layout.addWidget(controls.Rule(style.MID, column))
        commit = QtWidgets.QHBoxLayout()
        commit.setContentsMargins(0, 0, 0, 0)
        commit.setSpacing(6)
        self.get_keys_button = controls.PushButton(text("commit.get_keys"), "import", column)
        controls.set_tip(self.get_keys_button, text("commit.get_keys.tip"),
                         text("commit.get_keys"))
        commit.addWidget(self.get_keys_button)
        commit.addStretch(1)
        pair = QtWidgets.QWidget(column)
        pair.setAccessibleName(text("commit.apply.name"))
        pl = QtWidgets.QHBoxLayout(pair)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(0)
        self.apply_buttons: dict[str, controls.ToolButton] = {}
        for mode, icon, key in ((APPLY_EACH, "interval", "commit.apply.each"),
                                (APPLY_WHOLE, "range", "commit.apply.whole")):
            button = controls.ToolButton(icon, pair, accent_checked=True)
            button.setCheckable(True)
            controls.set_tip(button, text(f"{key}.tip"), text(key))
            button.clicked.connect(lambda _c=False, m=mode: self.set_apply_mode(m))
            pl.addWidget(button)
            self.apply_buttons[mode] = button
        commit.addWidget(pair)
        self.commit_button = controls.PushButton(text("commit.commit"), "check", column,
                                                 icon_color=style.INK, height=style.COMMIT_H)
        self.commit_button.setObjectName("CommitButton")
        controls.set_tip(self.commit_button, text("commit.commit.tip"), text("commit.commit"))
        commit.addWidget(self.commit_button)
        layout.addLayout(commit)
        return column

    def _build_inspector(self) -> QtWidgets.QWidget:
        inspector = QtWidgets.QWidget(self.content)
        inspector.setMinimumWidth(style.INSPECTOR_MIN)
        inspector.setMaximumWidth(style.INSPECTOR_MAX)
        layout = QtWidgets.QVBoxLayout(inspector)
        layout.setContentsMargins(6, 4, 6, 6)
        layout.setSpacing(0)
        self.tabs = controls.IconTabBar([("layers", text("tab.stack")),
                                         ("wrench", text("tab.tools")),
                                         ("grid", text("tab.recipes"))], inspector)
        self.tabs.setAccessibleName(text("tab.name"))
        layout.addWidget(self.tabs)
        self.pane = _Pane(inspector)
        self.stack_page = MotionStackPage()
        self.tools_page = KeyToolsPage(self.session, lambda: self)
        self.recipes_page = RecipesPage(self.session, self.services.library,
                                        self.services.history, lambda: self,
                                        self.canvas.displayed_curve)
        for page, scroll in ((self.stack_page, True), (self.tools_page, True),
                             (self.recipes_page, False)):
            if scroll:
                area = QtWidgets.QScrollArea()
                area.setWidget(page)
                area.setWidgetResizable(True)
                area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
                area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                self.pane.addWidget(area)
            else:
                self.pane.addWidget(page)
        self.tabs.currentChanged.connect(self.pane.setCurrentIndex)
        layout.addWidget(self.pane, 1)
        self.inspector = inspector
        return inspector

    def _connect(self) -> None:
        tb = self.toolbar_buttons
        tb["frame"].clicked.connect(lambda: self.canvas.auto_frame(True))
        tb["smooth"].clicked.connect(lambda: self._edit("undo.smooth", ops.smooth_tangents))
        tb["break"].clicked.connect(lambda: self._edit("undo.break", ops.break_tangents))
        tb["flatten"].clicked.connect(lambda: self._edit("undo.flatten", ops.flatten_tangents))
        tb["flip_time"].clicked.connect(lambda: self._edit("undo.flip_time", ops.flip_time,
                                                           frame=True))
        tb["flip_value"].clicked.connect(lambda: self._edit("undo.flip_value", ops.flip_value,
                                                            frame=True))
        tb["insert"].clicked.connect(lambda: self.insert_knot())
        tb["delete"].clicked.connect(self.delete_knots)
        self.convert_button.clicked.connect(self.convert_to_ease)
        self.get_keys_button.clicked.connect(self.get_keys)
        self.commit_button.clicked.connect(self.commit)
        self.play_button.clicked.connect(lambda: self.set_playing(not self.transport.playing))
        self.restart_button.clicked.connect(self.restart_preview)
        self.duration.valueChanged.connect(self._duration_changed)
        self.canvas.knotActivated.connect(self.inspect_knot)
        self.canvas.menuRequested.connect(self._canvas_menu)
        self.canvas.undoRequested.connect(self.undo)
        self.canvas.redoRequested.connect(self.redo)
        self.canvas.deleteRequested.connect(self.delete_knots)
        self.canvas.gestureFinished.connect(self.refresh_toolbar)
        self.tabs.currentChanged.connect(lambda _i: self.canvas.snap())
        for page in (self.stack_page, self.tools_page, self.recipes_page):
            page.status.connect(self.show_status)
        self.stack_page.stack.layersShown.connect(self.canvas.set_layers)
        self.stack_page.stack.renameRequested.connect(self._rename_layer)
        self.recipes_page.loaded.connect(self._after_load)
        self.recipes_page.historyPreview.connect(self.canvas.set_preview_curve)
        self.recipes_page.historyRestored.connect(self._after_history_restore)
        self.recipes_page.drawerToggled.connect(lambda on: self.remember(history_open=on))
        self.session.commit_listeners.append(self._record_history)

    # -- settings ----------------------------------------------------------------------------

    def dialog_parent(self):
        parent = self._dialog_parent() if self._dialog_parent is not None else None
        return parent if parent is not None else self.window()

    def _apply_settings(self) -> None:
        s = self.services.preferences.settings
        with contextlib.suppress(ValueError):
            self.session.switch_shape(ShapeKind(s.last_shape))
        self.session.set_apply_mode(s.apply_mode)
        self.session.fit_tolerance_pct = s.fit_tolerance_pct
        self.canvas.set_mode(s.view_mode)
        self._inspector_width = s.inspector_width
        if s.history_open:
            self.recipes_page.set_drawer(True)

    def remember(self, **changes) -> None:
        self.services.preferences.update(**changes)
        if self.services.preferences.dirty:
            self._settings_timer.start()

    def flush_settings(self) -> None:
        try:
            self.services.preferences.flush()
        except MotionAtelierError as exc:
            self.show_status(text("status.store_warning", detail=issue_text(
                Issue(exc.code, exc.detail)), more=0))

    def _record_history(self, doc, curve, result) -> None:
        """Record every verified commit; a failed history write never fails the commit."""
        label = self.ease_source if doc.kind == ShapeKind.EASE else text(
            f"shape.{doc.kind.value}")
        try:
            self.services.history.record(curve, doc.kind, label)
        except MotionAtelierError as exc:
            self.show_status(text("status.store_warning", detail=issue_text(
                Issue(exc.code, exc.detail)), more=0))

    # -- session binding ---------------------------------------------------------------------

    def _on_session(self, event: str) -> None:
        if event == "shape":
            self._show_shape(frame=True)
            self.remember(last_shape=self.session.current_kind.value)
        elif event == "status":
            self.show_status(self.session.status)
        elif event == "apply_mode":
            self._sync_apply()
            self.remember(apply_mode=self.session.apply_mode)

    def _on_document(self, _doc) -> None:
        if not self._disposed:
            self.refresh_toolbar()

    def _show_shape(self, frame: bool) -> None:
        doc = self.session.current
        self.shape_combo.set_current_index(self.shape_combo.find_data(doc.kind), silent=True)
        self.canvas.set_document(doc, frame=True, animate=frame)
        self.stack_page.set_document(doc)
        self.recipes_page.refresh()
        self._sync_apply()
        self._sync_view()
        self.get_keys_button.setVisible(doc.kind == ShapeKind.EASE)
        self._update_accessible_source()
        self.refresh_toolbar()

    def _sync_apply(self) -> None:
        for mode, button in self.apply_buttons.items():
            button.setChecked(mode == self.session.apply_mode)

    def _sync_view(self) -> None:
        for mode, button in self.view_buttons.items():
            button.setChecked(mode == self.canvas.mode())

    def _update_accessible_source(self) -> None:
        doc = self.session.current
        name = self.ease_source if doc.kind == ShapeKind.EASE else text(f"shape.{doc.kind.value}")
        self.canvas.setAccessibleName(name)

    def refresh_toolbar(self) -> None:
        if self._disposed:
            return
        doc = self.session.current
        editable = doc.kind == ShapeKind.EASE
        curve = doc.base_curve()
        last = len(curve.knots) - 1
        interior = [i for i in doc.selection if 0 < i < last]
        tb = self.toolbar_buttons
        for key in ("smooth", "break", "flatten", "flip_time", "flip_value", "insert", "delete"):
            tb[key].setVisible(editable)
        for divider in self.toolbar_dividers[1:]:
            divider.setVisible(editable)
        self.convert_button.setVisible(not editable)
        busy = doc.in_gesture
        tb["smooth"].setEnabled(not busy and any(not curve.knots[i].smooth for i in interior))
        tb["break"].setEnabled(not busy and any(curve.knots[i].smooth for i in interior))
        tb["flatten"].setEnabled(not busy and bool(doc.selection))
        tb["delete"].setEnabled(not busy and bool(interior))
        for key in ("flip_time", "flip_value", "insert", "frame"):
            tb[key].setEnabled(not busy)
        # Scene actions depend only on a host: the session cancels any open gesture before
        # it reads or writes the scene, so a gesture can never leave them stuck disabled.
        host = self.session.host is not None
        self.get_keys_button.setEnabled(host)
        self.commit_button.setEnabled(host)
        self.tools_page.set_available(host)
        if self._fitted_for != editable:  # the toolbar's width changes with the shape kind
            self._fitted_for = editable
            self._fit()

    # -- responsive rules --------------------------------------------------------------------

    def _fit(self) -> None:
        # Overlay toggles show their names only while everything fits the edit row.
        named = sum(c.width_for(True) for c in self.overlay_checks.values()) + 16
        needed = self.toolbar.sizeHint().width() + 8 + named
        fits = needed <= self.edit_row.width()
        for check in self.overlay_checks.values():
            check.set_text_visible(fits)
        # Get Keys and Commit Keys share one width: 110 + 50 · progress, never below a label.
        width = self.content.width()
        progress = min(1.0, max(0.0, (width - style.PANEL_MIN_W)
                                / (style.APPLY_MAX_AT - style.PANEL_MIN_W)))
        floor = max(self.get_keys_button.natural_width(), self.commit_button.natural_width())
        shared = max(style.APPLY_MIN + round((style.APPLY_MAX - style.APPLY_MIN) * progress),
                     floor)
        self.get_keys_button.set_fixed_width(shared)
        self.commit_button.set_fixed_width(shared)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place_splitter()
        self._fit()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._place_splitter()
        self._fit()

    def _place_splitter(self) -> None:
        total = self.workspace.width() - style.SPLITTER_HANDLE
        if total <= 0:
            return
        width = min(self._inspector_width, style.INSPECTOR_MAX,
                    max(style.INSPECTOR_MIN, total - style.CANVAS_MIN_W))
        width = max(style.INSPECTOR_MIN, width)
        self.workspace.setSizes([total - width, width])

    def _splitter_moved(self, _pos: int, _index: int) -> None:
        width = min(style.INSPECTOR_MAX, max(style.INSPECTOR_MIN, self.workspace.sizes()[1]))
        self._inspector_width = width
        self.remember(inspector_width=width)
        self._fit()

    # -- status ------------------------------------------------------------------------------

    def show_status(self, message: str) -> None:
        self.status.set_full_text(message)

    def status_text(self) -> str:
        return self.status.full_text()

    def _guard(self, fn, *args):
        try:
            return fn(*args)
        except Exception:  # slot boundary: keep the panel alive
            traceback.print_exc()
            self.show_status(text("status.unexpected"))
            return None

    # -- commands ----------------------------------------------------------------------------

    def switch_shape(self, kind: ShapeKind | None) -> None:
        if kind is not None:
            self._guard(self.session.switch_shape, kind)

    def set_view(self, mode: str) -> None:
        self.canvas.set_mode(mode)
        self._sync_view()
        self.remember(view_mode=mode)

    def set_apply_mode(self, mode: str) -> None:
        self.session.set_apply_mode(mode)
        self._sync_apply()

    def _overlay(self, key: str, on: bool) -> None:
        self.canvas.set_overlays(**{key: on})

    def get_keys(self) -> None:
        result = self._guard(self.session.get_keys)
        if result is not None and result.loaded:
            self.ease_source = text("canvas.source.selected_keys")
            self._update_accessible_source()
            self.canvas.auto_frame(True)

    def commit(self) -> None:
        self._guard(self.session.commit_keys)

    def undo(self) -> None:
        if self.session.current.undo() is not None:
            self.canvas.auto_frame(False)

    def redo(self) -> None:
        if self.session.current.redo() is not None:
            self.canvas.auto_frame(False)

    def _edit(self, label_key: str, fn, frame: bool = False) -> bool:
        doc = self.session.current
        if doc.kind != ShapeKind.EASE:
            return False
        try:
            result = fn(doc.base_curve(), doc.selection)
        except MotionAtelierError as exc:
            self.show_status(text("status.edit_rejected", reason=text(f"curve_error.{exc.code}")))
            return False
        changed = doc.apply(text(label_key), doc.content.with_curve(result.curve),
                            result.selection)
        if changed:
            self.show_status(text("status.local_edit", label=text(label_key)))
            if frame:
                self.canvas.auto_frame(True)
        return changed

    def insert_knot(self, at: float | None = None) -> None:
        """Halfway along the segment after the first selected knot (the segment before it for
        the last knot; the longest segment with no selection), or at ``at``."""
        def insert(curve, selection):
            xs = curve.xs
            if at is not None:
                return ops.insert_knot(curve, at)
            if selection:
                i = min(min(selection), len(xs) - 2)
            else:
                i = max(range(len(xs) - 1), key=lambda j: (xs[j + 1] - xs[j], -j))
            return ops.insert_knot(curve, 0.5 * (xs[i] + xs[i + 1]))

        self._edit("undo.insert_knot", insert, frame=True)

    def delete_knots(self) -> None:
        self._edit("undo.delete_knots", ops.delete_knots, frame=True)

    def restore_default(self) -> None:
        doc = self.session.current
        if doc.apply(text("undo.restore_default"), default_content(doc.kind), frozenset()):
            if doc.kind == ShapeKind.EASE:
                self.ease_source = text("canvas.source.default")
                self._update_accessible_source()
            self.show_status(text("status.local_edit", label=text("undo.restore_default")))
            self.canvas.auto_frame(True)

    def convert_to_ease(self) -> None:
        source = self.session.current_kind
        if source == ShapeKind.EASE:
            return
        if self._guard(self.session.convert_to_ease) is not None:
            shape = text(f"shape.{source.value}")
            self.ease_source = text("canvas.source.converted", shape=shape)
            self._update_accessible_source()
            self.show_status(text("status.converted", shape=shape))

    def inspect_knot(self, index: int) -> None:
        """Select the knot, bring Motion Stack forward, expand the disclosure, scroll it into
        view and focus Time (Value on an endpoint)."""
        doc = self.session.current
        if doc.kind != ShapeKind.EASE:
            return
        doc.set_selection({index})
        self.tabs.set_current(0)
        knot = self.stack_page.knot
        knot.set_expanded(True)
        area = self.pane.widget(0)
        if isinstance(area, QtWidgets.QScrollArea):
            QtCore.QTimer.singleShot(0, lambda: area.ensureWidgetVisible(knot))
        knot.focus_first()

    def save_recipe(self) -> None:
        self.tabs.set_current(2)
        self.recipes_page.save_current()

    def _after_load(self, source) -> None:
        doc = self.session.current
        grid = self.recipes_page.grid
        if doc.kind == ShapeKind.EASE:
            names = [r.name for r in grid.recipes if r.id == grid.current]
            if names:
                self.ease_source = names[0]
        self._update_accessible_source()
        self.canvas.morph_from(source)

    def _after_history_restore(self, source) -> None:
        entry_id = self.recipes_page.history_grid.selected
        if entry_id is not None:
            self.ease_source = self.services.history.get(entry_id).label
        self._update_accessible_source()
        self.canvas.set_preview_curve(None)
        self.canvas.morph_from(source)

    def _rename_layer(self, initial: str, done) -> None:
        name = self.recipes_page.prompt(text("stack.rename.title"), text("recipes.prompt.name"),
                                        initial, False)
        if name:
            done(name)

    # -- preview transport -------------------------------------------------------------------

    def set_playing(self, on: bool) -> None:
        if on and not self.transport.playing:
            self.transport.play()
        elif not on and self.transport.playing:
            self.transport.pause()
        playing = self.transport.playing
        self.play_button.setChecked(playing)
        self.play_button.set_icon("pause" if playing else "play")
        name = text("preview.pause" if playing else "preview.play")
        controls.set_tip(self.play_button, name, name)
        if playing:
            self.preview_timer.start()
            self.canvas.set_marker(self.transport.phase())
        else:
            self.preview_timer.stop()
            self.canvas.set_marker(None)

    def restart_preview(self) -> None:
        self.transport.restart()
        if self.transport.playing:
            self.canvas.set_marker(self.transport.phase())

    def _duration_changed(self, seconds: float) -> None:
        self.transport.set_duration(seconds)
        if self.transport.playing:
            self.transport.restart()

    def _tick_preview(self) -> None:
        if self.transport.playing:
            self.canvas.set_marker(self.transport.phase())

    # -- canvas context menu -----------------------------------------------------------------

    def build_canvas_menu(self, x: float) -> QtWidgets.QMenu:
        doc = self.session.current
        menu = QtWidgets.QMenu(self)
        menu.setObjectName("CanvasMenu")
        velocity = qt.QAction(text("menu.show_velocity"), menu)
        velocity.setCheckable(True)
        velocity.setChecked(self.overlay_checks["velocity"].isChecked())
        velocity.triggered.connect(lambda: self.overlay_checks["velocity"].toggle())
        if doc.kind != ShapeKind.EASE:
            menu.addAction(text("menu.convert"), self.convert_to_ease)
            menu.addSeparator()
            menu.addAction(velocity)
            return menu
        curve = doc.base_curve()
        last = len(curve.knots) - 1
        sel = sorted(doc.selection)
        interior = [i for i in sel if 0 < i < last]
        menu.addAction(SectionAction(text("menu.knot"), menu))
        menu.addAction(text("menu.insert_here"), lambda: self.insert_knot(x))
        menu.addAction(text("menu.inspect_knot"),
                       lambda: self.inspect_knot(sel[0])).setEnabled(len(sel) == 1)
        menu.addAction(text("menu.delete_knots"), self.delete_knots).setEnabled(bool(interior))
        menu.addAction(SectionAction(text("menu.tangents"), menu))
        menu.addAction(text("menu.smooth"),
                       lambda: self._edit("undo.smooth", ops.smooth_tangents)).setEnabled(
            any(not curve.knots[i].smooth for i in interior))
        menu.addAction(text("menu.break"),
                       lambda: self._edit("undo.break", ops.break_tangents)).setEnabled(
            any(curve.knots[i].smooth for i in interior))
        menu.addAction(text("menu.flatten"),
                       lambda: self._edit("undo.flatten", ops.flatten_tangents)).setEnabled(
            bool(sel))
        menu.addAction(SectionAction(text("menu.curve"), menu))
        menu.addAction(text("menu.flip_time"),
                       lambda: self._edit("undo.flip_time", ops.flip_time, frame=True))
        menu.addAction(text("menu.flip_value"),
                       lambda: self._edit("undo.flip_value", ops.flip_value, frame=True))
        menu.addAction(text("menu.restore_default"), self.restore_default).setEnabled(
            doc.committed_content != default_content(doc.kind))
        menu.addAction(text("menu.save_recipe"), self.save_recipe)
        menu.addAction(SectionAction(text("menu.view"), menu))
        menu.addAction(text("menu.frame"), lambda: self.canvas.auto_frame(True))
        menu.addAction(velocity)
        return menu

    def _canvas_menu(self, global_pos: QtCore.QPoint, x: float) -> None:
        menu = self.build_canvas_menu(x)
        menu.exec(global_pos)
        menu.deleteLater()

    # -- lifecycle ---------------------------------------------------------------------------

    def dispose(self) -> None:
        """Release timers and callbacks. Safe to call more than once."""
        if self._disposed:
            return
        self._disposed = True
        self._settings_timer.stop()
        self.preview_timer.stop()
        self.flush_settings()
        self.recipes_page.dispose()
        if self._record_history in self.session.commit_listeners:
            self.session.commit_listeners.remove(self._record_history)
        self.session.unsubscribe(self._on_session)
        for doc in self._subscribed:
            doc.unsubscribe(self._on_document)
        self._subscribed.clear()
        self.canvas.set_document(None, frame=False)
        self.stack_page.set_document(None)

    @property
    def disposed(self) -> bool:
        return self._disposed

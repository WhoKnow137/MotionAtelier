# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Design tokens of the MotionAtelier interface.

The panel draws itself with these fixed values rather than the host palette, so it looks
the same in every Houdini session. Values are logical pixels at 1× scale.
"""

from __future__ import annotations

from motion_atelier.ui.qt import QtCore, QtGui, QtWidgets

# -- palette -----------------------------------------------------------------------------------
WINDOW = "#3a3a3a"
BASE = "#2e2e2e"
ALT_BASE = "#333333"
TEXT = "#cccccc"
TEXT_DISABLED = "#a8a8a8"
HIGHLIGHT = "#b98620"
HIGHLIGHTED_TEXT = "#ffffff"
MID = "#595959"
MIDLIGHT = "#cbcbcb"
PANE_BORDER = "#242424"
LIST_BORDER = "#1c1c1c"
BUTTON_BORDER = "#2a2a2a"
FIELD_DARK = "#131313"
FIELD_DARK_BORDER = "#242424"
LINE_EDIT_BORDER = "#0e0e0e"
FIELD = "#2e2e2e"
FIELD_BORDER = "#2a2a2a"
FIELD_TOP = "#2b2b2b"
SLIDER_FILL = "#004a98"
SLIDER_FILL_TOP = "#4c6680"
SLIDER_FILL_DISABLED = "#3c4a58"
SPLITTER = "#494949"
GLYPH = "#c4ccd0"
GLYPH_DISABLED = "#697074"
INK = "#252015"
FAVORITE = "#ffd22d"
VIEW_ICON = "#969696"
MENU_BORDER = "#1f1f1f"
MENU_SEPARATOR = "#4c4c4c"
MENU_DISABLED = "#7a7a7a"
MENU_SECTION = "#8f8f8f"
TOOLTIP_BG = "#262626"
TOOLTIP_BORDER = "#111111"
DIALOG_TITLE = "#2b2b2b"
DIALOG_CLOSE_HOVER = "#c42b1c"
CHECK_MARK = "#aaaaaa"

# -- bevels: (outline, gradient top, gradient bottom, inner top, inner bottom) -----------------
PUSH = ("#2a2a2a", "#737373", "#626262", "#848484", "#747474")
PUSH_HOVER = ("#2a2a2a", "#7d7d7d", "#6b6b6b", "#848484", "#747474")
PUSH_DOWN = ("#2a2a2a", "#5a5a5a", "#5a5a5a", "#6d6d6d", "#6d6d6d")
TOOL = ("#000000", "#555555", "#3b3b3b")
TOOL_HOVER = ("#000000", "#5f5f5f", "#444444")
TOOL_DOWN = ("#000000", "#3b3b3b", "#4b4b4b")
TAB = ("#000000", "#535353", "#313131")
TAB_CURRENT = ("#000000", "#545454", "#3a3a3a")
SLIDER_HANDLE = ("#000000", "#2a2a2a", "#545454", "#3c3c3c")  # outline, top edge, top, bottom
SCROLL_TRACK = "#3a3a3a"
SCROLL_THUMB = "#484848"
SCROLL_THUMB_EDGE = "#2e2e2e"
SCROLL_ARROW = ("#848484", "#5a5a5a")

# -- typography --------------------------------------------------------------------------------
FONT_FAMILY = "Segoe UI"
FONT_POINTS = 9.0
AXIS_FONT_SCALE = 0.85
AXIS_FONT_MIN_POINTS = 6.5

# -- motion ------------------------------------------------------------------------------------
ANIMATION_MS = 150
MARKER_REDRAW_MS = 33
TOOLTIP_DELAY_MS = 650

# -- metrics -----------------------------------------------------------------------------------
PANEL_MIN_W, PANEL_MIN_H = 700, 520
HEADER_PAD_V, HEADER_PAD_H, HEADER_ROW_GAP = 8, 12, 6
HEADER_ROW1_H, HEADER_ROW2_H = 22, 24
BRAND_SIZE = 22
CANVAS_MIN_W = 300
CANVAS_COL_PAD_V, CANVAS_COL_PAD_H, CANVAS_COL_GAP = 8, 10, 6
INSPECTOR_MIN, INSPECTOR_MAX, INSPECTOR_DEFAULT = 220, 330, 275
SPLITTER_HANDLE, SPLITTER_BAR = 5, 4
TOOL_BUTTON = 26
TOOL_BUTTON_SMALL = 24
ICON = 16
ICON_SMALL = 14
TAB_ICON = 18
TAB_H = 22
PUSH_H = 22
COMMIT_H = 28
COMBO_H = 24
FIELD_H = 22
LINE_EDIT_H = 21
TOOLBAR_GAP = 4
DIVIDER_W, DIVIDER_H = 9, 20
APPLY_MIN, APPLY_MAX = 110, 160
APPLY_MAX_AT = 1400
GRAPH_PAD = 24
CANVAS_MIN = (240, 120)  # short docks: the graph shrinks before anything scrolls
GRID_DIVISIONS = 5
HIT_RADIUS = 7.0
FINE_DRAG_SCALE = 0.1
INFLUENCE_MIN_OFFSET_PX = 10.0
MORPH_SAMPLES = 129
UNDO_LIMIT = 100
MENU_ITEM_H = 22
MENU_ITEM_PAD = 24
TOOLTIP_MAX_W = 360
DIALOG_TITLE_H = 30
DIALOG_PAD = 11
DIALOG_BUTTON_MIN = 75
DIALOG_SHADE_ALPHA = 64  # 25 % black
PROMPT_FIELD_W = 240


def color(hex_value: str, alpha: int = 255) -> QtGui.QColor:
    """A colour from ``#rrggbb`` with an alpha out of 255 (``color@a`` in the design tokens)."""
    c = QtGui.QColor(hex_value)
    c.setAlpha(alpha)
    return c


def out_cubic(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return 1.0 - (1.0 - t) ** 3


def ui_font(points: float = FONT_POINTS, weight: QtGui.QFont.Weight | None = None
            ) -> QtGui.QFont:
    font = QtGui.QFont(FONT_FAMILY)
    font.setStyleHint(QtGui.QFont.StyleHint.SansSerif)
    font.setPointSizeF(points)
    if weight is not None:
        font.setWeight(weight)
    return font


def semibold() -> QtGui.QFont:
    return ui_font(weight=QtGui.QFont.Weight.DemiBold)


def palette() -> QtGui.QPalette:
    """A palette carrying the tokens, for widgets that still consult palette roles."""
    p = QtGui.QPalette()
    roles = QtGui.QPalette.ColorRole
    for role, value in ((roles.Window, WINDOW), (roles.Base, BASE), (roles.AlternateBase, ALT_BASE),
                        (roles.Text, TEXT), (roles.WindowText, TEXT), (roles.ButtonText, TEXT),
                        (roles.Button, "#686868"), (roles.Highlight, HIGHLIGHT),
                        (roles.HighlightedText, HIGHLIGHTED_TEXT), (roles.Mid, MID),
                        (roles.Midlight, MIDLIGHT), (roles.ToolTipBase, TOOLTIP_BG),
                        (roles.ToolTipText, TEXT), (roles.PlaceholderText, "#4d4d4d")):
        p.setColor(role, QtGui.QColor(value))
    p.setColor(QtGui.QPalette.ColorGroup.Disabled, roles.ButtonText, QtGui.QColor(TEXT_DISABLED))
    p.setColor(QtGui.QPalette.ColorGroup.Disabled, roles.Text, QtGui.QColor(TEXT))
    p.setColor(QtGui.QPalette.ColorGroup.Disabled, roles.WindowText, QtGui.QColor(TEXT))
    return p


def style_sheet() -> str:
    """Rules for the parts Qt style sheets express exactly: fields, scroll bars, menus,
    tool tips and tables. Buttons, tabs, sliders and check boxes are painted (``controls``)."""
    return f"""
QWidget#MotionAtelierPanel, QDialog#MAFramedDialog {{ background: {WINDOW}; color: {TEXT}; }}
QLabel {{ color: {TEXT}; background: transparent; }}
QLineEdit {{ background: {FIELD}; color: {TEXT}; border: 1px solid {FIELD_BORDER};
    border-top-color: {FIELD_TOP}; padding: 0 6px; selection-background-color: {HIGHLIGHT};
    selection-color: {HIGHLIGHTED_TEXT}; min-height: {FIELD_H - 2}px;
    max-height: {FIELD_H - 2}px; }}
QLineEdit[dark="true"] {{ background: {FIELD_DARK}; border: 1px solid {FIELD_DARK_BORDER}; }}
QLineEdit[prompt="true"] {{ background: {FIELD_DARK}; border: 1px solid {FIELD_DARK_BORDER};
    padding: 0 4px; }}
QLineEdit:disabled {{ color: {TEXT}; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: 0; }}
QScrollBar:vertical {{ background: {SCROLL_TRACK}; width: 14px; margin: 14px 0 14px 0;
    border: 0; }}
QScrollBar:horizontal {{ background: {SCROLL_TRACK}; height: 14px; margin: 0 14px 0 14px;
    border: 0; }}
QScrollBar::handle:vertical {{ background: {SCROLL_THUMB}; border: 1px solid {SCROLL_THUMB_EDGE};
    min-height: 20px; }}
QScrollBar::handle:horizontal {{ background: {SCROLL_THUMB}; border: 1px solid {SCROLL_THUMB_EDGE};
    min-width: 20px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 14px;
    subcontrol-origin: margin; background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
    stop:0 {SCROLL_ARROW[0]}, stop:1 {SCROLL_ARROW[1]}); border: 0; }}
QScrollBar::add-line:vertical {{ subcontrol-position: bottom; }}
QScrollBar::sub-line:vertical {{ subcontrol-position: top; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 14px;
    subcontrol-origin: margin; background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
    stop:0 {SCROLL_ARROW[0]}, stop:1 {SCROLL_ARROW[1]}); border: 0; }}
QScrollBar::add-line:horizontal {{ subcontrol-position: right; }}
QScrollBar::sub-line:horizontal {{ subcontrol-position: left; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
QMenu {{ background: {WINDOW}; color: {TEXT}; border: 1px solid {MENU_BORDER}; padding: 3px 0; }}
QMenu::item {{ height: {MENU_ITEM_H}px; padding: 0 {MENU_ITEM_PAD}px; background: transparent; }}
QMenu::item:selected {{ background: {HIGHLIGHT}; color: {HIGHLIGHTED_TEXT}; }}
QMenu::item:disabled {{ color: {MENU_DISABLED}; background: transparent; }}
QMenu::separator {{ height: 1px; background: {MENU_SEPARATOR}; margin: 3px 1px; }}
QMenu::indicator {{ width: 11px; height: 11px; left: 6px; }}
QToolTip {{ background: {TOOLTIP_BG}; color: {TEXT}; border: 1px solid {TOOLTIP_BORDER};
    padding: 3px 6px; }}
QTableWidget {{ background: {BASE}; color: {TEXT}; border: 1px solid {LIST_BORDER};
    gridline-color: #353535; selection-background-color: transparent; }}
QTableWidget::item {{ border-bottom: 1px solid #353535; padding: 0 6px; }}
QHeaderView::section {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #555555,
    stop:1 #444444); color: {TEXT}; border: 0; border-right: 1px solid {BUTTON_BORDER};
    border-bottom: 1px solid {BUTTON_BORDER}; padding: 3px 6px; height: 22px; }}
"""


def apply(widget: QtWidgets.QWidget) -> None:
    """Give ``widget`` (a panel root or a dialog) the tokens: font, palette and style sheet."""
    widget.setFont(ui_font())
    widget.setPalette(palette())
    widget.setAutoFillBackground(True)
    widget.setStyleSheet(style_sheet())


def device_ratio(widget: QtWidgets.QWidget) -> float:
    return max(1.0, float(widget.devicePixelRatioF()))


def rect_f(widget: QtWidgets.QWidget) -> QtCore.QRectF:
    return QtCore.QRectF(widget.rect())

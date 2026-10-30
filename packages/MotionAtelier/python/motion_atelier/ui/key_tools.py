# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""The Key Tools tab: ten actions in four groups.

Each tool acts on the keys selected in Houdini, applies as one Houdini undo step, rolls back
completely on failure, and reports in the status line. Tools with options ask for them in a
dialog first; Cancel changes nothing.
"""

from __future__ import annotations

from collections.abc import Callable

from motion_atelier.app import tools as T
from motion_atelier.app.session import EditorSession
from motion_atelier.app.strings import text
from motion_atelier.ui import controls, dialogs, style
from motion_atelier.ui.qt import QtCore, QtWidgets

Qt = QtCore.Qt

# (group label id, ((button id, tool, fixed options), ...)) in workflow order.
GROUPS = (
    ("tools.group.inspect", (("inspect", T.TOOL_INSPECT, {}),)),
    ("tools.group.clean", (("remove_interior", T.TOOL_REMOVE_INTERIOR, {}),
                           ("remove_outside", T.TOOL_REMOVE_OUTSIDE, {}),
                           ("reduce", T.TOOL_REDUCE, {}))),
    ("tools.group.retime", (("scale", T.TOOL_SCALE, {}),
                            ("offset_channels", T.TOOL_OFFSET, {}),
                            ("shift_following", T.TOOL_SHIFT, {}),
                            ("match_durations", T.TOOL_MATCH, {}))),
    ("tools.group.snap", (("align_start", T.TOOL_ALIGN, {"edge": "start"}),
                          ("align_end", T.TOOL_ALIGN, {"edge": "end"}))),
)
ACTION_COUNT = sum(len(entries) for _, entries in GROUPS)

# How options are asked for: (tool, parent) -> options or None; replaced in tests.
Ask = Callable[[str, QtWidgets.QWidget], "dict | None"]


def ask_with_dialog(tool: str, shade: QtWidgets.QWidget) -> dict | None:
    dialog = dialogs.TOOL_DIALOGS[tool](shade.window())
    try:
        return dialog.options() if dialog.run(shade) else None
    finally:
        dialog.deleteLater()


class KeyToolsPage(QtWidgets.QWidget):
    status = QtCore.Signal(str)

    def __init__(self, session: EditorSession, shade: Callable[[], QtWidgets.QWidget],
                 parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("KeyToolsPage")
        self.session = session
        self.shade = shade
        self.ask: Ask = ask_with_dialog
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(2)
        row, count = controls.heading_row(text("tools.heading"), self)
        count.setText(text("tools.count", count=ACTION_COUNT))
        layout.addLayout(row)
        self.buttons: dict[str, controls.PushButton] = {}
        for group, entries in GROUPS:
            layout.addSpacing(6)
            label = QtWidgets.QLabel(text(group).upper(), self)
            label.setFont(style.semibold())
            label.setFixedHeight(16)
            label.setAccessibleName(text(group))
            layout.addWidget(label)
            for button_id, tool, fixed in entries:
                name = text(f"tool.{button_id}")
                button = controls.PushButton(name, None, self, left=True)
                button.setToolTip(f"<b>{name}</b><br>{text(f'tool.{button_id}.desc')}")
                button.setAccessibleName(name)
                button.setObjectName(f"tool_{button_id}")
                button.clicked.connect(lambda _c=False, t=tool, f=dict(fixed): self.request(t, f))
                layout.addWidget(button)
                self.buttons[button_id] = button
        self.report = QtWidgets.QLabel(self)
        self.report.setObjectName("InspectReport")
        self.report.setWordWrap(True)
        self.report.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.report.setAccessibleName(text("tools.report"))
        self.report.hide()
        layout.addSpacing(6)
        layout.addWidget(self.report)
        layout.addStretch(1)

    def request(self, tool: str, fixed: dict | None = None):
        options = dict(fixed or {})
        if tool in dialogs.TOOL_DIALOGS:
            chosen = self.ask(tool, self.shade())
            if chosen is None:
                self.status.emit(text("status.tool.cancelled", tool=text(f"tool.{tool}")))
                return None
            options.update(chosen)
        return self.run(tool, options)

    def run(self, tool: str, options: dict | None = None):
        result = self.session.run_tool(tool, options or None)
        if result.read_only:
            self.report.setText("\n".join(f"{label}: {value}" for label, value in result.report))
            self.report.show()
        return result

    def set_available(self, available: bool) -> None:
        for button in self.buttons.values():
            button.setEnabled(available)

# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""The Qt binding Houdini provides: PySide6 (Houdini 21.0, 22.0) or PySide2 (20.0, 20.5).

This is the only module that imports a binding (enforced by tests/architecture). Every name
the panel uses resolves identically in both bindings (checked against PySide2 5.15 in
Houdini 20.5) except the few differences wrapped here.
"""

from __future__ import annotations

import sys

# Prefer the binding Houdini has already loaded: a second Qt in the process hangs it (a
# PySide6 reachable on sys.path inside Qt 5 Houdini 20.5 did). Otherwise try PySide6.
if "PySide2" in sys.modules and "PySide6" not in sys.modules:
    from PySide2 import QtCore, QtGui, QtSvg, QtWidgets

    QT6 = False
else:
    try:
        from PySide6 import QtCore, QtGui, QtSvg, QtWidgets

        QT6 = True
    except ImportError:
        from PySide2 import QtCore, QtGui, QtSvg, QtWidgets

        QT6 = False

BINDING = "PySide6" if QT6 else "PySide2"

# Qt 6 moved QAction from QtWidgets to QtGui.
QAction = QtGui.QAction if QT6 else QtWidgets.QAction


def event_pos(event) -> QtCore.QPointF:
    """Local position of a mouse, hover, wheel or drop event."""
    if QT6:
        return event.position()
    for name in ("localPos", "posF"):  # QMouseEvent / QHoverEvent, QDropEvent
        getter = getattr(event, name, None)
        if getter is not None:
            return QtCore.QPointF(getter())
    return QtCore.QPointF(event.position())  # QWheelEvent (Qt 5.14+)


def global_pos(event) -> QtCore.QPointF:
    """Screen position of a mouse event."""
    if QT6:
        return event.globalPosition()
    return QtCore.QPointF(event.screenPos())


def exec_dialog(dialog) -> int:
    """Run a modal dialog (``exec`` is ``exec_`` in PySide2)."""
    return dialog.exec() if QT6 else dialog.exec_()


def __getattr__(name: str):
    """``QtTest`` for tests and probes only: imported on first use, never by the panel."""
    if name == "QtTest":
        import importlib

        return importlib.import_module(f"{BINDING}.QtTest")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["BINDING", "QT6", "QAction", "QtCore", "QtGui", "QtSvg", "QtWidgets", "event_pos",
           "exec_dialog", "global_pos"]

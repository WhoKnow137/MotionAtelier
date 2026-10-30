# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Wires the host adapter, application layer, and UI together for the Houdini panel.

This is the only module besides ``host/`` that may import ``hou``, and it does so lazily so
that the panel can also be built headless (tests, offscreen smoke checks).

Persistent services (library, settings) are shared by all panels that use the same data
directory, so two open panels never overwrite each other's library. This registry is
the product's only process-wide mutable state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_SERVICES: dict[str, Any] = {}
_IN_MEMORY = "<memory>"


def _houdini_host():
    try:
        import hou  # noqa: F401
    except ImportError:
        return None
    from motion_atelier.host.adapter import HoudiniHost

    return HoudiniHost()


def _default_data_dir() -> Path | None:
    try:
        import hou  # noqa: F401
    except ImportError:
        return None
    from motion_atelier.host.paths import user_data_dir

    return user_data_dir()


def _dialog_parent():
    try:
        import hou
    except ImportError:
        return None
    qt = getattr(hou, "qt", None)
    return qt.mainWindow() if qt is not None else None


def services_for(data_dir: Path | None):
    from motion_atelier.app.services import create_services

    key = str(Path(data_dir).resolve()) if data_dir is not None else _IN_MEMORY
    services = _SERVICES.get(key)
    if services is None:
        services = create_services(data_dir)
        _SERVICES[key] = services
    return services


def forget_services() -> None:
    """Drop cached services (tests; a new session then reloads from disk)."""
    _SERVICES.clear()


def create_session(host=None, services=None):
    from motion_atelier.app.session import EditorSession

    return EditorSession(host)


def create_panel(kwargs: dict[str, Any] | None = None, host=None,
                 data_dir: Path | None | str = "default"):
    """Build and return the root panel widget. Called from the .pypanel script."""
    from motion_atelier.ui.panel import MainPanel

    if host is None:
        host = _houdini_host()
    if data_dir == "default":
        data_dir = _default_data_dir()
    services = services_for(data_dir)
    return MainPanel(create_session(host), services=services, dialog_parent=_dialog_parent)


def destroy_panel(panel) -> None:
    """Dispose of a panel created by :func:`create_panel`. Accepts ``None``."""
    if panel is not None:
        panel.dispose()

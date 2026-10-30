# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Vector icons on a 16-unit grid, rendered with QtSvg.

Strokes are 1.5 units with round caps and joins; ``play``, ``pause``, ``restart`` and ``dot``
are filled with the stroke colour. Icons are rendered on demand at the requested logical size
and device pixel ratio and cached. The brand mark is the only image file
(``resources/brand_mark.svg``); everything else is inline path data.
"""

from __future__ import annotations

from pathlib import Path

from motion_atelier.ui import style
from motion_atelier.ui.qt import QtCore, QtGui, QtSvg

GLYPHS: dict[str, str] = {
    "frame": '<path d="M2 5V2h3M11 2h3v3M14 11v3h-3M5 14H2v-3"/><path d="M5 11C8 11 8 5 11 5"/>',
    "smooth": '<rect x="1.5" y="5.5" width="7" height="5" rx="2.5"/>'
              '<rect x="7.5" y="5.5" width="7" height="5" rx="2.5"/>',
    "break": '<rect x="1" y="5.5" width="6" height="5" rx="2.5"/>'
             '<rect x="9" y="5.5" width="6" height="5" rx="2.5"/><path d="M8 1.5v2M8 12.5v2"/>',
    "flatten": '<path d="M2 8h12"/><circle cx="8" cy="8" r="1.6"/>',
    "flip_time": '<path d="M2 5h12M11 2l3 3-3 3M14 11H2M5 8l-3 3 3 3"/>',
    "flip_value": '<path d="M5 14V2M2 5l3-3 3 3M11 2v12M8 11l3 3 3-3"/>',
    "insert": '<circle cx="8" cy="8" r="6"/><path d="M8 5v6M5 8h6"/>',
    "delete": '<path d="M2.5 4h11M6 4V2h4v2M4 4l.8 10h6.4L12 4M6.8 7v4M9.2 7v4"/>',
    "play": '<path d="M4 2.5L13 8 4 13.5z"/>',
    "pause": '<rect x="4" y="3" width="2.5" height="10"/>'
             '<rect x="9.5" y="3" width="2.5" height="10"/>',
    "restart": '<path d="M3 3v10"/><path d="M13 3L5.5 8 13 13z"/>',
    "plus": '<path d="M8 3v10M3 8h10"/>',
    "chevron_right": '<path d="M6 3.5L10.5 8 6 12.5"/>',
    "chevron_down": '<path d="M3.5 6L8 10.5 12.5 6"/>',
    "arrow_up": '<path d="M8 13V3M4 7l4-4 4 4"/>',
    "arrow_down": '<path d="M8 3v10M4 9l4 4 4-4"/>',
    "duplicate": '<rect x="5.5" y="5.5" width="8" height="8" rx="1.5"/>'
                 '<path d="M3 10.5h-.5v-8h8V3"/>',
    "history": '<path d="M5 2.804A6 6 0 1 0 11 2.804"/>'
               '<path d="M2 3l.6 3.2 3.1-.8M8 5v3l2.5 1.5"/>',
    "import": '<path d="M8 2v8M4.5 6.5L8 10l3.5-3.5M2.5 11v3h11v-3"/>',
    "check": '<path d="M3 8.5L6.5 12 13 4.5"/>',
    "convert": '<path d="M2 13C6 13 6 3 10 3"/><circle cx="2" cy="13" r="1.4"/>'
               '<circle cx="10" cy="3" r="1.4"/><path d="M12.5 9v5M10 11.5h5"/>',
    "dot": '<circle cx="8" cy="8" r="5"/>',
    "search": '<circle cx="7" cy="7" r="4.5"/><path d="M10.5 10.5L14 14"/>',
    "layers": '<path d="M8 2l6 3.5L8 9 2 5.5z"/><path d="M2 8.5L8 12l6-3.5M2 11.5L8 15l6-3.5"/>',
    "wrench": '<path d="M12.5 7.598A3 3 0 1 0 9.5 7.598"/><path d="M9.2 6.8L2.8 13.2"/>',
    "grid": '<rect x="2.5" y="2.5" width="4.5" height="4.5" rx="1"/>'
            '<rect x="9" y="2.5" width="4.5" height="4.5" rx="1"/>'
            '<rect x="2.5" y="9" width="4.5" height="4.5" rx="1"/>'
            '<rect x="9" y="9" width="4.5" height="4.5" rx="1"/>',
    "interval": '<path d="M2 3v10M8 3v10M14 3v10M3.5 8h3M9.5 8h3"/>',
    "range": '<path d="M2 3v10M14 3v10M4 8h8M10 6l2 2-2 2"/>',
}
FILLED = frozenset({"play", "pause", "restart", "dot"})

# Whole-SVG artwork: view toggles (filled grey) and the check mark.
VIEW_GRID = ('<g fill="{c}"><rect x="2" y="2" width="5" height="5" rx=".7"/>'
             '<rect x="9" y="2" width="5" height="5" rx=".7"/>'
             '<rect x="2" y="9" width="5" height="5" rx=".7"/>'
             '<rect x="9" y="9" width="5" height="5" rx=".7"/></g>')
VIEW_LIST = ('<g stroke="{c}" stroke-width="1.35" stroke-linecap="round" fill="none">'
             '<path d="M6 3.5h8M6 8h8M6 12.5h8"/></g><g fill="{c}">'
             '<circle cx="3" cy="3.5" r="1.15"/><circle cx="3" cy="8" r="1.15"/>'
             '<circle cx="3" cy="12.5" r="1.15"/></g>')
CHECKMARK = ('<path d="M3 8.5L6.5 12 13 4.5" fill="none" stroke="{c}" stroke-width="2.2" '
             'stroke-linecap="round" stroke-linejoin="round"/>')
# The brand mark artwork, used as-is; it is cropped to its ``tile`` element when rendered.
BRAND_PATH = Path(__file__).resolve().parents[1] / "resources" / "brand_mark.svg"
BRAND_ELEMENT = "tile"

_CACHE: dict[tuple, QtGui.QPixmap] = {}
_CACHE_LIMIT = 512


def _svg(body: str) -> bytes:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" '
            f'viewBox="0 0 16 16">{body}</svg>').encode()


def glyph_svg(name: str, color: str) -> bytes:
    fill = color if name in FILLED else "none"
    return _svg(f'<g fill="{fill}" stroke="{color}" stroke-width="1.5" stroke-linecap="round" '
                f'stroke-linejoin="round">{GLYPHS[name]}</g>')


def swatch_svg(color: QtGui.QColor, dashed: bool) -> bytes:
    dash = ' stroke-dasharray="8 4"' if dashed else ""
    rgb = f"rgb({color.red()},{color.green()},{color.blue()})"
    return _svg(f'<path d="M2 8h12" stroke="{rgb}" stroke-opacity="{color.alphaF():.3f}" '
                f'stroke-width="2"{dash}/>')


def render(svg: bytes, size: int, ratio: float, element: str = "") -> QtGui.QPixmap:
    """Rasterise ``svg`` (a 16-unit document) at ``size`` logical pixels and ``ratio``.

    With ``element``, the document is cropped to that element's bounds instead.
    """
    key = (svg, size, round(ratio, 3), element)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    px = max(1, round(size * ratio))
    image = QtGui.QImage(px, px, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(image)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    renderer = QtSvg.QSvgRenderer(QtCore.QByteArray(svg))
    if element:
        renderer.setViewBox(renderer.boundsOnElement(element))
    renderer.render(painter, QtCore.QRectF(0, 0, px, px))
    painter.end()
    pixmap = QtGui.QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(ratio)
    if len(_CACHE) > _CACHE_LIMIT:
        _CACHE.clear()
    _CACHE[key] = pixmap
    return pixmap


def glyph(name: str, size: int = style.ICON, color: str = style.GLYPH,
          ratio: float = 1.0) -> QtGui.QPixmap:
    return render(glyph_svg(name, color), size, ratio)


def view_icon(grid: bool, size: int = style.ICON, ratio: float = 1.0) -> QtGui.QPixmap:
    body = (VIEW_GRID if grid else VIEW_LIST).format(c=style.VIEW_ICON)
    return render(_svg(body), size, ratio)


def checkmark(size: int, ratio: float = 1.0) -> QtGui.QPixmap:
    return render(_svg(CHECKMARK.format(c=style.CHECK_MARK)), size, ratio)


def brand_mark(size: int = style.BRAND_SIZE, ratio: float = 1.0) -> QtGui.QPixmap:
    return render(BRAND_PATH.read_bytes(), size, ratio, BRAND_ELEMENT)


def swatch(color: QtGui.QColor, dashed: bool, ratio: float = 1.0) -> QtGui.QPixmap:
    return render(swatch_svg(color, dashed), style.ICON, ratio)


def qicon(name: str, size: int = style.ICON, color: str = style.GLYPH) -> QtGui.QIcon:
    """A QIcon (for menus and dialogs) with normal and disabled renderings at 1×–3×."""
    icon = QtGui.QIcon()
    for ratio in (1.0, 2.0, 3.0):
        icon.addPixmap(glyph(name, size, color, ratio), QtGui.QIcon.Mode.Normal)
        icon.addPixmap(glyph(name, size, style.GLYPH_DISABLED, ratio), QtGui.QIcon.Mode.Disabled)
    return icon


def draw(painter: QtGui.QPainter, pixmap: QtGui.QPixmap, center: QtCore.QPointF) -> None:
    """Draw a cached icon centred on ``center`` (logical coordinates)."""
    ratio = pixmap.devicePixelRatio()
    w, h = pixmap.width() / ratio, pixmap.height() / ratio
    painter.drawPixmap(QtCore.QPointF(center.x() - w / 2, center.y() - h / 2), pixmap)

# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""The curve canvas.

Painting: base fill and a 5 × 5 grid inside a fixed 24 px padding; in the value view axis
labels, the velocity overlay (own vertical framing), the dashed base reference under an active
stack, tangent handles, the curve, the preview marker, knots and the marquee; in the velocity
view the faint value curve, the velocity curve, influence squares and speed circles.

Gestures go through the document's gesture API, so one completed gesture is one local undo
entry and Escape restores the pre-gesture state exactly. Framing animates over 150 ms
(OutCubic) on explicit requests and is immediate after edits; a curve replaced from outside
morphs from what was on screen.
"""

from __future__ import annotations

import itertools
import math
import time
from dataclasses import dataclass, field

from motion_atelier.app.document import ShapeDocument
from motion_atelier.app.strings import text
from motion_atelier.curve import evaluate
from motion_atelier.curve.errors import MotionAtelierError
from motion_atelier.curve.model import Curve, Knot, Tangent
from motion_atelier.generators.base import ShapeKind
from motion_atelier.ui import qt, style
from motion_atelier.ui.qt import QtCore, QtGui, QtWidgets

Qt = QtCore.Qt
VALUE = "value"
VELOCITY = "velocity"
VELOCITY_SUBDIVISIONS = 48  # velocity samples per segment
DENSE_VELOCITY_SAMPLES = 257  # smoothed central difference under an active stack
DENSE_KNOT_COUNT = 17  # curves with at least this many knots use the dense velocity
FLATNESS_PX = 0.75  # adaptive flattening tolerance
FLATNESS_DEPTH = 12
SPEED_LIMIT = 1e6  # speeds are clipped to this magnitude when framing and drawing
MEMO_LIMIT = 256  # cached per-curve results (curves are immutable, so identity is a key)

_MEMO: dict[tuple, tuple[Curve, object]] = {}


def _memo(kind: str, curve: Curve, extra, compute):
    """Cache a derived result per curve object. The cache holds the curve itself, so its
    ``id`` cannot be reused while the entry exists."""
    key = (kind, id(curve), extra)
    hit = _MEMO.get(key)
    if hit is not None and hit[0] is curve:
        return hit[1]
    value = compute()
    if len(_MEMO) >= MEMO_LIMIT:
        _MEMO.clear()
    _MEMO[key] = (curve, value)
    return value


@dataclass(frozen=True)
class Bounds:
    x0: float
    x1: float
    y0: float
    y1: float

    def lerp(self, other: Bounds, t: float) -> Bounds:
        return Bounds(*(a + (b - a) * t for a, b in zip(
            (self.x0, self.x1, self.y0, self.y1), (other.x0, other.x1, other.y0, other.y1),
            strict=True)))

    def union(self, other: Bounds) -> Bounds:
        return Bounds(min(self.x0, other.x0), max(self.x1, other.x1), min(self.y0, other.y0),
                      max(self.y1, other.y1))

    def close_to(self, other: Bounds) -> bool:
        return all(abs(a - b) < 1e-12 for a, b in zip(
            (self.x0, self.x1, self.y0, self.y1), (other.x0, other.x1, other.y0, other.y1),
            strict=True))


class Transform:
    """Data ↔ screen mapping into the padded rectangle."""

    def __init__(self, b: Bounds, w: float, h: float, pad: float = style.GRAPH_PAD) -> None:
        self.b, self.w, self.h, self.pad = b, w, h, pad
        self.cw = max(1e-9, w - 2 * pad)
        self.ch = max(1e-9, h - 2 * pad)
        self.dx = (b.x1 - b.x0) or 1.0
        self.dy = (b.y1 - b.y0) or 1.0

    def to(self, x: float, y: float) -> QtCore.QPointF:
        return QtCore.QPointF(self.pad + (x - self.b.x0) / self.dx * self.cw,
                              self.h - self.pad - (y - self.b.y0) / self.dy * self.ch)

    def frm(self, sx: float, sy: float) -> tuple[float, float]:
        return (self.b.x0 + (sx - self.pad) / self.cw * self.dx,
                self.b.y0 + (self.h - self.pad - sy) / self.ch * self.dy)


# -- curve geometry helpers ----------------------------------------------------------------------


def segment(curve: Curve, i: int) -> tuple[tuple[float, float], ...]:
    a, b = curve.knots[i], curve.knots[i + 1]
    d = b.x - a.x
    wo = a.tout.weight if a.tout else 1 / 3
    so = a.tout.slope if a.tout else 0.0
    wi = b.tin.weight if b.tin else 1 / 3
    si = b.tin.slope if b.tin else 0.0
    return ((a.x, a.y), (a.x + wo * d, a.y + so * wo * d), (b.x - wi * d, b.y - si * wi * d),
            (b.x, b.y))


def handle(curve: Curve, i: int, side: str) -> tuple[float, float] | None:
    """Handle position, or None when the tangent is absent or has zero length."""
    k = curve.knots[i]
    if side == "out":
        if k.tout is None or i == len(curve.knots) - 1:
            return None
        dx = k.tout.weight * (curve.knots[i + 1].x - k.x)
        dy = k.tout.slope * dx
    else:
        if k.tin is None or i == 0:
            return None
        dx = -k.tin.weight * (k.x - curve.knots[i - 1].x)
        dy = k.tin.slope * dx
    if dx == 0 and dy == 0:
        return None
    return k.x + dx, k.y + dy


def curve_bounds(curve: Curve) -> Bounds:
    return _memo("curve_bounds", curve, None, lambda: _curve_bounds(curve))


def _curve_bounds(curve: Curve) -> Bounds:
    """Exact bounds of every knot and handle; a curve with no value span shows ±0.5."""
    xs, ys = [], []
    for i, k in enumerate(curve.knots):
        xs.append(k.x)
        ys.append(k.y)
        for side in ("in", "out"):
            h = handle(curve, i, side)
            if h is not None:
                xs.append(h[0])
                ys.append(h[1])
    y0, y1 = min(ys), max(ys)
    if y0 == y1:
        y0, y1 = y0 - 0.5, y1 + 0.5
    return Bounds(min(xs), max(xs), y0, y1)


def _bez(p, t: float) -> tuple[float, float]:
    u = 1 - t
    a, b, c, d = u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t
    return (a * p[0][0] + b * p[1][0] + c * p[2][0] + d * p[3][0],
            a * p[0][1] + b * p[1][1] + c * p[2][1] + d * p[3][1])


def _bez_d(p, t: float) -> tuple[float, float]:
    u = 1 - t
    return (3 * u * u * (p[1][0] - p[0][0]) + 6 * u * t * (p[2][0] - p[1][0])
            + 3 * t * t * (p[3][0] - p[2][0]),
            3 * u * u * (p[1][1] - p[0][1]) + 6 * u * t * (p[2][1] - p[1][1])
            + 3 * t * t * (p[3][1] - p[2][1]))


def adaptive_points(curve: Curve, tr: Transform, tol: float = FLATNESS_PX) -> QtGui.QPolygonF:
    b = tr.b
    extra = (b.x0, b.x1, b.y0, b.y1, tr.w, tr.h, tr.pad, tol)
    return _memo("adaptive_points", curve, extra, lambda: _adaptive_points(curve, tr, tol))


def _adaptive_points(curve: Curve, tr: Transform, tol: float = FLATNESS_PX) -> QtGui.QPolygonF:
    """Subdivide each segment until within ``tol`` px of its chord (depth ≤ 12)."""
    poly = QtGui.QPolygonF()
    k0 = curve.knots[0]
    poly.append(tr.to(k0.x, k0.y))

    def dist(p: QtCore.QPointF, a: QtCore.QPointF, b: QtCore.QPointF) -> float:
        dx, dy = b.x() - a.x(), b.y() - a.y()
        if dx == 0 and dy == 0:
            return math.hypot(p.x() - a.x(), p.y() - a.y())
        return abs(dy * p.x() - dx * p.y() + b.x() * a.y() - b.y() * a.x()) / math.hypot(dx, dy)

    def rec(p, depth: int) -> None:
        v = [tr.to(*q) for q in p]
        if max(dist(v[1], v[0], v[3]), dist(v[2], v[0], v[3])) <= tol or depth >= FLATNESS_DEPTH:
            poly.append(v[3])
            return

        def mid(a, b):
            return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        q0, q1, q2 = mid(p[0], p[1]), mid(p[1], p[2]), mid(p[2], p[3])
        r0, r1 = mid(q0, q1), mid(q1, q2)
        m = mid(r0, r1)
        rec((p[0], q0, r0, m), depth + 1)
        rec((m, r1, q2, p[3]), depth + 1)

    for i in range(len(curve.knots) - 1):
        rec(segment(curve, i), 0)
    return poly


def velocity_data(curve: Curve, sub: int = VELOCITY_SUBDIVISIONS) -> list[tuple[float, float]]:
    return _memo("velocity_data", curve, sub, lambda: _velocity_data(curve, sub))


def _velocity_data(curve: Curve, sub: int = VELOCITY_SUBDIVISIONS) -> list[tuple[float, float]]:
    out = []
    for s in range(len(curve.knots) - 1):
        p = segment(curve, s)
        for i in range(0 if s == 0 else 1, sub + 1):
            u = i / sub
            x, _ = _bez(p, u)
            dx, dy = _bez_d(p, u)
            if abs(dx) <= 1e-15:
                continue
            v = dy / dx
            if math.isfinite(v):
                out.append((x, max(-SPEED_LIMIT, min(SPEED_LIMIT, v))))
    return out


def dense_velocity(curve: Curve, n: int = DENSE_VELOCITY_SAMPLES) -> list[tuple[float, float]]:
    return _memo("dense_velocity", curve, n, lambda: _dense_velocity(curve, n))


def _dense_velocity(curve: Curve, n: int = DENSE_VELOCITY_SAMPLES) -> list[tuple[float, float]]:
    """Smoothed central difference over a dense (effect output) curve."""
    knots = curve.knots
    if len(knots) < DENSE_KNOT_COUNT:
        return velocity_data(curve)
    x0, x1 = knots[0].x, knots[-1].x
    dom = x1 - x0
    steps = sorted(b.x - a.x for a, b in itertools.pairwise(knots))
    radius = min(dom * 0.05, steps[len(steps) >> 1] * 1.5)
    out = []
    for i in range(n):
        x = x0 + dom * i / (n - 1)
        lo, hi = max(x0, x - radius), min(x1, x + radius)
        out.append((x, (evaluate.value_at(curve, hi) - evaluate.value_at(curve, lo)) / (hi - lo)))
    return out


@dataclass(frozen=True)
class SpeedControl:
    knot: int
    side: str | None  # None: the shared control of a smooth interior knot
    time: float
    speed: float


@dataclass(frozen=True)
class InfluenceControl:
    knot: int
    side: str
    knot_time: float
    time: float
    speed: float


def speed_controls(curve: Curve) -> list[SpeedControl]:
    out, last = [], len(curve.knots) - 1
    for i, k in enumerate(curve.knots):
        if i == 0:
            out.append(SpeedControl(i, "out", k.x, k.tout.slope if k.tout else 0.0))
        elif i == last:
            out.append(SpeedControl(i, "in", k.x, k.tin.slope if k.tin else 0.0))
        elif k.smooth:
            out.append(SpeedControl(i, None, k.x, k.tin.slope))
        else:
            out.append(SpeedControl(i, "in", k.x, k.tin.slope))
            out.append(SpeedControl(i, "out", k.x, k.tout.slope))
    return out


def influence_controls(curve: Curve) -> list[InfluenceControl]:
    out, knots, last = [], curve.knots, len(curve.knots) - 1
    for i, k in enumerate(knots):
        if i > 0 and k.tin is not None:
            out.append(InfluenceControl(i, "in", k.x, k.x - k.tin.weight * (k.x - knots[i - 1].x),
                                        k.tin.slope))
        if i < last and k.tout is not None:
            out.append(InfluenceControl(i, "out", k.x,
                                        k.x + k.tout.weight * (knots[i + 1].x - k.x),
                                        k.tout.slope))
    return out


def _speed_bounds(curve: Curve, speeds) -> Bounds:
    finite = [max(-SPEED_LIMIT, min(SPEED_LIMIT, s)) for s in speeds if math.isfinite(s)]
    lo, hi = min([0.0, *finite]), max([0.0, *finite])
    if lo == hi:
        lo, hi = -1.0, 1.0
    return Bounds(curve.knots[0].x, curve.knots[-1].x, lo, hi)


def velocity_bounds(curve: Curve) -> Bounds:
    return _memo("velocity_bounds", curve, None, lambda: _velocity_bounds(curve))


def _velocity_bounds(curve: Curve) -> Bounds:
    speeds = [v for _, v in velocity_data(curve)]
    speeds += [c.speed for c in speed_controls(curve)]
    speeds += [c.speed for c in influence_controls(curve)]
    return _speed_bounds(curve, speeds)


def density_bounds(curve: Curve) -> Bounds:
    return _memo("density_bounds", curve, None, lambda: _density_bounds(curve))


def _density_bounds(curve: Curve) -> Bounds:
    return _speed_bounds(curve, [v for _, v in dense_velocity(curve)])


# -- gesture operations (pure; every result is a validated Curve) -------------------------------


def _shared_retime(curve: Curve, selection: frozenset[int], dt: float) -> float:
    knots, last = curve.knots, len(curve.knots) - 1
    eps = (knots[last].x - knots[0].x) * 1e-6
    lo, hi = -math.inf, math.inf
    for i in selection:
        if i in (0, last):
            continue
        if i - 1 not in selection or i - 1 == 0:
            lo = max(lo, knots[i - 1].x - knots[i].x + eps)
        if i + 1 not in selection or i + 1 == last:
            hi = min(hi, knots[i + 1].x - knots[i].x - eps)
    return min(max(dt, lo), hi) if lo <= hi else 0.0


def move_knots(curve: Curve, selection: frozenset[int], dx: float, dy: float) -> Curve:
    """Endpoints move in value only; interior knots share one clamped time offset."""
    d = _shared_retime(curve, selection, dx)
    last = len(curve.knots) - 1
    out = [k.replace(x=k.x + (d if 0 < i < last else 0.0), y=k.y + dy) if i in selection else k
           for i, k in enumerate(curve.knots)]
    return Curve(tuple(out))


def move_handle(curve: Curve, i: int, side: str, cx: float, cy: float) -> Curve:
    """The handle stays on its side, within its segment; on a smooth knot the other side
    takes the same slope and keeps its own weight."""
    knots = list(curve.knots)
    k = knots[i]
    last = len(knots) - 1
    if side == "out":
        s = knots[i + 1].x - k.x
        dx = min(max(cx - k.x, s * 1e-4), s)
        tout = Tangent((cy - k.y) / dx, dx / s)
        tin = k.tin.with_slope(tout.slope) if (k.smooth and i > 0 and k.tin) else k.tin
    else:
        s = k.x - knots[i - 1].x
        dx = min(max(k.x - cx, s * 1e-4), s)
        tin = Tangent((k.y - cy) / dx, dx / s)
        tout = k.tout.with_slope(tin.slope) if (k.smooth and i < last and k.tout) else k.tout
    knots[i] = Knot(k.x, k.y, tin, tout, k.smooth)
    return Curve(tuple(knots))


def drag_speed(curve: Curve, selection: frozenset[int], side: str | None, dt: float,
               dsp: float) -> Curve:
    """Speed circle: vertical changes speed, horizontal retimes selected interior knots."""
    d = _shared_retime(curve, selection, dt)
    knots, last = curve.knots, len(curve.knots) - 1
    out = []
    for i, k in enumerate(knots):
        if i not in selection:
            out.append(k)
            continue
        x = k.x + d if 0 < i < last else k.x
        tin, tout = k.tin, k.tout
        if 0 < i < last and (k.smooth or side is None):
            tin = tin.with_slope(tin.slope + dsp)
            tout = tout.with_slope(tin.slope) if k.smooth else tout.with_slope(tout.slope + dsp)
        elif i == 0:
            tout = tout.with_slope(tout.slope + dsp)
        elif i == last or side == "in":
            tin = tin.with_slope(tin.slope + dsp)
        else:
            tout = tout.with_slope(tout.slope + dsp)
        out.append(Knot(x, k.y, tin, tout, k.smooth))
    return Curve(tuple(out))


def drag_influence(curve: Curve, selection: frozenset[int], side: str, dt: float,
                   dsp: float) -> Curve:
    """Influence square: horizontal changes influence (0–1), vertical changes speed."""
    knots, last = curve.knots, len(curve.knots) - 1
    out = []
    for i, k in enumerate(knots):
        if i not in selection:
            out.append(k)
            continue
        s = ("out" if i < last else "in") if side == "out" else ("in" if i > 0 else "out")
        tin, tout = k.tin, k.tout
        if s == "out":
            span = knots[i + 1].x - k.x
            tout = Tangent(tout.slope + dsp, min(1.0, max(0.0, tout.weight + dt / span)))
            if k.smooth and i > 0 and tin is not None:
                tin = tin.with_slope(tout.slope)
        else:
            span = k.x - knots[i - 1].x
            tin = Tangent(tin.slope + dsp, min(1.0, max(0.0, tin.weight - dt / span)))
            if k.smooth and i < last and tout is not None:
                tout = tout.with_slope(tin.slope)
        out.append(Knot(k.x, k.y, tin, tout, k.smooth))
    return Curve(tuple(out))


# -- the widget ------------------------------------------------------------------------------------


@dataclass
class Hit:
    kind: str  # "knot", "in", "out", "speed", "influence"
    knot: int
    side: str | None = None
    distance: float = 0.0


@dataclass
class _Drag:
    state: str  # "marquee", "knot", "in", "out", "speed", "influence"
    origin: QtCore.QPointF
    start: Curve
    selection: frozenset[int]
    hit: Hit | None = None
    additive: bool = False
    transform: Transform | None = None


@dataclass
class _Morph:
    times: list[float]
    a: list[float]
    b: list[float]
    start: float
    u: float = 0.0


@dataclass
class _FrameAnim:
    origin: Bounds
    target: Bounds
    start: float = field(default_factory=time.monotonic)


class CurveCanvas(QtWidgets.QWidget):
    """Draws a shape document and edits it through gestures."""

    knotActivated = QtCore.Signal(int)
    menuRequested = QtCore.Signal(QtCore.QPoint, float)
    undoRequested = QtCore.Signal()
    redoRequested = QtCore.Signal()
    deleteRequested = QtCore.Signal()
    gestureFinished = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CurveCanvas")
        self.setMinimumSize(*style.CANVAS_MIN)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                           QtWidgets.QSizePolicy.Policy.Expanding)
        self._doc: ShapeDocument | None = None
        self._mode = VALUE
        self.show_output = True
        self.show_base = True
        self.show_velocity = True
        self._layers: int | None = None  # stack layers shown (None: all)
        self._bounds = Bounds(0.0, 1.0, 0.0, 1.0)
        self._frame_anim: _FrameAnim | None = None
        self._morph: _Morph | None = None
        self._preview: Curve | None = None  # history preview (display only)
        self._drag: _Drag | None = None
        self._marquee: QtCore.QRectF | None = None
        self._hover: Hit | None = None
        self._marker: float | None = None
        self._background: QtGui.QPixmap | None = None
        self._background_key: tuple | None = None
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    # -- document ---------------------------------------------------------------------------

    def set_document(self, doc: ShapeDocument | None, frame: bool = True,
                     animate: bool = True) -> None:
        if self._doc is not None:
            self._doc.unsubscribe(self._on_doc)
        self._cancel_drag(restore=True)
        self._doc = doc
        self._preview = None
        self._morph = None
        if doc is not None:
            doc.subscribe(self._on_doc)
        if frame:
            self.auto_frame(animate)
        self.update()

    def document(self) -> ShapeDocument | None:
        return self._doc

    def _on_doc(self, _doc) -> None:
        idle = self._drag is None or self._drag.state == "marquee"
        if idle and not (self._doc is not None and self._doc.in_gesture):
            self._frame_now()
        self.update()

    @property
    def editable(self) -> bool:
        return self._doc is not None and self._doc.kind == ShapeKind.EASE

    def stack_active(self) -> bool:
        doc = self._doc
        return doc is not None and doc.base_curve() is not doc.evaluated_curve()

    def base_curve(self) -> Curve | None:
        return self._doc.base_curve() if self._doc is not None else None

    def displayed_curve(self) -> Curve | None:
        """The settled output: a history preview, or the stack evaluated through the
        selected layer."""
        if self._preview is not None:
            return self._preview
        doc = self._doc
        if doc is None:
            return None
        return doc.evaluated_through(self._layers)

    def set_layers(self, count: int | None) -> None:
        if count != self._layers:
            self._layers = count
            self._frame_now()
            self.update()

    # -- view state -------------------------------------------------------------------------

    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        if mode in (VALUE, VELOCITY) and mode != self._mode:
            self._cancel_drag(restore=True)
            self._mode = mode
            self.auto_frame(True)

    def set_overlays(self, output: bool | None = None, base: bool | None = None,
                     velocity: bool | None = None) -> None:
        if output is not None and output != self.show_output:
            self._cancel_drag(restore=True)
            self.show_output = output
        if base is not None:
            self.show_base = base
        if velocity is not None:
            self.show_velocity = velocity
        self.update()

    def set_marker(self, u: float | None) -> None:
        self._marker = u
        self.update()

    def bounds(self) -> Bounds:
        return self._bounds

    def transform(self) -> Transform:
        return Transform(self._bounds, self.width(), self.height())

    # -- framing and morph --------------------------------------------------------------------

    def presentation_bounds(self) -> Bounds:
        out = self.displayed_curve()
        base = self.base_curve()
        if out is None or base is None:
            return Bounds(0.0, 1.0, 0.0, 1.0)
        stacked = self.stack_active() and self._preview is None
        if self._mode == VELOCITY:
            if not stacked:
                return velocity_bounds(out)
            return density_bounds(base).union(density_bounds(out))
        b = curve_bounds(out)
        return b.union(curve_bounds(base)) if stacked else b

    def overlay_bounds(self) -> Bounds:
        out = self.displayed_curve()
        if not self.stack_active() or self._preview is not None:
            return velocity_bounds(out)
        return density_bounds(self.base_curve()).union(density_bounds(out))

    def auto_frame(self, animate: bool = True) -> None:
        target = self.presentation_bounds()
        if animate and self.isVisible() and not target.close_to(self._bounds):
            self._frame_anim = _FrameAnim(self._bounds, target)
            self._timer.start()
        else:
            self._frame_anim = None
            self._bounds = target
        self.update()

    def _frame_now(self) -> None:
        if self._frame_anim is None and self._morph is None:
            self._bounds = self.presentation_bounds()

    def snap(self) -> None:
        """Snap running animations to their end (any press, or hiding the canvas)."""
        if self._frame_anim is not None:
            self._bounds = self._frame_anim.target
            self._frame_anim = None
        if self._morph is not None:
            self._morph = None
            self._bounds = self.presentation_bounds()
        self.update()

    def morph_from(self, source: Curve | None) -> None:
        """Animate from ``source`` (what was on screen) to the current displayed curve."""
        target = self.displayed_curve()
        if source is None or target is None or source == target or not self.isVisible():
            self.auto_frame(False)
            return
        x0 = min(source.x_start, target.x_start)
        x1 = max(source.x_end, target.x_end)
        n = style.MORPH_SAMPLES
        times = [x0 + (x1 - x0) * i / (n - 1) for i in range(n)]

        def values(c: Curve) -> list[float]:
            return [evaluate.value_at(c, min(c.x_end, max(c.x_start, x))) for x in times]

        self._morph = _Morph(times, values(source), values(target), time.monotonic())
        self._timer.start()
        self.update()

    def set_preview_curve(self, curve: Curve | None) -> None:
        """Show ``curve`` without loading it (history preview), morphing to it."""
        source = self.displayed_curve()
        self._cancel_drag(restore=True)
        self._preview = curve
        if curve is None:
            self._morph = None
            self.auto_frame(True)
        else:
            self.morph_from(source)
        self.update()

    def preview_curve(self) -> Curve | None:
        return self._preview

    def animating(self) -> bool:
        return self._frame_anim is not None or self._morph is not None

    def _tick(self) -> None:
        now = time.monotonic()
        active = False
        if self._frame_anim is not None:
            f = self._frame_anim
            u = min(1.0, (now - f.start) * 1000 / style.ANIMATION_MS)
            self._bounds = f.origin.lerp(f.target, style.out_cubic(u))
            if u >= 1.0:
                self._frame_anim = None
            else:
                active = True
        if self._morph is not None:
            m = self._morph
            m.u = style.out_cubic((now - m.start) * 1000 / style.ANIMATION_MS)
            if m.u >= 1.0:
                self._morph = None
                self.auto_frame(True)
                active = self._frame_anim is not None
            else:
                active = True
        if not active:
            self._timer.stop()
        self.update()

    def hideEvent(self, event) -> None:
        self.snap()
        super().hideEvent(event)

    # -- painting ---------------------------------------------------------------------------

    def _paint_background(self, p: QtGui.QPainter) -> None:
        ratio = style.device_ratio(self)
        key = (self.width(), self.height(), ratio)
        if self._background_key != key:
            pm = QtGui.QPixmap(max(1, round(self.width() * ratio)),
                               max(1, round(self.height() * ratio)))
            pm.setDevicePixelRatio(ratio)
            pm.fill(QtGui.QColor(style.BASE))
            q = QtGui.QPainter(pm)
            q.setPen(QtGui.QPen(style.color(style.MID, 170), 1))
            pad, w, h = style.GRAPH_PAD, self.width(), self.height()
            for i in range(style.GRID_DIVISIONS + 1):
                f = i / style.GRID_DIVISIONS
                x = round(pad + f * (w - 2 * pad)) + 0.5
                y = round(pad + f * (h - 2 * pad)) + 0.5
                q.drawLine(QtCore.QPointF(x, pad), QtCore.QPointF(x, h - pad))
                q.drawLine(QtCore.QPointF(pad, y), QtCore.QPointF(w - pad, y))
            q.end()
            self._background, self._background_key = pm, key
        p.drawPixmap(0, 0, self._background)

    def _axis_font(self) -> QtGui.QFont:
        font = QtGui.QFont(self.font())
        font.setPointSizeF(max(style.AXIS_FONT_MIN_POINTS,
                               self.font().pointSizeF() * style.AXIS_FONT_SCALE))
        return font

    def paintEvent(self, _event) -> None:
        p = QtGui.QPainter(self)
        self._paint_background(p)
        doc = self._doc
        out = self.displayed_curve()
        if doc is None or out is None:
            p.end()
            return
        tr = self.transform()
        value = self._mode == VALUE
        settled = self._morph is None
        base = doc.base_curve()
        stacked = self.stack_active() and self._preview is None
        authoring = base if self.editable else out
        w, h, pad = self.width(), self.height(), style.GRAPH_PAD
        if value and w >= 160:
            self._paint_axes(p, tr)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        round_pen = QtGui.QPen()
        round_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        round_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        if value:
            if self.show_velocity:
                vt = Transform(self.overlay_bounds(), w, h)
                data = dense_velocity(out) if stacked else velocity_data(out)
                pen = QtGui.QPen(round_pen)
                pen.setColor(style.color(style.TEXT, 130))
                pen.setWidthF(1.25)
                p.setPen(pen)
                p.drawPolyline(QtGui.QPolygonF([vt.to(x, v) for x, v in data]))
            if self.show_base and stacked:
                pen = QtGui.QPen(style.color(style.TEXT, 120), 1.3)
                pen.setDashPattern([4, 2])
                p.setPen(pen)
                p.drawPolyline(adaptive_points(base, tr))
            if self.editable and self.show_output and settled:
                self._paint_handles(p, tr, authoring)
        elif self.show_output:
            vt = Transform(curve_bounds(out), w, h)
            p.setPen(QtGui.QPen(style.color(style.TEXT, 70), 1))
            p.drawPolyline(adaptive_points(out, vt))
        if self.show_output or not value:
            pen = QtGui.QPen(round_pen)
            pen.setColor(QtGui.QColor(style.HIGHLIGHT))
            pen.setWidthF(2.6)
            p.setPen(pen)
            if self._morph is not None and value:
                m = self._morph
                pts = [tr.to(x, a + (b - a) * m.u) for x, a, b in zip(m.times, m.a, m.b,
                                                                       strict=True)]
                p.drawPolyline(QtGui.QPolygonF(pts))
            elif not value:
                p.drawPolyline(QtGui.QPolygonF([tr.to(x, v) for x, v in velocity_data(out)]))
            else:
                p.drawPolyline(adaptive_points(out, tr))
        if self.show_output and self._marker is not None and value:
            x = out.x_start + (out.x_end - out.x_start) * self._marker
            pt = tr.to(x, evaluate.value_at(out, x))
            marker = style.color(style.HIGHLIGHT, 200)
            p.setPen(QtGui.QPen(marker, 1))
            p.drawLine(QtCore.QPointF(pad, pt.y()), QtCore.QPointF(w - pad, pt.y()))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(marker)
            p.drawEllipse(pt, 4, 4)
        if settled and value and self.show_output and (self.editable or not stacked):
            self._paint_knots(p, tr, authoring)
        elif settled and not value and self.editable:
            self._paint_velocity_controls(p, tr, authoring)
        if self._marquee is not None:
            pen = QtGui.QPen(QtGui.QColor(style.HIGHLIGHT), 1)
            pen.setDashPattern([4, 2])
            p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, False)
            p.fillRect(self._marquee, style.color(style.HIGHLIGHT, 35))
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(self._marquee.adjusted(0.5, 0.5, 0, 0))
        p.end()

    def _paint_axes(self, p: QtGui.QPainter, tr: Transform) -> None:
        font = self._axis_font()
        p.setFont(font)
        metrics = QtGui.QFontMetricsF(font)
        p.setPen(style.color(style.TEXT, 150))
        w, h, pad = self.width(), self.height(), style.GRAPH_PAD
        bottom = h - pad
        for v in (0, 1):
            x = tr.to(v, 0).x()
            if pad - 1 <= x <= w - pad + 1:
                label = str(v)
                p.drawText(QtCore.QPointF(x - metrics.horizontalAdvance(label) / 2,
                                          bottom + 4 + metrics.ascent()), label)
            y = tr.to(0, v).y()
            if pad - 1 <= y <= bottom + 1:
                label = str(v)
                p.drawText(QtCore.QPointF(pad - 4 - metrics.horizontalAdvance(label),
                                          y + (metrics.ascent() - metrics.descent()) / 2), label)
        label = "TIME"
        if metrics.horizontalAdvance(label) < w / 4:
            p.drawText(QtCore.QPointF(w / 2 - metrics.horizontalAdvance(label) / 2,
                                      bottom + 4 + metrics.ascent()), label)
        p.setFont(self.font())

    def _paint_handles(self, p: QtGui.QPainter, tr: Transform, curve: Curve) -> None:
        for i, k in enumerate(curve.knots):
            kp = tr.to(k.x, k.y)
            for side in ("in", "out"):
                h = handle(curve, i, side)
                if h is None:
                    continue
                cp = tr.to(*h)
                hovered = self._hover is not None and self._hover.kind == side and \
                    self._hover.knot == i
                p.setPen(QtGui.QPen(QtGui.QColor(style.MIDLIGHT), 1))
                p.drawLine(kp, cp)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QtGui.QColor(style.HIGHLIGHT if hovered else style.MIDLIGHT))
                r = 4 if hovered else 3
                p.drawEllipse(cp, r, r)

    def _paint_knots(self, p: QtGui.QPainter, tr: Transform, curve: Curve) -> None:
        selection = self._doc.selection if self.editable else frozenset()
        for i, k in enumerate(curve.knots):
            pt = tr.to(k.x, k.y)
            hovered = self._hover is not None and self._hover.kind == "knot" and \
                self._hover.knot == i
            selected = i in selection
            if selected:
                p.setPen(QtGui.QPen(QtGui.QColor(style.HIGHLIGHT), 2))
                p.setBrush(QtGui.QColor(style.TEXT))
            else:
                p.setPen(QtGui.QPen(QtGui.QColor(style.BASE), 2))
                p.setBrush(QtGui.QColor(style.HIGHLIGHT))
            r = 6 if hovered or selected else 5
            p.drawEllipse(pt, r, r)

    def _influence_point(self, c: InfluenceControl, tr: Transform) -> QtCore.QPointF:
        kp = tr.to(c.knot_time, c.speed)
        rp = tr.to(c.time, c.speed)
        direction = 1 if c.side == "out" else -1
        offset = max((rp.x() - kp.x()) * direction, style.INFLUENCE_MIN_OFFSET_PX)
        return QtCore.QPointF(kp.x() + direction * offset, kp.y())

    def _paint_velocity_controls(self, p: QtGui.QPainter, tr: Transform, curve: Curve) -> None:
        selection = self._doc.selection
        hov = self._hover
        for c in influence_controls(curve):
            kp = tr.to(c.knot_time, c.speed)
            rp = self._influence_point(c, tr)
            active = c.knot in selection
            hovered = hov is not None and hov.kind == "influence" and hov.knot == c.knot and \
                hov.side == c.side
            p.setPen(QtGui.QPen(QtGui.QColor(style.HIGHLIGHT if active else style.MIDLIGHT), 1))
            p.drawLine(kp, rp)
            half = 3.5 if hovered else 2.75
            p.fillRect(QtCore.QRectF(rp.x() - half, rp.y() - half, 2 * half, 2 * half),
                       QtGui.QColor(style.HIGHLIGHT if active or hovered else style.TEXT))
        for c in speed_controls(curve):
            pt = tr.to(c.time, c.speed)
            active = c.knot in selection
            hovered = hov is not None and hov.kind == "speed" and hov.knot == c.knot and \
                hov.side == c.side
            p.setBrush(QtGui.QColor(style.HIGHLIGHT if active or hovered else style.BASE))
            p.setPen(QtGui.QPen(QtGui.QColor(style.HIGHLIGHT if active else style.TEXT), 1.25))
            r = 5.5 if hovered else 4.5
            p.drawEllipse(pt, r, r)

    # -- picking ----------------------------------------------------------------------------

    def hit_test(self, pos: QtCore.QPointF) -> Hit | None:
        doc = self._doc
        if doc is None or not self.editable:
            return None
        curve = doc.base_curve()
        tr = self.transform()
        r = style.HIT_RADIUS
        if self._mode == VELOCITY:
            cands = []
            for c in speed_controls(curve):
                pt = tr.to(c.time, c.speed)
                d = math.hypot(pos.x() - pt.x(), pos.y() - pt.y())
                if d <= r:
                    cands.append((d, c.knot, 0, 1 if c.side == "out" else 0,
                                  Hit("speed", c.knot, c.side, d)))
            for c in influence_controls(curve):
                pt = self._influence_point(c, tr)
                d = math.hypot(pos.x() - pt.x(), pos.y() - pt.y())
                if d <= r:
                    cands.append((d, c.knot, 1, 1 if c.side == "out" else 0,
                                  Hit("influence", c.knot, c.side, d)))
            cands.sort(key=lambda t: t[:4])
            return cands[0][4] if cands else None
        if not self.show_output:
            return None
        best: Hit | None = None
        for i, k in enumerate(curve.knots):
            pt = tr.to(k.x, k.y)
            d = math.hypot(pos.x() - pt.x(), pos.y() - pt.y())
            if d <= r and (best is None or d < best.distance):
                best = Hit("knot", i, None, d)
        if best is not None:
            return best
        for i in range(len(curve.knots)):
            for side in ("in", "out"):
                h = handle(curve, i, side)
                if h is None:
                    continue
                pt = tr.to(*h)
                d = math.hypot(pos.x() - pt.x(), pos.y() - pt.y())
                if d <= r and (best is None or d < best.distance):
                    best = Hit(side, i, side, d)
        return best

    # -- mouse ------------------------------------------------------------------------------

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        self.snap()
        doc = self._doc
        if event.button() != Qt.MouseButton.LeftButton or doc is None or not self.editable:
            return
        if not self.show_output and self._mode == VALUE:
            return
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        if self._preview is not None:
            self._preview = None
            self._frame_now()
        pos = qt.event_pos(event)
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        hit = self.hit_test(pos)
        tr = self.transform()
        if hit is None:
            self._drag = _Drag("marquee", pos, doc.base_curve(), doc.selection, additive=shift,
                               transform=tr)
            self._marquee = QtCore.QRectF(pos, pos)
            if not shift:
                doc.set_selection(())
            self.update()
            return
        selection = set(doc.selection)
        if hit.kind in ("knot", "speed", "influence"):
            if shift:
                selection ^= {hit.knot}
                doc.set_selection(selection)
                if hit.knot not in selection:
                    self.update()
                    return
            elif hit.knot not in selection:
                doc.set_selection({hit.knot})
        elif hit.knot not in selection:
            doc.set_selection({hit.knot})
        doc.begin_gesture()
        state = hit.kind if hit.kind in ("speed", "influence", "in", "out") else "knot"
        self._drag = _Drag(state, pos, doc.base_curve(), doc.selection, hit=hit, transform=tr)
        self.update()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        pos = qt.event_pos(event)
        drag, doc = self._drag, self._doc
        if drag is None or doc is None:
            hit = self.hit_test(pos)
            if hit != self._hover:
                self._hover = hit
                self.update()
            self.setCursor(Qt.CursorShape.ArrowCursor if self.editable
                           else Qt.CursorShape.ForbiddenCursor)
            return
        tr = drag.transform
        if drag.state == "marquee":
            self._marquee = QtCore.QRectF(drag.origin, pos).normalized()
            inside = set()
            if self._mode == VELOCITY:
                for c in speed_controls(drag.start):
                    if self._marquee.contains(tr.to(c.time, c.speed)):
                        inside.add(c.knot)
            else:
                for i, k in enumerate(drag.start.knots):
                    if self._marquee.contains(tr.to(k.x, k.y)):
                        inside.add(i)
            doc.set_selection((set(drag.selection) | inside) if drag.additive else inside)
            self.update()
            return
        mods = event.modifiers()
        other = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
        fine = (style.FINE_DRAG_SCALE
                if mods & Qt.KeyboardModifier.ShiftModifier and not mods & other else 1.0)
        ox, oy = tr.frm(drag.origin.x(), drag.origin.y())
        cx, cy = tr.frm(pos.x(), pos.y())
        dx, dy = (cx - ox) * fine, (cy - oy) * fine
        try:
            if drag.state == "knot":
                curve = move_knots(drag.start, drag.selection, dx, dy)
            elif drag.state in ("in", "out"):
                hx, hy = handle(drag.start, drag.hit.knot, drag.state) or (ox, oy)
                curve = move_handle(drag.start, drag.hit.knot, drag.state, hx + dx, hy + dy)
            elif drag.state == "speed":
                curve = drag_speed(drag.start, drag.selection, drag.hit.side, dx, dy)
            else:
                curve = drag_influence(drag.start, drag.selection, drag.hit.side, dx, dy)
        except MotionAtelierError:
            return  # an invalid intermediate position is skipped; the gesture continues
        doc.update_gesture(doc.committed_content.with_curve(curve))

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        drag, doc = self._drag, self._doc
        self._drag = None
        self._marquee = None
        if drag is not None and doc is not None and drag.state != "marquee" and doc.in_gesture:
            label = {"knot": "undo.move_knots", "in": "undo.move_in", "out": "undo.move_out",
                     "speed": "undo.edit_velocity", "influence": "undo.edit_velocity"}[drag.state]
            doc.end_gesture(text(label))
            self._frame_now()
        self._hover = self.hit_test(qt.event_pos(event))
        self.gestureFinished.emit()
        self.update()

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:
        if not self.editable or self._mode != VALUE:
            return
        hit = self.hit_test(qt.event_pos(event))
        if hit is not None and hit.kind == "knot":
            self.knotActivated.emit(hit.knot)

    def contextMenuEvent(self, event: QtGui.QContextMenuEvent) -> None:
        if self._drag is not None:
            return
        curve = self.base_curve()
        if curve is None:
            return
        x, _ = self.transform().frm(event.pos().x(), event.pos().y())
        x = min(max(x, curve.x_start + 1e-9), curve.x_end - 1e-9)
        self.menuRequested.emit(event.globalPos(), x)
        event.accept()

    def leaveEvent(self, event) -> None:
        if self._drag is None and self._hover is not None:
            self._hover = None
            self.update()
        super().leaveEvent(event)

    # -- keyboard (canvas focused, Ease) ----------------------------------------------------

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        key, mods = event.key(), event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        if key == Qt.Key.Key_Escape:
            self._cancel_drag(restore=True)
            event.accept()
            return
        if key == Qt.Key.Key_F and not ctrl:
            self.auto_frame(True)
            event.accept()
            return
        if not self.editable or self._doc is None:
            super().keyPressEvent(event)
            return
        if ctrl and key == Qt.Key.Key_Z and not shift:
            self.undoRequested.emit()
        elif ctrl and (key == Qt.Key.Key_Y or (key == Qt.Key.Key_Z and shift)):
            self.redoRequested.emit()
        elif ctrl and key == Qt.Key.Key_A:
            self._doc.set_selection(range(len(self._doc.base_curve().knots)))
        elif key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.deleteRequested.emit()
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def _cancel_drag(self, restore: bool) -> None:
        drag = self._drag
        self._drag = None
        self._marquee = None
        doc = self._doc
        if doc is not None:
            if drag is not None and drag.state == "marquee" and restore:
                doc.set_selection(drag.selection)
            if doc.in_gesture:
                doc.cancel_gesture()
        self.update()

    def cancel_gesture(self) -> None:
        self._cancel_drag(restore=True)

    def in_gesture(self) -> bool:
        return self._drag is not None

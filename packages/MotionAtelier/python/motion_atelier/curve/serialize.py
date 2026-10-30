# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Plain-data (JSON-ready) form of curves, canonical JSON, and content digests.

Curve data::

    {"knots": [{"x": 0.0, "y": 0.0, "in": null, "out": [slope, weight], "smooth": true}, ...]}

Parsing is strict: exact key sets, numbers only (booleans rejected), finite values, and the
full model validation. Canonical JSON (sorted keys, no whitespace, shortest round-trip float
repr, NaN/Inf forbidden) is platform-independent for IEEE-754 doubles, so digests of
canonical JSON are stable content identities.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from motion_atelier.curve import errors as E
from motion_atelier.curve.errors import CurveError
from motion_atelier.curve.model import Curve, Knot, Tangent

_KNOT_KEYS = frozenset({"x", "y", "in", "out", "smooth"})


def canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def digest(data: Any) -> str:
    """SHA-256 hex digest of the canonical JSON of ``data``."""
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def _tangent_to_data(t: Tangent | None) -> list[float] | None:
    return None if t is None else [t.slope, t.weight]


def curve_to_data(curve: Curve) -> dict[str, Any]:
    return {
        "knots": [
            {
                "x": k.x,
                "y": k.y,
                "in": _tangent_to_data(k.tin),
                "out": _tangent_to_data(k.tout),
                "smooth": k.smooth,
            }
            for k in curve.knots
        ]
    }


def _number(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise CurveError(E.BAD_CURVE_DATA, f"{what} must be a number")
    return float(value)


def _tangent_from_data(value: Any, what: str) -> Tangent | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 2:
        raise CurveError(E.BAD_CURVE_DATA, f"{what} must be null or [slope, weight]")
    return Tangent(_number(value[0], f"{what} slope"), _number(value[1], f"{what} weight"))


def curve_from_data(data: Any) -> Curve:
    if not isinstance(data, dict) or set(data) != {"knots"}:
        raise CurveError(E.BAD_CURVE_DATA, "curve data must be an object with exactly 'knots'")
    raw = data["knots"]
    if not isinstance(raw, list):
        raise CurveError(E.BAD_CURVE_DATA, "'knots' must be a list")
    knots = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict) or set(item) != _KNOT_KEYS:
            raise CurveError(E.BAD_CURVE_DATA, f"knot {index} must have keys {sorted(_KNOT_KEYS)}")
        smooth = item["smooth"]
        if not isinstance(smooth, bool):
            raise CurveError(E.BAD_CURVE_DATA, f"knot {index} 'smooth' must be a boolean")
        knots.append(
            Knot(
                _number(item["x"], f"knot {index} x"),
                _number(item["y"], f"knot {index} y"),
                _tangent_from_data(item["in"], f"knot {index} in"),
                _tangent_from_data(item["out"], f"knot {index} out"),
                smooth,
            )
        )
    return Curve(tuple(knots))


def curve_digest(curve: Curve) -> str:
    return digest(curve_to_data(curve))

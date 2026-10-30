# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Stack evaluation, layer construction and strict parsing of stack data."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from motion_atelier.curve.errors import Notice
from motion_atelier.curve.model import Curve
from motion_atelier.effects.library import EFFECTS, Context, EffectDef
from motion_atelier.effects.model import (
    BAD_EFFECT_DATA,
    EDGE_MODES,
    STACK_VERSION,
    UNKNOWN_EFFECT,
    UNSUPPORTED_EFFECT_VERSION,
    EffectError,
    EffectLayer,
    EffectStack,
)
from motion_atelier.generators.params import defaults, validate_params, with_values


def effect_def(effect: str) -> EffectDef:
    try:
        return EFFECTS[effect]
    except KeyError:
        raise EffectError(UNKNOWN_EFFECT, f"unknown effect {effect!r}") from None


def make_layer(effect: str, name: str = "", mix: float = 1.0, enabled: bool = True,
               **params: Any) -> EffectLayer:
    d = effect_def(effect)
    values = with_values(d.params, defaults(d.params), **params)
    return EffectLayer(effect, name or effect, enabled, mix, values, d.version)


def layer_is_effective(layer: EffectLayer) -> bool:
    if not layer.active:
        return False
    return not effect_def(layer.effect).neutral(dict(layer.params))


def is_effective(stack: EffectStack | None) -> bool:
    """True when evaluating the stack can change the curve."""
    return stack is not None and any(layer_is_effective(layer) for layer in stack.layers)


def needs_fit(stack: EffectStack | None) -> bool:
    """True when an effective layer samples the curve (its output is dense, never committed
    as-is). Analytic layers keep the knot structure and commit directly."""
    return stack is not None and any(
        layer_is_effective(layer) and effect_def(layer.effect).kind != "analytic"
        for layer in stack.layers
    )


def evaluate(stack: EffectStack | None, base: Curve) -> tuple[Curve, tuple[Notice, ...]]:
    """Apply the stack top to bottom. Disabled, zero-mix and neutral layers are skipped, so a
    stack of such layers returns ``base`` itself (identity)."""
    if stack is None:
        return base, ()
    ctx = Context(stack.edge, stack.preserve_ends)
    curve = base
    notices: list[Notice] = []
    for layer in stack.layers:
        if not layer_is_effective(layer):
            continue
        d = effect_def(layer.effect)
        curve, more = d.apply(curve, dict(layer.params), layer.mix, ctx)
        notices.extend(more)
    return curve, tuple(notices)


def evaluate_until(stack: EffectStack, base: Curve, index: int) -> Curve:
    """Output after layers ``0..index`` (for previewing one layer's contribution)."""
    return evaluate(stack.with_layers(stack.layers[: index + 1]), base)[0]


_LAYER_KEYS = {"effect", "version", "name", "enabled", "mix", "params"}
_STACK_KEYS = {"version", "edge", "preserve_ends", "layers"}


def stack_from_data(data: Any) -> EffectStack:
    if not isinstance(data, dict) or set(data) != _STACK_KEYS:
        raise EffectError(BAD_EFFECT_DATA, f"stack needs keys {sorted(_STACK_KEYS)}")
    if data["version"] != STACK_VERSION or isinstance(data["version"], bool):
        raise EffectError(UNSUPPORTED_EFFECT_VERSION, f"stack version {data['version']!r}")
    if data["edge"] not in EDGE_MODES or not isinstance(data["preserve_ends"], bool):
        raise EffectError(BAD_EFFECT_DATA, "bad edge or preserve_ends")
    if not isinstance(data["layers"], list):
        raise EffectError(BAD_EFFECT_DATA, "layers must be a list")
    layers = []
    for item in data["layers"]:
        if not isinstance(item, dict) or set(item) != _LAYER_KEYS:
            raise EffectError(BAD_EFFECT_DATA, f"layer needs keys {sorted(_LAYER_KEYS)}")
        d = effect_def(item["effect"]) if isinstance(item["effect"], str) else None
        if d is None:
            raise EffectError(UNKNOWN_EFFECT, repr(item["effect"]))
        version = item["version"]
        if isinstance(version, bool) or version not in (d.version, *d.upgrades_from):
            raise EffectError(UNSUPPORTED_EFFECT_VERSION,
                              f"{d.id} version {item['version']!r}")
        if not isinstance(item["params"], Mapping):
            raise EffectError(BAD_EFFECT_DATA, "params must be an object")
        layers.append(EffectLayer(item["effect"], item["name"], item["enabled"], item["mix"],
                                  validate_params(d.params, item["params"]), d.version))
    return EffectStack(tuple(layers), data["edge"], data["preserve_ends"])

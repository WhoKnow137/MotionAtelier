# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Effect layers and stacks (immutable, validated, serialisable)."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any

from motion_atelier.curve.errors import MotionAtelierError
from motion_atelier.generators.params import Params

EDGE_HOLD = "hold"
EDGE_REFLECT = "reflect"
EDGE_MODES = (EDGE_HOLD, EDGE_REFLECT)

STACK_VERSION = 1
MAX_LAYERS = 16  # a practical bound; deeper stacks are slow and rarely meaningful

# Stable error codes.
BAD_EFFECT_DATA = "bad_effect_data"
UNKNOWN_EFFECT = "unknown_effect"
UNSUPPORTED_EFFECT_VERSION = "unsupported_effect_version"
TOO_MANY_LAYERS = "too_many_layers"


class EffectError(MotionAtelierError):
    pass


@dataclass(frozen=True, slots=True)
class EffectLayer:
    effect: str
    name: str
    enabled: bool
    mix: float
    params: Params
    version: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.mix, int | float) or isinstance(self.mix, bool):
            raise EffectError(BAD_EFFECT_DATA, "mix must be a number")
        if not math.isfinite(self.mix) or not 0.0 <= self.mix <= 1.0:
            raise EffectError(BAD_EFFECT_DATA, f"mix {self.mix!r} not in [0, 1]")
        object.__setattr__(self, "mix", float(self.mix) + 0.0)
        if not isinstance(self.enabled, bool) or not isinstance(self.name, str):
            raise EffectError(BAD_EFFECT_DATA, "bad layer fields")

    def with_(self, **changes) -> EffectLayer:
        return replace(self, **changes)

    @property
    def active(self) -> bool:
        return self.enabled and self.mix > 0.0


@dataclass(frozen=True, slots=True)
class EffectStack:
    layers: tuple[EffectLayer, ...] = ()
    edge: str = EDGE_HOLD
    preserve_ends: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "layers", tuple(self.layers))
        if self.edge not in EDGE_MODES:
            raise EffectError(BAD_EFFECT_DATA, f"edge {self.edge!r}")
        if len(self.layers) > MAX_LAYERS:
            raise EffectError(TOO_MANY_LAYERS, f"at most {MAX_LAYERS} layers")
        if not isinstance(self.preserve_ends, bool):
            raise EffectError(BAD_EFFECT_DATA, "preserve_ends must be a bool")

    def with_layers(self, layers) -> EffectStack:
        return replace(self, layers=tuple(layers))

    def replace_layer(self, index: int, layer: EffectLayer) -> EffectStack:
        layers = list(self.layers)
        layers[index] = layer
        return self.with_layers(layers)

    def insert(self, index: int, layer: EffectLayer) -> EffectStack:
        layers = list(self.layers)
        layers.insert(index, layer)
        return self.with_layers(layers)

    def remove(self, index: int) -> EffectStack:
        layers = list(self.layers)
        del layers[index]
        return self.with_layers(layers)

    def move(self, index: int, to: int) -> EffectStack:
        layers = list(self.layers)
        layer = layers.pop(index)
        layers.insert(max(0, min(to, len(layers))), layer)
        return self.with_layers(layers)

    def to_data(self) -> dict[str, Any]:
        return {
            "version": STACK_VERSION,
            "edge": self.edge,
            "preserve_ends": self.preserve_ends,
            "layers": [
                {"effect": layer.effect, "version": layer.version, "name": layer.name,
                 "enabled": layer.enabled, "mix": layer.mix, "params": dict(layer.params)}
                for layer in self.layers
            ],
        }

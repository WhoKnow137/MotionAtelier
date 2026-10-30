# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Recipes and the recipe library (records only; no disk access)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from motion_atelier.curve.errors import MotionAtelierError
from motion_atelier.curve.model import Curve
from motion_atelier.curve.serialize import curve_from_data, curve_to_data
from motion_atelier.effects.model import EffectStack
from motion_atelier.effects.stack import stack_from_data
from motion_atelier.generators import registry
from motion_atelier.generators.base import GeneratorRecord, ShapeKind
from motion_atelier.records.common import (
    InvalidDocument,
    content_digest,
    document,
    exact_keys,
    expect_bool,
    expect_int,
    expect_list,
    expect_str,
)

LIBRARY_SCHEMA = "motion_atelier.library"
LIBRARY_VERSION = 1
RECIPE_SCHEMA = "motion_atelier.recipe"
RECIPE_VERSION = 1

MAX_NAME = 80
MAX_TAG = 32
MAX_TAGS = 16
MAX_RECIPES = 5000  # far above any practical library; bounds memory and file size
MAX_ID = 64

_RECIPE_KEYS = {"id", "name", "kind", "tags", "favorite", "builtin", "payload", "effects"}


def normalise_tags(tags) -> tuple[str, ...]:
    """Strip, drop empties and case-insensitive duplicates, keep first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for tag in tags:
        tag = " ".join(str(tag).split())[:MAX_TAG]
        if tag and tag.casefold() not in seen:
            seen.add(tag.casefold())
            out.append(tag)
    return tuple(out[:MAX_TAGS])


@dataclass(frozen=True)
class Recipe:
    id: str
    name: str
    kind: ShapeKind
    tags: tuple[str, ...] = ()
    favorite: bool = False
    builtin: bool = False
    curve: Curve | None = None
    record: GeneratorRecord | None = None
    effects: EffectStack | None = None

    def __post_init__(self) -> None:
        if (self.kind == ShapeKind.EASE) != (self.curve is not None) or \
                (self.kind != ShapeKind.EASE) != (self.record is not None):
            raise InvalidDocument("recipe payload does not match its kind")
        if self.record is not None and self.record.kind != self.kind:
            raise InvalidDocument("generator record kind mismatch")
        if self.effects is not None and not self.effects.layers:
            object.__setattr__(self, "effects", None)

    def payload_data(self) -> dict[str, Any]:
        if self.curve is not None:
            return {"curve": curve_to_data(self.curve)}
        assert self.record is not None
        return {"generator": self.record.to_data()}

    def effects_data(self) -> dict | None:
        return self.effects.to_data() if self.effects is not None else None

    def digest(self) -> str:
        return content_digest(self.kind.value, self.payload_data(), self.effects_data())

    def with_(self, **changes) -> Recipe:
        return replace(self, **changes)

    def to_data(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "kind": self.kind.value,
            "tags": list(self.tags), "favorite": self.favorite, "builtin": self.builtin,
            "payload": self.payload_data(), "effects": self.effects_data(),
        }


def recipe_from_data(data: Any) -> Recipe:
    data = exact_keys(data, _RECIPE_KEYS, "recipe")
    try:
        kind = ShapeKind(data["kind"]) if isinstance(data["kind"], str) else None
    except ValueError:
        kind = None
    if kind is None:
        raise InvalidDocument(f"unknown recipe kind {data['kind']!r}")
    tags_raw = expect_list(data["tags"], "tags", MAX_TAGS)
    if not all(isinstance(t, str) for t in tags_raw):
        raise InvalidDocument("tags must be strings")
    payload = data["payload"]
    try:
        if kind == ShapeKind.EASE:
            payload = exact_keys(payload, {"curve"}, "ease payload")
            curve, record = curve_from_data(payload["curve"]), None
        else:
            payload = exact_keys(payload, {"generator"}, "generator payload")
            curve, record = None, registry.record_from_data(payload["generator"])
        effects = None if data["effects"] is None else stack_from_data(data["effects"])
    except InvalidDocument:
        raise
    except MotionAtelierError as exc:
        raise InvalidDocument(f"{exc.code}: {exc.detail}") from exc
    return Recipe(
        id=expect_str(data["id"], "id", MAX_ID),
        name=expect_str(data["name"], "name", MAX_NAME),
        kind=kind,
        tags=normalise_tags(tags_raw),
        favorite=expect_bool(data["favorite"], "favorite"),
        builtin=expect_bool(data["builtin"], "builtin"),
        curve=curve,
        record=record,
        effects=effects,
    )


@dataclass(frozen=True)
class Library:
    recipes: tuple[Recipe, ...] = ()
    builtin_revision: int = 0

    def __post_init__(self) -> None:
        ids = [r.id for r in self.recipes]
        if len(set(ids)) != len(ids):
            raise InvalidDocument("duplicate recipe ids")
        if len(ids) > MAX_RECIPES:
            raise InvalidDocument(f"more than {MAX_RECIPES} recipes")

    def by_id(self, recipe_id: str) -> Recipe | None:
        return next((r for r in self.recipes if r.id == recipe_id), None)

    def to_data(self) -> dict[str, Any]:
        return {"schema": LIBRARY_SCHEMA, "version": LIBRARY_VERSION,
                "builtin_revision": self.builtin_revision,
                "recipes": [r.to_data() for r in self.recipes]}


def library_from_data(data: Any) -> Library:
    data = document(data, LIBRARY_SCHEMA, LIBRARY_VERSION, {"builtin_revision", "recipes"})
    recipes = tuple(recipe_from_data(item) for item in
                    expect_list(data["recipes"], "recipes", MAX_RECIPES))
    return Library(recipes, expect_int(data["builtin_revision"], "builtin_revision", 0, 10**6))

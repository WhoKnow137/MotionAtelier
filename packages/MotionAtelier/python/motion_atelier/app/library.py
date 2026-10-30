# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""The recipe library service: one user library of built-in and user recipes.

Every change is applied to an in-memory copy and saved atomically; if the save fails the
in-memory library is left as it was, so a failed write never loses valid data. Loading a
recipe only fills the editor (one local undo step); it never touches the scene.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from motion_atelier.app.document import ShapeContent
from motion_atelier.curve.errors import MotionAtelierError
from motion_atelier.generators.base import ShapeKind
from motion_atelier.records.archive import export_archive, read_archive
from motion_atelier.records.recipe import (
    MAX_NAME,
    Library,
    Recipe,
    library_from_data,
    normalise_tags,
    recipe_from_data,
)
from motion_atelier.store.atomic import JsonStore, StoreWarning

BUILTINS_PATH = Path(__file__).resolve().parents[1] / "resources" / "builtin_recipes.json"
LIBRARY_FILE = "library.json"

SORT_MANUAL = "manual"
SORT_NAME = "name"
SORT_FAVORITES = "favorites"
SORT_MODES = (SORT_MANUAL, SORT_NAME, SORT_FAVORITES)

# Import review states and actions.
NEW = "new"
DUPLICATE = "duplicate"
NAME_CONFLICT = "name_conflict"
INVALID = "invalid"
ACTION_IMPORT = "import"
ACTION_SKIP = "skip"
ACTION_RENAME = "rename"
ACTION_REPLACE = "replace"
ACTIONS_BY_STATE = {
    NEW: (ACTION_IMPORT, ACTION_SKIP),
    DUPLICATE: (ACTION_SKIP, ACTION_IMPORT),
    NAME_CONFLICT: (ACTION_RENAME, ACTION_REPLACE, ACTION_SKIP),
    INVALID: (ACTION_SKIP,),
}

# Stable codes.
UNKNOWN_RECIPE = "unknown_recipe"
BAD_NAME = "bad_name"
LIBRARY_CHANGED = "library_changed"
BUILTINS_ADDED = "builtins_added"
BAD_ACTION = "bad_action"


class LibraryError(MotionAtelierError):
    pass


@dataclass(frozen=True)
class BuiltinSet:
    revision: int
    recipes: tuple[Recipe, ...]


def load_builtins(path: Path = BUILTINS_PATH) -> BuiltinSet:
    data = json.loads(path.read_text(encoding="utf-8"))
    return BuiltinSet(int(data["revision"]), tuple(recipe_from_data(r) for r in data["recipes"]))


def content_of(recipe: Recipe) -> ShapeContent:
    return ShapeContent(recipe.kind, curve=recipe.curve, record=recipe.record,
                        effects=recipe.effects)


def unique_name(name: str, taken: Iterable[str]) -> str:
    taken_folded = {t.casefold() for t in taken}
    if name.casefold() not in taken_folded:
        return name
    n = 2
    while True:
        candidate = f"{name} ({n})"[:MAX_NAME]
        if candidate.casefold() not in taken_folded:
            return candidate
        n += 1


@dataclass(frozen=True)
class ReviewItem:
    index: int
    name: str
    state: str
    recipe: Recipe | None
    problem: str | None = None
    conflict_id: str | None = None

    @property
    def actions(self) -> tuple[str, ...]:
        actions = ACTIONS_BY_STATE[self.state]
        if self.state == NAME_CONFLICT and self.conflict_id and \
                self.conflict_id.startswith("builtin:"):
            actions = tuple(a for a in actions if a != ACTION_REPLACE)  # built-ins never replaced
        return actions

    @property
    def default_action(self) -> str:
        return self.actions[0]


@dataclass(frozen=True)
class ImportReview:
    items: tuple[ReviewItem, ...]
    library_revision: int


@dataclass(frozen=True)
class ImportOutcome:
    imported: int
    replaced: int
    skipped: int


class LibraryService:
    def __init__(self, store: JsonStore | None, builtins: BuiltinSet | None = None) -> None:
        self.store = store
        self.builtins = builtins if builtins is not None else load_builtins()
        self.revision = 0
        self.warnings: tuple[StoreWarning, ...] = ()
        self._listeners: list[Callable[[], None]] = []
        if store is None:
            library = Library(self.builtins.recipes, self.builtins.revision)
        else:
            loaded = store.load()
            self.warnings = loaded.warnings
            library = loaded.value
            if loaded.source == "defaults":
                library = Library(self.builtins.recipes, self.builtins.revision)
        self.library = library
        if library.builtin_revision < self.builtins.revision:
            self._add_missing_builtins()

    @staticmethod
    def make_store(directory: Path, clock) -> JsonStore:
        return JsonStore(Path(directory) / LIBRARY_FILE, library_from_data,
                         lambda lib: lib.to_data(), lambda: Library(), clock)

    # -- observation -------------------------------------------------------------------------

    def subscribe(self, listener: Callable[[], None]) -> None:
        self._listeners.append(listener)

    def unsubscribe(self, listener: Callable[[], None]) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    # -- persistence -------------------------------------------------------------------------

    def _commit(self, library: Library) -> None:
        """Save first; only a successful save replaces the in-memory library."""
        if self.store is not None:
            self.store.save(library)
        self.library = library
        self.revision += 1
        for listener in list(self._listeners):
            listener()

    def _replace(self, recipes) -> None:
        self._commit(Library(tuple(recipes), self.library.builtin_revision))

    def _get(self, recipe_id: str) -> Recipe:
        recipe = self.library.by_id(recipe_id)
        if recipe is None:
            raise LibraryError(UNKNOWN_RECIPE, recipe_id)
        return recipe

    def _new_id(self, recipe: Recipe) -> str:
        base = f"user:{recipe.digest()[:12]}"
        taken = {r.id for r in self.library.recipes}
        candidate, n = base, 2
        while candidate in taken:
            candidate, n = f"{base}-{n}", n + 1
        return candidate

    @staticmethod
    def _clean_name(name: str) -> str:
        name = " ".join(str(name).split())
        if not name or len(name) > MAX_NAME:
            raise LibraryError(BAD_NAME, f"names need 1..{MAX_NAME} characters")
        return name

    # -- operations --------------------------------------------------------------------------

    def save_content(self, content: ShapeContent, name: str, tags=()) -> Recipe:
        draft = Recipe("draft", self._clean_name(name), content.kind, normalise_tags(tags),
                       False, False, curve=content.curve, record=content.record,
                       effects=content.effects)
        recipe = draft.with_(id=self._new_id(draft))
        self._replace([*self.library.recipes, recipe])
        return recipe

    def rename(self, recipe_id: str, name: str) -> None:
        name = self._clean_name(name)
        self._get(recipe_id)
        self._replace(r.with_(name=name) if r.id == recipe_id else r
                      for r in self.library.recipes)

    def set_tags(self, recipe_id: str, tags) -> None:
        self._get(recipe_id)
        self._replace(r.with_(tags=normalise_tags(tags)) if r.id == recipe_id else r
                      for r in self.library.recipes)

    def toggle_favorite(self, recipe_id: str) -> bool:
        value = not self._get(recipe_id).favorite
        self._replace(r.with_(favorite=value) if r.id == recipe_id else r
                      for r in self.library.recipes)
        return value

    def delete(self, recipe_ids: Iterable[str]) -> int:
        ids = set(recipe_ids)
        for recipe_id in ids:
            self._get(recipe_id)
        self._replace(r for r in self.library.recipes if r.id not in ids)
        return len(ids)

    def move(self, recipe_id: str, to_index: int) -> None:
        recipes = list(self.library.recipes)
        recipe = self._get(recipe_id)
        recipes.remove(recipe)
        recipes.insert(max(0, min(to_index, len(recipes))), recipe)
        self._replace(recipes)

    def reorder(self, recipe_ids: list[str]) -> None:
        """Set the manual order (all ids, as shown after a drag)."""
        if sorted(recipe_ids) != sorted(r.id for r in self.library.recipes):
            raise LibraryError(UNKNOWN_RECIPE, "order must list every recipe once")
        by_id = {r.id: r for r in self.library.recipes}
        self._replace(by_id[i] for i in recipe_ids)

    def restore_builtins(self) -> int:
        """Reset built-ins to their original content, name and tags (keeping favourites and
        positions) and re-add any that were deleted. Returns how many were re-added."""
        originals = {r.id: r for r in self.builtins.recipes}
        recipes = [originals[r.id].with_(favorite=r.favorite) if r.id in originals else r
                   for r in self.library.recipes]
        present = {r.id for r in recipes}
        missing = [r for r in self.builtins.recipes if r.id not in present]
        self._commit(Library(tuple(recipes + missing), self.builtins.revision))
        return len(missing)

    def _add_missing_builtins(self) -> None:
        present = {r.id for r in self.library.recipes}
        missing = [r for r in self.builtins.recipes if r.id not in present]
        library = Library((*self.library.recipes, *missing), self.builtins.revision)
        try:
            self._commit(library)
        except MotionAtelierError:
            self.library = library  # keep working in memory; the warning tells the user
        if missing:
            self.warnings = (*self.warnings, StoreWarning(BUILTINS_ADDED, str(len(missing))))

    # -- queries -----------------------------------------------------------------------------

    def query(self, text: str = "", sort: str = SORT_MANUAL) -> list[Recipe]:
        needle = text.casefold().strip()
        recipes = [r for r in self.library.recipes
                   if not needle or needle in r.name.casefold()
                   or any(needle in t.casefold() for t in r.tags)]
        if sort == SORT_NAME:
            recipes.sort(key=lambda r: (r.name.casefold(), r.name, r.id))
        elif sort == SORT_FAVORITES:
            order = {r.id: i for i, r in enumerate(self.library.recipes)}
            recipes.sort(key=lambda r: (not r.favorite, order[r.id]))
        return recipes

    def load_into(self, session, recipe_id: str) -> None:
        """Load a recipe into its shape's document as one local undo step (never the scene)."""
        recipe = self._get(recipe_id)
        doc = session.documents[recipe.kind]
        doc.cancel_gesture()
        doc.apply(f"Load {recipe.name}", content_of(recipe), frozenset())
        session.switch_shape(recipe.kind)

    # -- archives ----------------------------------------------------------------------------

    def export(self, recipe_ids: Iterable[str]) -> bytes:
        ids = list(recipe_ids)
        wanted = set(ids)
        recipes = [r for r in self.library.recipes if r.id in wanted]  # library order
        if len(recipes) != len(wanted):
            raise LibraryError(UNKNOWN_RECIPE, "selection contains unknown recipes")
        return export_archive(recipes)

    def review_import(self, blob: bytes) -> ImportReview:
        """Validate the archive (raises ``archive_invalid`` if damaged) and classify items."""
        items = read_archive(blob)
        digests = {r.digest(): r for r in self.library.recipes}
        names = {r.name.casefold(): r for r in self.library.recipes}
        seen_digests: set[str] = set()
        review = []
        for item in items:
            if item.recipe is None:
                review.append(ReviewItem(item.index, item.name, INVALID, None, item.problem))
                continue
            d = item.recipe.digest()
            if d in digests or d in seen_digests:
                state, conflict = DUPLICATE, digests[d].id if d in digests else None
            elif item.recipe.name.casefold() in names:
                state, conflict = NAME_CONFLICT, names[item.recipe.name.casefold()].id
            else:
                state, conflict = NEW, None
            seen_digests.add(d)
            review.append(ReviewItem(item.index, item.recipe.name, state, item.recipe,
                                     conflict_id=conflict))
        return ImportReview(tuple(review), self.revision)

    def apply_import(self, review: ImportReview, choices: dict[int, str] | None = None
                     ) -> ImportOutcome:
        """Apply every choice to an in-memory copy, then make one store write."""
        if review.library_revision != self.revision:
            raise LibraryError(LIBRARY_CHANGED, "the library changed since the review opened")
        choices = choices or {}
        recipes = list(self.library.recipes)
        imported = replaced = skipped = 0
        for item in review.items:
            action = choices.get(item.index, item.default_action)
            if action not in item.actions:
                raise LibraryError(BAD_ACTION, f"{action!r} not allowed for {item.name!r}")
            if action == ACTION_SKIP or item.recipe is None:
                skipped += 1
                continue
            incoming = item.recipe.with_(builtin=False)
            if action == ACTION_REPLACE:
                index = next(i for i, r in enumerate(recipes) if r.id == item.conflict_id)
                recipes[index] = incoming.with_(id=recipes[index].id)
                replaced += 1
                continue
            name = incoming.name
            if action == ACTION_RENAME or any(r.name.casefold() == name.casefold()
                                              for r in recipes):
                name = unique_name(name, (r.name for r in recipes))
            draft = incoming.with_(name=name)
            taken = {r.id for r in recipes}
            base = f"user:{draft.digest()[:12]}"
            new_id, n = base, 2
            while new_id in taken:
                new_id, n = f"{base}-{n}", n + 1
            recipes.append(draft.with_(id=new_id))
            imported += 1
        self._replace(recipes)
        return ImportOutcome(imported, replaced, skipped)


def recipe_kinds(recipes: Iterable[Recipe]) -> set[ShapeKind]:
    return {r.kind for r in recipes}

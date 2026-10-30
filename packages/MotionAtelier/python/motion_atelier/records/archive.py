# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MotionAtelier contributors
# This file is part of MotionAtelier, distributed under the GNU General Public License,
# version 3 or (at your option) any later version, with the Houdini integration permission.
# See LICENSE and HOUDINI_EXCEPTION.txt.
"""Deterministic recipe archives.

Layout of the ZIP::

    manifest.json            {"schema", "version", "items": [{"path", "sha256", "id"}]}
    recipes/0001.json ...    {"schema", "version", "recipe": <recipe data>}

Export is byte-reproducible: members in a fixed order, fixed timestamp (1980-01-01), fixed
mode and creator system, canonical JSON. Import validates the whole archive structure before
reading any recipe and rejects it entirely on any structural problem (unexpected or duplicate
names, absolute or parent paths, size and count limits, manifest mismatch, digest mismatch,
unknown schema or version). A recipe whose *content* cannot be used in this build (e.g. a
future generator version) is reported as invalid in the review rather than rejecting the
whole archive.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from dataclasses import dataclass
from typing import Any

from motion_atelier.curve.errors import MotionAtelierError
from motion_atelier.curve.serialize import canonical_json
from motion_atelier.records.common import document, exact_keys, expect_list
from motion_atelier.records.recipe import (
    RECIPE_SCHEMA,
    RECIPE_VERSION,
    Recipe,
    recipe_from_data,
)

ARCHIVE_SCHEMA = "motion_atelier.recipe_archive"
ARCHIVE_VERSION = 1
MANIFEST = "manifest.json"
MEMBER_PATTERN = re.compile(r"^recipes/\d{4}\.json$")
FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)
FILE_MODE = 0o100644
MAX_ITEMS = 2000
MAX_MEMBER_BYTES = 512 * 1024  # one recipe with a full effect stack is a few kB
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024

ARCHIVE_INVALID = "archive_invalid"


class ArchiveError(MotionAtelierError):
    def __init__(self, detail: str) -> None:
        super().__init__(ARCHIVE_INVALID, detail)


@dataclass(frozen=True)
class ArchiveItem:
    """One recipe from an archive: the parsed recipe, or why it cannot be used."""

    index: int
    recipe: Recipe | None
    name: str
    problem: str | None = None


def _member_bytes(recipe: Recipe) -> bytes:
    data = {"schema": RECIPE_SCHEMA, "version": RECIPE_VERSION, "recipe": recipe.to_data()}
    return (canonical_json(data) + "\n").encode("utf-8")


def export_archive(recipes) -> bytes:
    recipes = list(recipes)
    if not recipes:
        raise ArchiveError("nothing to export")
    if len(recipes) > MAX_ITEMS:
        raise ArchiveError(f"at most {MAX_ITEMS} recipes per archive")
    members: list[tuple[str, bytes]] = []
    items = []
    for n, recipe in enumerate(recipes, start=1):
        path = f"recipes/{n:04d}.json"
        payload = _member_bytes(recipe)
        members.append((path, payload))
        items.append({"path": path, "sha256": hashlib.sha256(payload).hexdigest(),
                      "id": recipe.id})
    manifest = {"schema": ARCHIVE_SCHEMA, "version": ARCHIVE_VERSION, "items": items}
    members.insert(0, (MANIFEST, (canonical_json(manifest) + "\n").encode("utf-8")))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path, payload in members:
            info = zipfile.ZipInfo(path, date_time=FIXED_DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = FILE_MODE << 16
            archive.writestr(info, payload, compresslevel=9)
    return buffer.getvalue()


def _json(data: bytes, what: str) -> Any:
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ArchiveError(f"{what} is not valid JSON: {exc}") from exc


def read_archive(blob: bytes) -> list[ArchiveItem]:
    """Validate the archive structure completely, then parse each recipe."""
    if len(blob) > MAX_ARCHIVE_BYTES:
        raise ArchiveError("archive too large")
    try:
        archive = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as exc:
        raise ArchiveError(f"not a ZIP archive: {exc}") from exc
    with archive:
        infos = archive.infolist()
        names = [i.filename for i in infos]
        if len(names) != len(set(names)):
            raise ArchiveError("duplicate member names")
        if len(infos) > MAX_ITEMS + 1:
            raise ArchiveError(f"more than {MAX_ITEMS} recipes")
        total = 0
        for info in infos:
            name = info.filename
            if name.startswith(("/", "\\")) or ":" in name or "\\" in name or \
                    any(part in ("..", ".") for part in name.split("/")):
                raise ArchiveError(f"unsafe member path {name!r}")
            if name != MANIFEST and not MEMBER_PATTERN.match(name):
                raise ArchiveError(f"unexpected member {name!r}")
            if info.is_dir():
                raise ArchiveError(f"unexpected directory {name!r}")
            if info.file_size > MAX_MEMBER_BYTES:
                raise ArchiveError(f"{name} is larger than {MAX_MEMBER_BYTES} bytes")
            total += info.file_size
            if total > MAX_TOTAL_BYTES:
                raise ArchiveError("archive content too large")
        if MANIFEST not in names:
            raise ArchiveError("missing manifest.json")
        try:
            manifest = document(_json(archive.read(MANIFEST), MANIFEST), ARCHIVE_SCHEMA,
                                ARCHIVE_VERSION, {"items"})
            items = expect_list(manifest["items"], "items", MAX_ITEMS)
        except MotionAtelierError as exc:
            raise ArchiveError(f"manifest: {exc.detail}") from exc
        listed = []
        for item in items:
            try:
                item = exact_keys(item, {"path", "sha256", "id"}, "manifest item")
            except MotionAtelierError as exc:
                raise ArchiveError(exc.detail) from exc
            if not isinstance(item["path"], str) or not MEMBER_PATTERN.match(item["path"]):
                raise ArchiveError(f"bad manifest path {item['path']!r}")
            listed.append(item)
        listed_paths = [i["path"] for i in listed]
        if len(set(listed_paths)) != len(listed_paths):
            raise ArchiveError("manifest lists a member twice")
        if sorted(listed_paths) != sorted(n for n in names if n != MANIFEST):
            raise ArchiveError("manifest does not match the archive members")
        payloads = []
        for item in listed:
            payload = archive.read(item["path"])
            if hashlib.sha256(payload).hexdigest() != item["sha256"]:
                raise ArchiveError(f"{item['path']} is damaged (checksum mismatch)")
            payloads.append((item, payload))
    result: list[ArchiveItem] = []
    for index, (item, payload) in enumerate(payloads):
        wrapper = _json(payload, item["path"])
        try:
            body = document(wrapper, RECIPE_SCHEMA, RECIPE_VERSION, {"recipe"})["recipe"]
        except MotionAtelierError as exc:
            raise ArchiveError(f"{item['path']}: {exc.detail}") from exc
        name = body.get("name") if isinstance(body, dict) and isinstance(body.get("name"),
                                                                         str) else item["path"]
        try:
            recipe = recipe_from_data(body)
        except MotionAtelierError as exc:
            result.append(ArchiveItem(index, None, name, exc.detail or exc.code))
            continue
        result.append(ArchiveItem(index, recipe, recipe.name))
    return result

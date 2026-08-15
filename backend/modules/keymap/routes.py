"""User keybinding overrides, persisted to keymap.json.

Deliberately **not** a setting. `SettingValue` is `str | int | float | bool`, so a
list of bindings has no home there, and `GET /api/settings` hands the whole bag to
the browser on every boot. The keymap gets its own file and its own shape, and
mirrors the settings split: the frontend owns the schema, defaults and conflict
rules; the backend stores what it is given. See docs/architecture/keybindings.mdx.
"""

import json
from pathlib import Path

from fastapi import APIRouter

from backend import paths
from backend.modules.keymap.models import (
    KEYMAP_SCHEMA,
    KEYMAP_VERSION,
    Keymap,
    KeymapBinding,
)

router = APIRouter(prefix="/keymap", tags=["keymap"])


def _keymap_path() -> Path:
    return paths.data_dir() / "keymap.json"


def _empty() -> Keymap:
    return Keymap(schema=KEYMAP_SCHEMA, version=KEYMAP_VERSION, bindings=[])


def read_keymap() -> Keymap:
    """The stored keymap, or an empty one.

    A corrupt or unreadable file falls back to defaults rather than raising: a bad
    keymap must never be able to lock the user out of the app that would let them
    fix it.
    """
    path = _keymap_path()
    if not path.is_file():
        return _empty()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Keymap.model_validate(data)
    except (ValueError, OSError):
        return _empty()


def write_keymap(keymap: Keymap) -> None:
    path = _keymap_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(keymap.model_dump(by_alias=True), indent=2),
        encoding="utf-8",
    )


@router.get("", response_model=Keymap)
def get_keymap() -> Keymap:
    return read_keymap()


@router.put("", response_model=Keymap)
def put_keymap(body: Keymap) -> Keymap:
    """Replace the whole keymap. Whole-document rather than per-binding, because a
    rebind is two edits (add the new, disable the old) that must land together."""
    stored = Keymap(
        schema=KEYMAP_SCHEMA, version=KEYMAP_VERSION, bindings=body.bindings
    )
    write_keymap(stored)
    return stored


@router.delete("", response_model=Keymap)
def delete_keymap() -> Keymap:
    """Drop every override, restoring the shipped defaults."""
    empty = _empty()
    write_keymap(empty)
    return empty


__all__ = ["KeymapBinding", "read_keymap", "router", "write_keymap"]

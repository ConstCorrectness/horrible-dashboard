"""User settings: a flat key→value bag persisted to settings.json.

The backend is deliberately schema-agnostic — it stores whatever JSON-able value
the frontend writes for a namespaced key. Declarations, types, and defaults are
owned by the frontend (see docs/modules/settings.md). Mirrors the dashboard
layout store.
"""

import copy
import json
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter
from fastapi import Path as PathParam

from backend import jsonstore, paths
from backend.modules.settings.models import (
    SETTING_KEY_PATTERN,
    SettingsValues,
    SettingValue,
    is_secret_key,
)

router = APIRouter(prefix="/settings", tags=["settings"])

SettingKey = Annotated[str, PathParam(pattern=SETTING_KEY_PATTERN)]


def _settings_path() -> Path:
    return paths.data_dir() / "settings.json"


#: Serializes the read-modify-write around the whole bag — see `backend.jsonstore`
#: for why every one of these stores needs it. The bug it fixed here: first-run
#: setup writes the name, the theme and `desktop.oobeComplete` from one click, the
#: overlapping PUTs each read the pre-change bag, and the last writer dropped the
#: completion flag — 200 on every request, and a finished setup wizard that came
#: back on the next launch.
def _lock():
    return jsonstore.locked(_settings_path())


#: The parsed bag, with the file identity it was parsed from. `get_value` is called
#: per HTTP request and per WebSocket frame (the telemetry middleware reads its
#: body cap through it), and a read + `json.loads` of the whole bag each time is
#: event-loop time. Keyed on the file's stat rather than invalidated on write
#: alone, because this process is not the only writer: the bundled game server
#: shares the file, and so does anything that edits it by hand.
_cache: tuple[Path, tuple[int, int], dict] | None = None


def _stamp(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _load() -> dict:
    """The parsed bag, shared — callers must not mutate it. Hold `_lock()`."""
    global _cache
    path = _settings_path()
    stamp = _stamp(path)
    if stamp is None:
        _cache = None
        return {}
    if _cache is not None and _cache[0] == path and _cache[1] == stamp:
        return _cache[2]
    text = jsonstore.read_text(path)
    if text is None:
        _cache = None
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    # Stamped *before* the read: a write landing in between leaves a stamp that no
    # longer matches the file, so the next call re-reads instead of trusting it.
    _cache = (path, stamp, data)
    return data


def _read() -> dict:
    """A private copy of the bag, safe to modify and write back."""
    return dict(_load())


def _write(data: dict) -> None:
    """Write the bag atomically. A direct `write_text` truncates first, so a reader
    arriving mid-write (or a crash) sees a half-written file and `_read` falls back
    to `{}` — i.e. every setting the user ever chose, gone."""
    global _cache
    _cache = None
    jsonstore.write_text(_settings_path(), json.dumps(data))


def get_value(key: str, default: Any) -> Any:
    """Effective value for a setting key: the user override if set, else the
    caller's default. Backend consumers pass their own default because schemas and
    defaults are declared on the frontend (see docs/modules/settings.md)."""
    with _lock():
        bag = _load()
        if key not in bag:
            return default
        value = bag[key]
    # A container is copied so a caller editing what it was handed cannot edit
    # the cached bag every later reader sees.
    return copy.deepcopy(value) if isinstance(value, (dict, list)) else value


def set_value(key: str, value: Any) -> None:
    """Persist a setting value server-side. For backend consumers that write a
    setting directly (e.g. the agent persisting an 'always allow' permission rule),
    mirroring the frontend's PUT /settings/{key}."""
    with _lock():
        data = _read()
        data[key] = value
        _write(data)


def _served(data: dict) -> SettingsValues:
    """The bag as it leaves the process: secret-shaped values blanked.

    Blanked rather than omitted, because a key vanishing from the response is
    indistinguishable from never having been set — and a settings page that
    cannot tell those apart will happily overwrite a saved token with an empty
    string the first time someone saves an unrelated field.
    """
    values = {k: ("" if is_secret_key(k) else v) for k, v in data.items()}
    secret_keys = [
        k for k, v in data.items() if is_secret_key(k) and v not in ("", None)
    ]
    return SettingsValues(values=values, secretKeys=secret_keys)


@router.get("", response_model=SettingsValues)
def get_settings() -> SettingsValues:
    with _lock():
        return _served(_read())


@router.put("/{key}", response_model=SettingValue)
def put_setting(key: SettingKey, body: SettingValue) -> SettingValue:
    with _lock():
        data = _read()
        data[key] = body.value
        _write(data)
    if key.startswith("hassault."):
        # Account-scoped keys follow the account (`hassault/account_sync.py`).
        # Fire-and-forget: a settings write never waits on a server.
        from backend.modules.hassault import account_sync

        account_sync.push_setting(key, body.value)
    return body


@router.delete("/{key}", response_model=SettingsValues)
def delete_setting(key: SettingKey) -> SettingsValues:
    """Clear an override so the key falls back to its frontend default."""
    with _lock():
        data = _read()
        if key in data:
            del data[key]
            _write(data)
        return _served(data)

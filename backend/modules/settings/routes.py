"""User settings: a flat key→value bag persisted to settings.json.

The backend is deliberately schema-agnostic — it stores whatever JSON-able value
the frontend writes for a namespaced key. Declarations, types, and defaults are
owned by the frontend (see docs/modules/settings.md). Mirrors the dashboard
layout store.
"""

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


def _read() -> dict:
    text = jsonstore.read_text(_settings_path())
    if text is None:
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: dict) -> None:
    """Write the bag atomically. A direct `write_text` truncates first, so a reader
    arriving mid-write (or a crash) sees a half-written file and `_read` falls back
    to `{}` — i.e. every setting the user ever chose, gone."""
    jsonstore.write_text(_settings_path(), json.dumps(data))


def get_value(key: str, default: Any) -> Any:
    """Effective value for a setting key: the user override if set, else the
    caller's default. Backend consumers pass their own default because schemas and
    defaults are declared on the frontend (see docs/modules/settings.md)."""
    with _lock():
        return _read().get(key, default)


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

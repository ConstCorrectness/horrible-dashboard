"""User settings: a flat key→value bag persisted to settings.json.

The backend is deliberately schema-agnostic — it stores whatever JSON-able value
the frontend writes for a namespaced key. Declarations, types, and defaults are
owned by the frontend (see docs/modules/settings.md). Mirrors the dashboard
layout store.
"""

import json
import os
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter
from fastapi import Path as PathParam

from backend.modules.settings.models import (
    SETTING_KEY_PATTERN,
    SettingsValues,
    SettingValue,
)

router = APIRouter(prefix="/settings", tags=["settings"])

SettingKey = Annotated[str, PathParam(pattern=SETTING_KEY_PATTERN)]


def _settings_path() -> Path:
    return Path(os.environ.get("HORRIBLE_DATA_DIR", ".data")) / "settings.json"


def _read() -> dict:
    path = _settings_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: dict) -> None:
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def get_value(key: str, default: Any) -> Any:
    """Effective value for a setting key: the user override if set, else the
    caller's default. Backend consumers pass their own default because schemas and
    defaults are declared on the frontend (see docs/modules/settings.md)."""
    return _read().get(key, default)


def set_value(key: str, value: Any) -> None:
    """Persist a setting value server-side. For backend consumers that write a
    setting directly (e.g. the agent persisting an 'always allow' permission rule),
    mirroring the frontend's PUT /settings/{key}."""
    data = _read()
    data[key] = value
    _write(data)


@router.get("", response_model=SettingsValues)
def get_settings() -> SettingsValues:
    return SettingsValues(values=_read())


@router.put("/{key}", response_model=SettingValue)
def put_setting(key: SettingKey, body: SettingValue) -> SettingValue:
    data = _read()
    data[key] = body.value
    _write(data)
    return body


@router.delete("/{key}", response_model=SettingsValues)
def delete_setting(key: SettingKey) -> SettingsValues:
    """Clear an override so the key falls back to its frontend default."""
    data = _read()
    if key in data:
        del data[key]
        _write(data)
    return SettingsValues(values=data)

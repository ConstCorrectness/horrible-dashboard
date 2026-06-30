"""Named-connection store.

User connections persist as a list in ``.data/connections.json`` (mirrors the
settings store). The built-in ``app`` connection — the local vector store — is always
synthesized first and cannot be edited or removed. Credentials are stored as-is in the
gitignored ``.data/`` dir (plaintext for v1; encryption is a tracked follow-up).
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from backend.modules.database.vectorstore import get_db_path

# Connection config keys whose values must never be returned to the client.
_SECRET_FIELDS = {"password", "dsn"}

BUILTIN_APP_ID = "app"


def _store_path() -> Path:
    return Path(os.environ.get("HORRIBLE_DATA_DIR", ".data")) / "connections.json"


def _read() -> list[dict[str, Any]]:
    path = _store_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return []
    return [c for c in data if isinstance(c, dict)] if isinstance(data, list) else []


def _write(rows: list[dict[str, Any]]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows), encoding="utf-8")


def _builtin_app() -> dict[str, Any]:
    return {
        "id": BUILTIN_APP_ID,
        "name": "App (vector store)",
        "provider": "sqlite",
        "config": {"path": str(get_db_path()), "builtin": True},
        "builtin": True,
    }


def list_connections() -> list[dict[str, Any]]:
    """Built-in app connection first, then user connections."""
    return [_builtin_app(), *_read()]


def get_connection(conn_id: str) -> dict[str, Any] | None:
    return next((c for c in list_connections() if c["id"] == conn_id), None)


def resolve_config(conn: dict[str, Any]) -> dict[str, Any]:
    """The driver-ready config for a connection (built-in app always points at the
    live vector-store path, even if the data dir moved since it was created)."""
    if conn.get("id") == BUILTIN_APP_ID:
        return {"path": str(get_db_path()), "builtin": True}
    return dict(conn.get("config") or {})


def redact(conn: dict[str, Any]) -> dict[str, Any]:
    """A client-safe copy: secret config values replaced with a boolean 'isSet'."""
    config = dict(conn.get("config") or {})
    safe_config = {
        k: (bool(v) if k in _SECRET_FIELDS else v) for k, v in config.items()
    }
    return {**conn, "config": safe_config}


def add_connection(name: str, provider: str, config: dict[str, Any]) -> dict[str, Any]:
    rows = _read()
    record = {
        "id": uuid.uuid4().hex[:12],
        "name": name,
        "provider": provider,
        "config": config,
        "builtin": False,
    }
    rows.append(record)
    _write(rows)
    return record


def update_connection(
    conn_id: str, name: str, provider: str, config: dict[str, Any]
) -> dict[str, Any] | None:
    rows = _read()
    for record in rows:
        if record["id"] == conn_id:
            record["name"] = name
            record["provider"] = provider
            # Preserve existing secrets the client omitted (it never receives them).
            merged = dict(record.get("config") or {})
            merged.update(config)
            record["config"] = merged
            _write(rows)
            return record
    return None


def delete_connection(conn_id: str) -> bool:
    rows = _read()
    remaining = [c for c in rows if c["id"] != conn_id]
    if len(remaining) == len(rows):
        return False
    _write(remaining)
    return True

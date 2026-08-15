"""Named-connection store.

User connections persist as a list in ``.data/connections.json`` (mirrors the
settings store). The built-in ``app`` connection — the node's own SQLite database
(``.data/app.db``: library sources, browser history, code symbols, tasks) — is always
synthesized first and cannot be edited or removed. Credentials are stored as-is in the
gitignored ``.data/`` dir (plaintext for v1; encryption is a tracked follow-up).

Note ``app`` points at ``app.db``, **not** the LanceDB vector store: LanceDB is a
directory, not a SQLite file, so there is nothing for a *SQL* connection to open (see
``app_db.py`` — the two were one path before the LanceDB migration, and conflating
them is the classic bug here). The vector store is reachable instead through the
second built-in, ``vectors``, a ``lancedb`` json-dialect connection.

A **third** built-in, ``atlas``, appears only when this node administers the shared
MongoDB cluster (``ATLAS_ADMIN``; see ``backend/atlas.py:admin_access``). It is
conditional because every node on the social fabric holds cluster credentials to
publish its own presence record — synthesizing the connection whenever credentials
exist would turn a narrow, single-purpose credential into a cluster-wide console on
every user's machine.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from backend.modules.database.app_db import get_app_db_path
from backend import paths


# Connection config keys whose values must never be returned to the client. `uri` is
# here because a MongoDB (or any) connection string embeds the password — a redactor
# that only knows about a `password` field would hand it straight to the browser.
_SECRET_FIELDS = {"password", "dsn", "uri"}

BUILTIN_APP_ID = "app"
BUILTIN_VECTORS_ID = "vectors"
BUILTIN_ATLAS_ID = "atlas"

# Built-in connections are synthesized, not stored, so they can't be edited or deleted.
BUILTIN_IDS = frozenset({BUILTIN_APP_ID, BUILTIN_VECTORS_ID, BUILTIN_ATLAS_ID})


def _store_path() -> Path:
    return paths.data_dir() / "connections.json"


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
        "name": "App (local database)",
        "provider": "sqlite",
        "config": {"path": str(get_app_db_path()), "builtin": True},
        "builtin": True,
    }


def _builtin_vectors() -> dict[str, Any]:
    """The node's own LanceDB vector store, as a queryable connection.

    This is the counterpart to ``app``: same data dir, but the vector half — library
    chunks, symdex, CLIP siblings. It's a ``lancedb`` (json-dialect) connection, which
    is what makes it reachable from the console at all; a SQL connection never could,
    since LanceDB is a directory of datasets rather than a database file.
    """
    from backend.modules.database.drivers.lancedb_driver import default_path  # noqa: PLC0415

    return {
        "id": BUILTIN_VECTORS_ID,
        "name": "App (vector store)",
        "provider": "lancedb",
        "config": {"path": default_path(), "builtin": True},
        "builtin": True,
    }


def _builtin_atlas() -> dict[str, Any] | None:
    """The shared MongoDB Atlas cluster, for an operator who administers it.

    Returns None unless ``ATLAS_ADMIN`` is set (default off) — see
    ``atlas.admin_access()`` for why credentials-present is the wrong gate.

    The config exposed here carries **no URI**: the connection string holds the cluster
    password, and this dict is what ``redact()`` turns into the browser's payload.
    ``resolve_config`` reads the URI from the environment at query time instead, so the
    secret never enters the store or a response body.
    """
    from backend import atlas  # noqa: PLC0415 — avoid an import cycle at module load

    access = atlas.admin_access()
    if access == "off":
        return None
    return {
        "id": BUILTIN_ATLAS_ID,
        "name": f"Atlas cluster ({atlas.cluster_label()})",
        "provider": "mongodb",
        "config": {
            "cluster": atlas.cluster_label(),
            "database": atlas.database_name(),
            "read_only": access == "ro",
            "builtin": True,
        },
        "builtin": True,
    }


def list_connections() -> list[dict[str, Any]]:
    """Built-in connections first (app database, vector store, then the Atlas cluster
    when this node administers it), then user ones."""
    atlas_conn = _builtin_atlas()
    builtins = [_builtin_app(), _builtin_vectors()]
    if atlas_conn is not None:
        builtins.append(atlas_conn)
    return [*builtins, *_read()]


def get_connection(conn_id: str) -> dict[str, Any] | None:
    return next((c for c in list_connections() if c["id"] == conn_id), None)


def resolve_config(conn: dict[str, Any]) -> dict[str, Any]:
    """The driver-ready config for a connection (built-in app always points at the
    live app-database path, even if the data dir moved since it was created)."""
    if conn.get("id") == BUILTIN_APP_ID:
        return {"path": str(get_app_db_path()), "builtin": True}
    if conn.get("id") == BUILTIN_VECTORS_ID:
        return dict(_builtin_vectors()["config"])
    if conn.get("id") == BUILTIN_ATLAS_ID:
        # The URI is added here and nowhere else, so the password reaches the driver
        # without ever passing through the connection store or a client response. The
        # access check is repeated rather than trusted: this is the function every
        # query path funnels through, so it is the right place to fail closed if
        # ATLAS_ADMIN was removed since the connection list was built.
        from backend import atlas  # noqa: PLC0415

        record = _builtin_atlas()
        if record is None:
            raise PermissionError(
                "Atlas admin access is not enabled on this node (set ATLAS_ADMIN)."
            )
        return {**record["config"], "uri": atlas.admin_uri()}
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

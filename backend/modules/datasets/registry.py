"""The catalog of registered datasets, in `app.db`.

A *registered* dataset is the unit a recipe points at, and registering one is the
whole difference between "the run used `trl-lib/Capybara`" and "the run used
`trl-lib/Capybara`, train split, detected ShareGPT, mapped from `conversations`".
The second is reproducible; the first is a note to yourself.

It lives in `app.db` beside `model_lineage` and the eval tables, deliberately — so
"which sweeps trained on the dataset I built from last month's eval failures" is a
join in the `database` console rather than a feature request.

Note the `case_hash`/`lineage` precedent: `CREATE TABLE IF NOT EXISTS` does nothing
to a table that already exists, so a later column needs `_ensure_column`, which is
why that helper is here from the start.

The **fingerprint** is over the definition (source, ref, config, split, format,
column map) rather than over the rows. Hashing the rows would mean downloading
them, which is exactly what the peek layer exists to avoid; and the question a
recipe needs answered is "was this the same dataset *specification*", which is what
changes when someone edits a column map and silently changes what a rerun trains on.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
import uuid
from contextlib import contextmanager
from typing import Any, Generator

from backend.modules.database.app_db import ensure_app_db_dir
from backend.modules.datasets.models import DatasetModel

logger = logging.getLogger(__name__)

_initialized: set[str] = set()


@contextmanager
def _conn() -> Generator[sqlite3.Connection, None, None]:
    path = str(ensure_app_db_dir())
    if path not in _initialized:
        # Marked before the call, not after: `init_db` opens a connection through
        # this same helper and would otherwise recurse forever. Keyed by path
        # because `HORRIBLE_DATA_DIR` is env-driven and a test pointing at a fresh
        # tmp dir must not inherit a flag from the previous one.
        _initialized.add(path)
        init_db()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_column(conn: sqlite3.Connection, table: str, name: str, ddl: str) -> None:
    """Add a column if it is not there yet. SQLite has no `ADD COLUMN IF NOT EXISTS`."""
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if name not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def init_db() -> None:
    with _conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS datasets (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                ref TEXT NOT NULL DEFAULT '',
                config TEXT NOT NULL DEFAULT '',
                split TEXT NOT NULL DEFAULT 'train',
                format TEXT NOT NULL DEFAULT 'unknown',
                column_map_json TEXT NOT NULL DEFAULT '{}',
                rows INTEGER,
                path TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                fingerprint TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_datasets_source ON datasets(source)"
        )
        _ensure_column(conn, "datasets", "path", "TEXT NOT NULL DEFAULT ''")


def fingerprint(
    source: str, ref: str, config: str, split: str, fmt: str, column_map: dict[str, str]
) -> str:
    """Content identity of a dataset *definition*. See the module docstring."""
    payload = json.dumps(
        {
            "source": source,
            "ref": ref,
            "config": config,
            "split": split,
            "format": fmt,
            "columns": dict(sorted(column_map.items())),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _row_to_model(row: sqlite3.Row) -> DatasetModel:
    try:
        column_map = json.loads(row["column_map_json"] or "{}")
    except ValueError:
        column_map = {}
    return DatasetModel(
        id=row["id"],
        name=row["name"],
        source=row["source"],
        ref=row["ref"],
        config=row["config"],
        split=row["split"],
        format=row["format"],
        column_map=column_map if isinstance(column_map, dict) else {},
        rows=row["rows"],
        path=row["path"] or "",
        notes=row["notes"],
        fingerprint=row["fingerprint"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def register(
    *,
    name: str,
    source: str,
    ref: str,
    config: str = "",
    split: str = "train",
    fmt: str = "unknown",
    column_map: dict[str, str] | None = None,
    rows: int | None = None,
    path: str = "",
    notes: str = "",
) -> DatasetModel:
    """Save a dataset definition. Re-registering the same definition updates it.

    Keyed by fingerprint rather than by name: registering `gsm8k/train` twice is
    one dataset with two names in the user's head, and two rows would mean a sweep
    could point at either and nothing would say they were the same.
    """
    columns = dict(column_map or {})
    print_ = fingerprint(source, ref, config, split, fmt, columns)
    now = time.time()
    with _conn() as conn:
        existing = conn.execute(
            "SELECT * FROM datasets WHERE fingerprint = ?", (print_,)
        ).fetchone()
        dataset_id = existing["id"] if existing else uuid.uuid4().hex[:12]
        created = existing["created_at"] if existing else now
        conn.execute(
            """
            INSERT INTO datasets
                (id, name, source, ref, config, split, format, column_map_json,
                 rows, path, notes, fingerprint, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                split = excluded.split,
                config = excluded.config,
                format = excluded.format,
                column_map_json = excluded.column_map_json,
                rows = excluded.rows,
                path = excluded.path,
                notes = excluded.notes,
                updated_at = excluded.updated_at
            """,
            (
                dataset_id,
                name or ref,
                source,
                ref,
                config,
                split,
                fmt,
                json.dumps(columns),
                rows,
                path,
                notes,
                print_,
                created,
                now,
            ),
        )
    found = get(dataset_id)
    assert found is not None  # just written
    return found


def get(dataset_id: str) -> DatasetModel | None:
    if not dataset_id:
        return None
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM datasets WHERE id = ?", (dataset_id,)
        ).fetchone()
    return _row_to_model(row) if row else None


def list_datasets(source: str = "") -> list[DatasetModel]:
    query = "SELECT * FROM datasets"
    params: tuple[Any, ...] = ()
    if source:
        query += " WHERE source = ?"
        params = (source,)
    query += " ORDER BY updated_at DESC"
    with _conn() as conn:
        return [_row_to_model(r) for r in conn.execute(query, params)]


def update(dataset_id: str, **changes: Any) -> DatasetModel | None:
    """Edit a registered dataset. Re-fingerprints, because the definition changed.

    A column map edit changes what a rerun trains on, so it must change the
    identity — otherwise two runs record the same fingerprint for two different
    datasets, which is the one thing the fingerprint exists to prevent.
    """
    current = get(dataset_id)
    if current is None:
        return None
    merged = current.model_dump()
    for key, value in changes.items():
        if value is not None and key in merged:
            merged[key] = value
    print_ = fingerprint(
        merged["source"],
        merged["ref"],
        merged["config"],
        merged["split"],
        merged["format"],
        merged["column_map"],
    )
    with _conn() as conn:
        conn.execute(
            """
            UPDATE datasets SET name = ?, config = ?, split = ?, format = ?,
                column_map_json = ?, notes = ?, fingerprint = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                merged["name"],
                merged["config"],
                merged["split"],
                merged["format"],
                json.dumps(merged["column_map"]),
                merged["notes"],
                print_,
                time.time(),
                dataset_id,
            ),
        )
    return get(dataset_id)


def delete(dataset_id: str) -> bool:
    with _conn() as conn:
        cur = conn.execute("DELETE FROM datasets WHERE id = ?", (dataset_id,))
        return cur.rowcount > 0


def reset() -> None:
    """Test hook: forget which paths were initialized."""
    _initialized.clear()

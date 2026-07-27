"""Records storage: user-defined tables in the app's own SQLite database.

Two levels. `record_schemas` is the catalog — one row per user table, holding its
field declarations as JSON. Each schema also gets a **physical** table, `rec_<id>`,
with a real column per field.

Physical tables rather than one generic `(schema_id, data JSON)` blob table, because
the payoff is concrete: a CRM built here is immediately queryable from the
**database** console's built-in `app` connection, from `dash`, and by the `dba`
agent, with no new plumbing at all. The cost is schema evolution, so v1 is
deliberately additive — `ALTER TABLE ADD COLUMN` only. Retiring a field marks it
`hidden` and leaves the column in place; SQLite's DROP COLUMN has enough caveats
(indexes, generated columns) that silently losing a user's data to a form edit is
not a trade worth making. See docs/modules/records.mdx.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Generator

from backend.modules.database.app_db import ensure_app_db_dir
from backend.modules.records.models import (
    FieldDecl,
    RecordSchema,
    SCHEMA_ID_PATTERN,
)

# SQLite affinity per declared field type. Everything that is conceptually text
# stays TEXT so sorting and LIKE behave predictably; dates are ISO-8601 strings for
# the same reason (SQLite has no date type, and ISO strings sort correctly).
_AFFINITY: dict[str, str] = {
    "text": "TEXT",
    "longtext": "TEXT",
    "number": "REAL",
    "date": "TEXT",
    "select": "TEXT",
    "url": "TEXT",
    "email": "TEXT",
    "ref": "TEXT",
}

# Column names we own on every records table; a field may not shadow them.
RESERVED_KEYS = frozenset({"id", "created_at", "updated_at"})

_SCHEMA_ID_RE = re.compile(SCHEMA_ID_PATTERN)


class RecordsError(ValueError):
    """A caller-visible problem: unknown schema/field, bad id, reserved name."""


@contextmanager
def get_db_conn() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(str(ensure_app_db_dir()))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def table_name(schema_id: str) -> str:
    """The physical table for a schema. Validates rather than escapes: the id is
    interpolated into DDL (SQLite cannot parameterize identifiers), so the pattern
    check *is* the injection defense and must stay strict."""
    if not _SCHEMA_ID_RE.match(schema_id):
        raise RecordsError(f"invalid schema id: {schema_id!r}")
    return f"rec_{schema_id}"


def init_records_db() -> None:
    """Create the catalog + proposal tables (idempotent)."""
    with get_db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS record_schemas (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                icon TEXT,
                fields TEXT NOT NULL DEFAULT '[]',
                board_column TEXT,
                title_column TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS record_proposals (
                id TEXT PRIMARY KEY,
                schema_id TEXT NOT NULL,
                record_id TEXT,
                fields TEXT NOT NULL DEFAULT '{}',
                source TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_proposal_schema "
            "ON record_proposals(schema_id, status)"
        )


# --- Schemas -----------------------------------------------------------------


def _schema_from_row(row: sqlite3.Row) -> RecordSchema:
    return RecordSchema(
        id=row["id"],
        name=row["name"],
        icon=row["icon"],
        fields=[FieldDecl(**f) for f in json.loads(row["fields"] or "[]")],
        board_column=row["board_column"],
        title_column=row["title_column"],
    )


def list_schemas() -> list[RecordSchema]:
    with get_db_conn() as conn:
        rows = conn.execute("SELECT * FROM record_schemas ORDER BY name").fetchall()
    return [_schema_from_row(r) for r in rows]


def get_schema(schema_id: str) -> RecordSchema | None:
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM record_schemas WHERE id = ?", (schema_id,)
        ).fetchone()
    return _schema_from_row(row) if row else None


def require_schema(schema_id: str) -> RecordSchema:
    schema = get_schema(schema_id)
    if schema is None:
        raise RecordsError(f"unknown schema: {schema_id!r}")
    return schema


def save_schema(schema: RecordSchema) -> RecordSchema:
    """Create or update a schema and reconcile its physical table.

    Additive by design: a field present in the new declaration but missing from the
    table is added; a column whose field disappeared is left alone (the declaration
    is what the UI reads, so dropping the *declaration* already hides it)."""
    table = table_name(schema.id)
    for field in schema.fields:
        if field.key in RESERVED_KEYS:
            raise RecordsError(f"field key {field.key!r} is reserved")
    keys = [f.key for f in schema.fields]
    if len(set(keys)) != len(keys):
        raise RecordsError("duplicate field keys")
    if schema.board_column and schema.board_column not in keys:
        raise RecordsError(f"board_column {schema.board_column!r} is not a field")
    if schema.title_column and schema.title_column not in keys:
        raise RecordsError(f"title_column {schema.title_column!r} is not a field")

    with get_db_conn() as conn:
        conn.execute(
            """
            INSERT INTO record_schemas (id, name, icon, fields, board_column, title_column)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                icon = excluded.icon,
                fields = excluded.fields,
                board_column = excluded.board_column,
                title_column = excluded.title_column
            """,
            (
                schema.id,
                schema.name,
                schema.icon,
                json.dumps([f.model_dump() for f in schema.fields]),
                schema.board_column,
                schema.title_column,
            ),
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {table} (
                id TEXT PRIMARY KEY,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            )
            """
        )
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for field in schema.fields:
            if field.key in existing:
                continue
            # Identifiers can't be bound; both halves are pattern-validated above.
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN {field.key} "
                f"{_AFFINITY.get(field.type, 'TEXT')}"
            )
    return schema


def delete_schema(schema_id: str, *, drop_table: bool = False) -> None:
    """Forget a schema. The data table survives unless `drop_table` — the catalog
    row is cheap to recreate, the rows are not."""
    table = table_name(schema_id)
    with get_db_conn() as conn:
        conn.execute("DELETE FROM record_schemas WHERE id = ?", (schema_id,))
        conn.execute("DELETE FROM record_proposals WHERE schema_id = ?", (schema_id,))
        if drop_table:
            conn.execute(f"DROP TABLE IF EXISTS {table}")


# --- Rows --------------------------------------------------------------------


def _coerce(field: FieldDecl, value: Any) -> Any:
    if value is None or value == "":
        return None
    if field.type == "number":
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise RecordsError(
                f"{field.key}: expected a number, got {value!r}"
            ) from exc
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value)


def _validated_values(schema: RecordSchema, values: dict[str, Any]) -> dict[str, Any]:
    by_key = {f.key: f for f in schema.fields}
    unknown = [k for k in values if k not in by_key]
    if unknown:
        raise RecordsError(
            f"unknown field(s) for {schema.id}: {', '.join(sorted(unknown))}"
        )
    return {k: _coerce(by_key[k], v) for k, v in values.items()}


def list_rows(
    schema_id: str,
    *,
    where: dict[str, Any] | None = None,
    search: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Rows of a schema. `where` is field→value equality and `search` is a LIKE
    across the text fields — deliberately not raw SQL: an agent that needs real SQL
    has the database tools, and this table is visible to them."""
    schema = require_schema(schema_id)
    table = table_name(schema_id)
    by_key = {f.key: f for f in schema.fields}
    clauses: list[str] = []
    params: list[Any] = []
    for key, value in (where or {}).items():
        if key not in by_key:
            raise RecordsError(f"unknown field: {key!r}")
        if value is None:
            clauses.append(f"{key} IS NULL")
        else:
            clauses.append(f"{key} = ?")
            params.append(_coerce(by_key[key], value))
    if search:
        text_keys = [
            f.key
            for f in schema.fields
            if f.type in ("text", "longtext", "select", "url", "email", "ref")
        ]
        if text_keys:
            clauses.append("(" + " OR ".join(f"{k} LIKE ?" for k in text_keys) + ")")
            params.extend([f"%{search}%"] * len(text_keys))
    sql = f"SELECT * FROM {table}"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
    params.extend([max(1, min(limit, 1000)), max(0, offset)])
    with get_db_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_row(schema_id: str, record_id: str) -> dict[str, Any] | None:
    table = table_name(require_schema(schema_id).id)
    with get_db_conn() as conn:
        row = conn.execute(
            f"SELECT * FROM {table} WHERE id = ?", (record_id,)
        ).fetchone()
    return dict(row) if row else None


def create_row(schema_id: str, values: dict[str, Any]) -> dict[str, Any]:
    schema = require_schema(schema_id)
    table = table_name(schema_id)
    clean = _validated_values(schema, values)
    record_id = uuid.uuid4().hex[:12]
    stamp = _now()
    columns = ["id", "created_at", "updated_at", *clean.keys()]
    params = [record_id, stamp, stamp, *clean.values()]
    with get_db_conn() as conn:
        conn.execute(
            f"INSERT INTO {table} ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' for _ in columns)})",
            params,
        )
    return get_row(schema_id, record_id) or {}


def update_row(
    schema_id: str, record_id: str, values: dict[str, Any]
) -> dict[str, Any] | None:
    schema = require_schema(schema_id)
    table = table_name(schema_id)
    clean = _validated_values(schema, values)
    if not clean:
        return get_row(schema_id, record_id)
    assignments = ", ".join(f"{k} = ?" for k in clean)
    with get_db_conn() as conn:
        cur = conn.execute(
            f"UPDATE {table} SET {assignments}, updated_at = ? WHERE id = ?",
            [*clean.values(), _now(), record_id],
        )
        if cur.rowcount == 0:
            return None
    return get_row(schema_id, record_id)


def delete_row(schema_id: str, record_id: str) -> bool:
    table = table_name(require_schema(schema_id).id)
    with get_db_conn() as conn:
        cur = conn.execute(f"DELETE FROM {table} WHERE id = ?", (record_id,))
        return cur.rowcount > 0


def count_rows(schema_id: str) -> int:
    table = table_name(require_schema(schema_id).id)
    with get_db_conn() as conn:
        return int(conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])

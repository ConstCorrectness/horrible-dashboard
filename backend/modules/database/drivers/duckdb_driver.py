"""DuckDB driver — local analytical/columnar querying over a ``.duckdb`` file (or
an in-memory database when no path is given)."""

from __future__ import annotations

import time
from typing import Any

from backend.modules.database.drivers.base import (
    ColumnInfo,
    ColumnSchema,
    DatabaseSchema,
    DriverError,
    QueryResult,
    TableSchema,
    jsonable,
)

provider = "duckdb"
dialect = "sql"


def _import_duckdb():  # type: ignore[no-untyped-def]
    try:
        import duckdb  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - depends on optional install
        raise DriverError(
            "DuckDB support needs the 'duckdb' package (uv add duckdb)."
        ) from exc
    return duckdb


def _connect(config: dict[str, Any], *, read_only: bool = False):  # type: ignore[no-untyped-def]
    duckdb = _import_duckdb()
    path = (config.get("path") or "").strip() or ":memory:"
    try:
        return duckdb.connect(database=path, read_only=read_only and path != ":memory:")
    except duckdb.Error as exc:
        raise DriverError(str(exc)) from exc


def test(config: dict[str, Any]) -> None:
    duckdb = _import_duckdb()
    conn = _connect(config)
    try:
        conn.execute("SELECT 1")
    except duckdb.Error as exc:
        raise DriverError(str(exc)) from exc
    finally:
        conn.close()


def run_query(
    config: dict[str, Any],
    sql: str,
    params: list[Any] | None = None,
    *,
    read_only: bool = False,
    row_limit: int = 1000,
) -> QueryResult:
    duckdb = _import_duckdb()
    started = time.perf_counter()
    conn = _connect(config, read_only=read_only)
    try:
        cur = conn.execute(sql, params or None)
        if cur.description is None:
            elapsed = (time.perf_counter() - started) * 1000
            return QueryResult(
                columns=[], rows=[], rowcount=0, elapsed_ms=elapsed, message="OK"
            )
        columns = [ColumnInfo(name=d[0], type=str(d[1])) for d in cur.description]
        fetched = cur.fetchmany(row_limit + 1)
        truncated = len(fetched) > row_limit
        rows = [[jsonable(v) for v in row] for row in fetched[:row_limit]]
        elapsed = (time.perf_counter() - started) * 1000
        return QueryResult(
            columns=columns,
            rows=rows,
            rowcount=len(rows),
            elapsed_ms=elapsed,
            truncated=truncated,
        )
    except duckdb.Error as exc:
        raise DriverError(str(exc)) from exc
    finally:
        conn.close()


def introspect(config: dict[str, Any]) -> DatabaseSchema:
    duckdb = _import_duckdb()
    conn = _connect(config)
    try:
        rows = conn.execute(
            """
            SELECT table_name, column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
            ORDER BY table_name, ordinal_position
            """
        ).fetchall()
    except duckdb.Error as exc:
        raise DriverError(str(exc)) from exc
    finally:
        conn.close()

    tables: dict[str, TableSchema] = {}
    for table, col, dtype, nullable in rows:
        ts = tables.get(table)
        if ts is None:
            ts = TableSchema(name=table)
            tables[table] = ts
        ts.columns.append(
            ColumnSchema(name=col, type=dtype, nullable=(str(nullable) == "YES"))
        )
    return DatabaseSchema(tables=list(tables.values()))

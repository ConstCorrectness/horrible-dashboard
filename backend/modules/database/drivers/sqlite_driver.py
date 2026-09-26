"""SQLite driver — backs the built-in ``app`` connection (the local vector store)
and any user-added ``.sqlite``/``.db`` file. Uses the stdlib, no extra dependency."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
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


provider = "sqlite"
dialect = "sql"


def _connect(config: dict[str, Any], *, read_only: bool = False) -> sqlite3.Connection:
    path = str(config.get("path") or "").strip()
    if not path:
        raise DriverError("sqlite connection requires a 'path'")
    builtin = config.get("builtin") is True
    if not Path(path).exists() and not builtin:
        raise DriverError(f"sqlite file not found: {path}")
    if builtin:
        # The built-in DB is allowed not to exist yet — SQLite will create the file on
        # connect, but only if its directory is already there.
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = sqlite3.connect(path)
    except sqlite3.OperationalError as exc:
        # sqlite3 reports "unable to open database file" for a directory, a bad parent,
        # and a permissions problem alike; say which path failed at least.
        raise DriverError(f"cannot open sqlite database at {path}: {exc}") from exc
    # Make the app DB's similarity function available so users can run the same
    # semantic-search SQL the module uses internally. (Note: Removed since vectorstore is LanceDB)
    if read_only:
        conn.execute("PRAGMA query_only = ON")
    return conn


def test(config: dict[str, Any]) -> None:
    try:
        conn = _connect(config)
        try:
            conn.execute("SELECT 1")
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise DriverError(str(exc)) from exc


def run_query(
    config: dict[str, Any],
    sql: str,
    params: list[Any] | None = None,
    *,
    read_only: bool = False,
    row_limit: int = 1000,
) -> QueryResult:
    started = time.perf_counter()
    conn = _connect(config, read_only=read_only)
    try:
        cur = conn.execute(sql, tuple(params or []))
        if cur.description is None:
            conn.commit()
            elapsed = (time.perf_counter() - started) * 1000
            return QueryResult(
                columns=[],
                rows=[],
                rowcount=0,
                elapsed_ms=elapsed,
                affected=cur.rowcount if cur.rowcount != -1 else 0,
                message="OK",
            )
        columns = [ColumnInfo(name=d[0]) for d in cur.description]
        fetched = cur.fetchmany(row_limit + 1)
        truncated = len(fetched) > row_limit
        rows = [[jsonable(v) for v in row] for row in fetched[:row_limit]]
        conn.commit()
        elapsed = (time.perf_counter() - started) * 1000
        return QueryResult(
            columns=columns,
            rows=rows,
            rowcount=len(rows),
            elapsed_ms=elapsed,
            truncated=truncated,
        )
    except sqlite3.Error as exc:
        raise DriverError(str(exc)) from exc
    finally:
        conn.close()


def introspect(config: dict[str, Any]) -> DatabaseSchema:
    conn = _connect(config)
    try:
        try:
            # Try to use pragma_table_info as a table-valued function (SQLite 3.16.0+)
            # This allows a single query to get all columns for all tables, avoiding N+1 queries.
            rows = conn.execute(
                """
                SELECT m.name as table_name, p.name as column_name, p.type, p."notnull", p.pk
                FROM sqlite_master m
                JOIN pragma_table_info(m.name) p
                WHERE m.type IN ('table', 'view') AND m.name NOT LIKE 'sqlite_%'
                ORDER BY m.name, p.cid
                """
            ).fetchall()

            table_map = {}
            # Pre-fill all tables to maintain order and include empty ones if any
            names = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table','view') "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                ).fetchall()
            ]
            for name in names:
                table_map[name] = TableSchema(name=name, columns=[])

            for table_name, col_name, col_type, notnull, pk in rows:
                if table_name in table_map:
                    table_map[table_name].columns.append(
                        ColumnSchema(
                            name=col_name,
                            type=col_type or "",
                            nullable=not notnull,
                            primary_key=bool(pk),
                        )
                    )

            return DatabaseSchema(tables=list(table_map.values()))
        except sqlite3.OperationalError:
            # Fallback for older SQLite versions
            names = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table','view') "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                ).fetchall()
            ]
            tables: list[TableSchema] = []
            for name in names:
                cols = [
                    ColumnSchema(
                        name=row[1],
                        type=row[2] or "",
                        nullable=not row[3],
                        primary_key=bool(row[5]),
                    )
                    for row in conn.execute(f'PRAGMA table_info("{name}")').fetchall()
                ]
                tables.append(TableSchema(name=name, columns=cols))
            return DatabaseSchema(tables=tables)
    except sqlite3.Error as exc:
        raise DriverError(str(exc)) from exc
    finally:
        conn.close()

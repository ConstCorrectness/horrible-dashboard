"""MySQL / MariaDB driver (PyMySQL, pure-Python)."""

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

provider = "mysql"
dialect = "sql"


def _import_pymysql():  # type: ignore[no-untyped-def]
    try:
        import pymysql  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - depends on optional install
        raise DriverError(
            "MySQL support needs the 'pymysql' package (uv add pymysql)."
        ) from exc
    return pymysql


def _connect(config: dict[str, Any]):  # type: ignore[no-untyped-def]
    pymysql = _import_pymysql()
    try:
        return pymysql.connect(
            host=config.get("host") or "localhost",
            port=int(config.get("port") or 3306),
            user=config.get("user") or "root",
            password=config.get("password") or "",
            database=config.get("database") or config.get("dbname") or None,
            connect_timeout=5,
        )
    except pymysql.Error as exc:
        raise DriverError(str(exc)) from exc


def test(config: dict[str, Any]) -> None:
    pymysql = _import_pymysql()
    conn = _connect(config)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
    except pymysql.Error as exc:
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
    pymysql = _import_pymysql()
    started = time.perf_counter()
    conn = _connect(config)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or None)
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
    except pymysql.Error as exc:
        raise DriverError(str(exc)) from exc
    finally:
        conn.close()


def introspect(config: dict[str, Any]) -> DatabaseSchema:
    pymysql = _import_pymysql()
    database = config.get("database") or config.get("dbname")
    conn = _connect(config)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name, column_name, data_type, is_nullable, column_key
                FROM information_schema.columns
                WHERE table_schema = COALESCE(%s, DATABASE())
                ORDER BY table_name, ordinal_position
                """,
                (database,),
            )
            rows = cur.fetchall()
    except pymysql.Error as exc:
        raise DriverError(str(exc)) from exc
    finally:
        conn.close()

    tables: dict[str, TableSchema] = {}
    for table, col, dtype, nullable, key in rows:
        ts = tables.get(table)
        if ts is None:
            ts = TableSchema(name=table)
            tables[table] = ts
        ts.columns.append(
            ColumnSchema(
                name=col,
                type=dtype,
                nullable=(str(nullable) == "YES"),
                primary_key=(key == "PRI"),
            )
        )
    return DatabaseSchema(tables=list(tables.values()))

"""PostgreSQL driver (psycopg 3). pgvector ``vector`` columns and ``bytea`` blobs are
summarized by the shared ``jsonable`` coercion so they don't flood the results grid."""

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

provider = "postgres"
dialect = "sql"


def _import_psycopg():  # type: ignore[no-untyped-def]
    try:
        import psycopg  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - depends on optional install
        raise DriverError(
            "PostgreSQL support needs the 'psycopg' package (uv add psycopg[binary])."
        ) from exc
    return psycopg


def _conninfo(config: dict[str, Any]) -> str:
    dsn = (config.get("dsn") or "").strip()
    if dsn:
        return dsn
    psycopg = _import_psycopg()
    return psycopg.conninfo.make_conninfo(
        host=config.get("host") or "localhost",
        port=int(config.get("port") or 5432),
        dbname=config.get("database") or config.get("dbname") or "postgres",
        user=config.get("user") or None,
        password=config.get("password") or None,
        sslmode=config.get("sslmode") or None,
    )


def test(config: dict[str, Any]) -> None:
    psycopg = _import_psycopg()
    try:
        with psycopg.connect(_conninfo(config), connect_timeout=5) as conn:
            conn.execute("SELECT 1")
    except psycopg.Error as exc:
        raise DriverError(str(exc)) from exc


def run_query(
    config: dict[str, Any],
    sql: str,
    params: list[Any] | None = None,
    *,
    read_only: bool = False,
    row_limit: int = 1000,
) -> QueryResult:
    psycopg = _import_psycopg()
    started = time.perf_counter()
    try:
        with psycopg.connect(_conninfo(config), connect_timeout=5) as conn:
            with conn.cursor() as cur:
                if read_only:
                    cur.execute("SET TRANSACTION READ ONLY")
                cur.execute(sql, params or None)
                if cur.description is None:
                    elapsed = (time.perf_counter() - started) * 1000
                    return QueryResult(
                        columns=[],
                        rows=[],
                        rowcount=0,
                        elapsed_ms=elapsed,
                        affected=cur.rowcount if cur.rowcount != -1 else 0,
                        message=cur.statusmessage or "OK",
                    )
                columns = [ColumnInfo(name=d.name) for d in cur.description]
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
    except psycopg.Error as exc:
        raise DriverError(str(exc)) from exc


def introspect(config: dict[str, Any]) -> DatabaseSchema:
    psycopg = _import_psycopg()
    query = """
        SELECT c.table_schema, c.table_name, c.column_name, c.data_type,
               c.is_nullable,
               COALESCE(pk.is_pk, false) AS is_pk
        FROM information_schema.columns c
        LEFT JOIN (
            SELECT kcu.table_schema, kcu.table_name, kcu.column_name, true AS is_pk
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            WHERE tc.constraint_type = 'PRIMARY KEY'
        ) pk ON pk.table_schema = c.table_schema
            AND pk.table_name = c.table_name
            AND pk.column_name = c.column_name
        WHERE c.table_schema NOT IN ('pg_catalog', 'information_schema')
        ORDER BY c.table_schema, c.table_name, c.ordinal_position
    """
    try:
        with psycopg.connect(_conninfo(config), connect_timeout=5) as conn:
            rows = conn.execute(query).fetchall()
    except psycopg.Error as exc:
        raise DriverError(str(exc)) from exc

    tables: dict[tuple[str, str], TableSchema] = {}
    for schema, table, col, dtype, nullable, is_pk in rows:
        key = (schema, table)
        ts = tables.get(key)
        if ts is None:
            ts = TableSchema(name=table, schema=schema)
            tables[key] = ts
        ts.columns.append(
            ColumnSchema(
                name=col,
                type=dtype,
                nullable=(nullable == "YES"),
                primary_key=bool(is_pk),
            )
        )
    return DatabaseSchema(tables=list(tables.values()))

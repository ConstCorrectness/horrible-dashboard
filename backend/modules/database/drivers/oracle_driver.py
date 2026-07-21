"""Oracle Database driver (python-oracledb, **thin mode**).

Thin mode talks the wire protocol directly, so there's no Oracle Instant Client to
install — the driver is a pure-Python wheel and works the same on every OS the app
runs on. That's why no thick-mode init is attempted here.

Oracle 23ai's AI Vector Search is plain SQL (a ``VECTOR`` column type and
``VECTOR_DISTANCE()``), so it needs no special casing: it's a ``sql``-dialect driver
like postgres. Only the *values* need care — a ``VECTOR`` comes back as an
``array.array``, which the shared ``jsonable`` coercion summarizes rather than
dumping a few thousand floats into the grid.

Binds are Oracle-style (``:1``, ``:2``), not ``%s`` or ``?``.
"""

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

provider = "oracle"
dialect = "sql"

# Oracle's own metadata schemas. Listing them in the schema tree buries the user's
# tables under a few thousand dictionary objects.
_SYSTEM_SCHEMAS = (
    "SYS",
    "SYSTEM",
    "XDB",
    "MDSYS",
    "CTXSYS",
    "DBSNMP",
    "OUTLN",
    "ORDSYS",
    "ORDDATA",
    "OLAPSYS",
    "WMSYS",
    "LBACSYS",
    "AUDSYS",
    "GSMADMIN_INTERNAL",
    "APPQOSSYS",
    "DVSYS",
    "OJVMSYS",
    "RDSADMIN",
)


def _import_oracledb():  # type: ignore[no-untyped-def]
    try:
        import oracledb  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - depends on optional install
        raise DriverError(
            "Oracle support needs the 'oracledb' package (uv sync --extra oracle)."
        ) from exc
    return oracledb


def _dsn(config: dict[str, Any]) -> str:
    """Build an Easy Connect DSN, or pass through an explicit one.

    An explicit ``dsn`` covers the cases Easy Connect can't express — a tnsnames
    alias, a wallet-backed cloud alias, or a full DESCRIPTION string.
    """
    dsn = str(config.get("dsn") or "").strip()
    if dsn:
        return dsn
    host = str(config.get("host") or "localhost").strip()
    port = int(config.get("port") or 1521)
    service = str(
        config.get("service_name") or config.get("database") or "FREEPDB1"
    ).strip()
    return f"{host}:{port}/{service}"


def _connect(config: dict[str, Any]):  # type: ignore[no-untyped-def]
    oracledb = _import_oracledb()
    kwargs: dict[str, Any] = {
        "user": config.get("user") or None,
        "password": config.get("password") or None,
        "dsn": _dsn(config),
    }
    # A wallet directory (Autonomous Database) is supported in thin mode via
    # config_dir + wallet_location; both are optional and only passed when set.
    wallet = str(config.get("wallet_location") or "").strip()
    if wallet:
        kwargs["config_dir"] = wallet
        kwargs["wallet_location"] = wallet
        if config.get("wallet_password"):
            kwargs["wallet_password"] = config["wallet_password"]
    try:
        return oracledb.connect(**kwargs)
    except Exception as exc:  # oracledb.Error and DPY-* config errors alike
        raise DriverError(str(exc)) from exc


def test(config: dict[str, Any]) -> None:
    conn = _connect(config)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM dual")
            cur.fetchone()
    except Exception as exc:
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
    started = time.perf_counter()
    conn = _connect(config)
    try:
        with conn.cursor() as cur:
            if read_only:
                cur.execute("SET TRANSACTION READ ONLY")
            # Oracle rejects a trailing semicolon on a single statement through the
            # driver (it's a SQL*Plus terminator, not part of the statement).
            cur.execute(sql.strip().rstrip(";"), params or [])
            if cur.description is None:
                affected = cur.rowcount or 0
                conn.commit()
                elapsed = (time.perf_counter() - started) * 1000
                return QueryResult(
                    columns=[],
                    rows=[],
                    rowcount=0,
                    elapsed_ms=elapsed,
                    affected=affected,
                    message="OK",
                )
            columns = [
                ColumnInfo(name=d[0], type=getattr(d[1], "name", None))
                for d in cur.description
            ]
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
    except Exception as exc:
        raise DriverError(str(exc)) from exc
    finally:
        conn.close()


def introspect(config: dict[str, Any]) -> DatabaseSchema:
    """Columns for every table the connected user can see.

    Scoped to ``ALL_TAB_COLUMNS`` minus the dictionary schemas, or to a single owner
    when the connection sets ``schema``. Oracle folds unquoted identifiers to upper
    case, so the owner filter is upper-cased to match.
    """
    owner = str(config.get("schema") or "").strip().upper()
    binds: list[Any] = []
    if owner:
        owner_clause = "c.OWNER = :1"
        binds.append(owner)
    else:
        placeholders = ", ".join(f"'{s}'" for s in _SYSTEM_SCHEMAS)
        owner_clause = f"c.OWNER NOT IN ({placeholders})"

    query = f"""
        SELECT c.OWNER, c.TABLE_NAME, c.COLUMN_NAME, c.DATA_TYPE, c.NULLABLE,
               CASE WHEN pk.COLUMN_NAME IS NULL THEN 0 ELSE 1 END AS IS_PK
        FROM ALL_TAB_COLUMNS c
        LEFT JOIN (
            SELECT cc.OWNER, cc.TABLE_NAME, cc.COLUMN_NAME
            FROM ALL_CONSTRAINTS ct
            JOIN ALL_CONS_COLUMNS cc
              ON ct.CONSTRAINT_NAME = cc.CONSTRAINT_NAME
             AND ct.OWNER = cc.OWNER
            WHERE ct.CONSTRAINT_TYPE = 'P'
        ) pk ON pk.OWNER = c.OWNER
            AND pk.TABLE_NAME = c.TABLE_NAME
            AND pk.COLUMN_NAME = c.COLUMN_NAME
        WHERE {owner_clause}
        ORDER BY c.OWNER, c.TABLE_NAME, c.COLUMN_ID
    """

    conn = _connect(config)
    try:
        with conn.cursor() as cur:
            cur.execute(query, binds)
            rows = cur.fetchall()
    except Exception as exc:
        raise DriverError(str(exc)) from exc
    finally:
        conn.close()

    tables: dict[tuple[str, str], TableSchema] = {}
    for schema_name, table, col, dtype, nullable, is_pk in rows:
        key = (schema_name, table)
        ts = tables.get(key)
        if ts is None:
            ts = TableSchema(name=table, schema=schema_name)
            tables[key] = ts
        ts.columns.append(
            ColumnSchema(
                name=col,
                type=dtype,
                nullable=(nullable == "Y"),
                primary_key=bool(is_pk),
            )
        )
    return DatabaseSchema(tables=list(tables.values()))

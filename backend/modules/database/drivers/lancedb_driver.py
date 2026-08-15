"""LanceDB driver — the node's own vector store, and any other Lance directory.

This is the driver that closes the long-standing gap called out in ``connections.py``:
LanceDB is a *directory* of Lance datasets, not a SQL file, so the SQL console could
never open it. As a ``json``-dialect provider it's queryable natively instead, and a
built-in ``vectors`` connection points at ``$HORRIBLE_DATA_DIR/lancedb`` — the same
store ``vectorstore.py`` writes (library chunks, symdex, CLIP siblings).

Notes on this store's shape:

* ``search()`` with no argument is a plain scan, so ``list`` needs no vector.
* There is no server-side offset. ``offset`` is honoured by over-fetching and
  slicing, which is fine at console row limits but is *not* a cheap deep paginator.
* Sibling tables (``<collection>__clip``) are internal plumbing everywhere else in
  the app, but the console lists them: an inspector's job is to show what's on disk,
  and a CLIP table you can't see is a CLIP table you can't debug.
* ``metadata`` is stored as a JSON *string*; it's parsed back out so its keys become
  real columns rather than one unreadable blob cell.
"""

from __future__ import annotations

import json
import time
from typing import Any

from backend.modules.database.drivers.base import (
    ColumnSchema,
    DatabaseSchema,
    DriverError,
    QueryResult,
    TableSchema,
)
from backend import paths
from backend.modules.database.drivers.vector_base import (
    affected_result,
    flatten_record,
    parse_query,
    records_to_result,
    resolve_vector,
    similarity_from_distance,
    unsupported,
)

provider = "lancedb"
dialect = "json"

# Result columns Lance adds that aren't user data.
_DISTANCE_KEY = "_distance"

# Metadata filters are applied in Python (see _split_where), so the driver over-fetches
# a candidate window first. These bound that window: enough slack that a selective
# filter still fills a page, capped so a console query can't degrade into a full scan.
_POST_FILTER_OVERFETCH = 10
_POST_FILTER_MAX = 2000

# LanceDB's own default is L2, but every table this app writes is searched with
# cosine (vectorstore.py sets .metric("cosine")), so querying the node's store with
# the library default would rank against a different metric than it was built for.
# Overridable per query via {"metric": "l2" | "cosine" | "dot"} for foreign stores.
DEFAULT_METRIC = "cosine"


def _import_lancedb():  # type: ignore[no-untyped-def]
    try:
        import lancedb  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - lancedb is a core dep
        raise DriverError("LanceDB support needs the 'lancedb' package.") from exc
    return lancedb


def default_path() -> str:
    """The node's own vector store directory."""
    return str(paths.data_dir() / "lancedb")


def _connect(config: dict[str, Any]):  # type: ignore[no-untyped-def]
    lancedb = _import_lancedb()
    path = str(config.get("path") or "").strip() or default_path()
    try:
        return lancedb.connect(path)
    except Exception as exc:
        raise DriverError(f"lancedb: cannot open {path!r}: {exc}") from exc


def _table_names(db: Any) -> list[str]:
    """List tables across LanceDB versions.

    0.34 returns a ``ListTablesResponse`` object from ``list_tables()`` while older
    builds returned a plain list from the now-deprecated ``table_names()``.
    """
    try:
        result = db.list_tables()
    except AttributeError:  # pragma: no cover - older lancedb
        return list(db.table_names())
    if isinstance(result, list):
        return list(result)
    tables = getattr(result, "tables", None)
    return list(tables) if tables is not None else []


def _open(db: Any, name: str):  # type: ignore[no-untyped-def]
    try:
        return db.open_table(name)
    except Exception as exc:
        raise DriverError(f"lancedb: no table {name!r} ({exc})") from exc


def _split_where(
    tbl: Any, where: dict[str, Any] | None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split a ``where`` dict into pushdown-able and client-side halves.

    Lance can only filter *physical* columns, but this driver hoists the JSON
    ``metadata`` blob into columns for display. Without this split, filtering on a
    column you can plainly see in the grid (``provider``, ``module``, …) fails with
    a raw "No field named provider" from the scanner. So: physical columns are
    pushed down to Lance, metadata keys are filtered in Python after the fetch.
    """
    if not where:
        return {}, {}
    physical = {f.name for f in tbl.schema}
    pushdown = {k: v for k, v in where.items() if k in physical}
    client = {k: v for k, v in where.items() if k not in physical}
    return pushdown, client


def _matches(record: dict[str, Any], filters: dict[str, Any]) -> bool:
    """Client-side equality check against a flattened record."""
    for key, value in filters.items():
        actual = record.get(key, record.get(f"meta.{key}"))
        if isinstance(value, bool) or value is None:
            if actual is not value:
                return False
        elif isinstance(value, (int, float)) and isinstance(actual, (int, float)):
            if float(actual) != float(value):
                return False
        elif str(actual) != str(value):
            return False
    return True


def _where_clause(where: dict[str, Any] | None) -> str | None:
    """Translate the pushdown half of a ``where`` dict into Lance's filter string.

    Only equality is expressible this way; anything else should be written by the
    user as a raw string, which we pass straight through.
    """
    if not where:
        return None
    parts: list[str] = []
    for key, value in where.items():
        if value is None:
            parts.append(f"{key} IS NULL")
        elif isinstance(value, bool):
            parts.append(f"{key} = {str(value).lower()}")
        elif isinstance(value, (int, float)):
            parts.append(f"{key} = {value}")
        else:
            escaped = str(value).replace("'", "''")
            parts.append(f"{key} = '{escaped}'")
    return " AND ".join(parts)


def _row_to_record(
    row: dict[str, Any], *, drop_vector: bool, metric: str = DEFAULT_METRIC
) -> dict[str, Any]:
    """Turn a raw Lance row into a flat grid record."""
    row = dict(row)
    distance = row.pop(_DISTANCE_KEY, None)
    raw_meta = row.pop("metadata", None)
    metadata: dict[str, Any] = {}
    if isinstance(raw_meta, dict):
        metadata = raw_meta
    elif isinstance(raw_meta, str) and raw_meta.strip():
        try:
            parsed = json.loads(raw_meta)
            metadata = parsed if isinstance(parsed, dict) else {"metadata": raw_meta}
        except ValueError:
            metadata = {"metadata": raw_meta}

    doc_id = row.pop("id", None)
    text = row.pop("text", None)
    if drop_vector:
        row.pop("vector", None)

    extra: dict[str, Any] = dict(row)
    if distance is not None:
        extra["distance"] = round(float(distance), 6)
    return flatten_record(
        doc_id=doc_id,
        text=text,
        metadata=metadata,
        score=similarity_from_distance(distance, metric),
        extra=extra,
    )


def test(config: dict[str, Any]) -> None:
    db = _connect(config)
    try:
        _table_names(db)
    except Exception as exc:
        raise DriverError(str(exc)) from exc


def run_query(
    config: dict[str, Any],
    sql: str,
    params: list[Any] | None = None,
    *,
    read_only: bool = False,
    row_limit: int = 1000,
) -> QueryResult:
    q = parse_query(sql, provider=provider)
    if read_only and not q.is_read_only:
        raise DriverError(f"lancedb: op '{q.op}' writes; uncheck read-only to run it.")
    started = time.perf_counter()
    db = _connect(config)
    limit = min(q.limit, row_limit)

    if q.op == "collections":
        records = []
        for name in _table_names(db):
            tbl = _open(db, name)
            records.append({"collection": name, "rows": tbl.count_rows()})
        return records_to_result(records, started=started, row_limit=row_limit)

    name = q.require_collection(provider)
    tbl = _open(db, name)

    if q.op == "count":
        return records_to_result(
            [{"collection": name, "count": tbl.count_rows()}],
            started=started,
            row_limit=row_limit,
        )

    if q.op == "describe":
        records = [{"column": f.name, "type": str(f.type)} for f in tbl.schema]
        return records_to_result(records, started=started, row_limit=row_limit)

    if q.op in {"search", "list", "peek", "get"}:
        pushdown, client_filters = _split_where(tbl, q.where)
        clause = _where_clause(pushdown)
        if q.op == "get":
            if not q.ids:
                raise DriverError('lancedb: op "get" needs "ids".')
            quoted = ", ".join("'" + str(i).replace("'", "''") + "'" for i in q.ids)
            id_clause = f"id IN ({quoted})"
            clause = f"({clause}) AND {id_clause}" if clause else id_clause

        # A metadata filter can only be applied after the JSON blob is parsed, so
        # over-fetch a bounded candidate window and narrow it here. Bounded, not
        # unlimited: a console query must not turn into a full-table scan.
        want = limit + q.offset
        fetch = (
            min(want * _POST_FILTER_OVERFETCH, _POST_FILTER_MAX)
            if client_filters
            else want
        )

        metric = (q.metric or DEFAULT_METRIC).lower()
        try:
            if q.op == "search":
                builder = tbl.search(resolve_vector(q, provider=provider)).metric(
                    metric
                )
            else:
                builder = tbl.search()
            builder = builder.limit(fetch)
            if clause:
                builder = builder.where(clause)
            rows = builder.to_list()
        except Exception as exc:
            raise DriverError(f"lancedb: {q.op} failed: {exc}") from exc

        records = [_row_to_record(r, drop_vector=True, metric=metric) for r in rows]
        message = None
        if client_filters:
            scanned = len(records)
            records = [r for r in records if _matches(r, client_filters)]
            keys = ", ".join(sorted(client_filters))
            message = (
                f"filtered {keys} client-side over a {scanned}-row window "
                "(metadata keys aren't physical columns, so Lance can't push them down)"
            )
        records = records[q.offset :]
        return records_to_result(
            records,
            started=started,
            row_limit=row_limit,
            select=q.select,
            message=message,
        )

    if q.op == "delete":
        if not q.ids:
            raise DriverError('lancedb: op "delete" needs "ids".')
        quoted = ", ".join("'" + str(i).replace("'", "''") + "'" for i in q.ids)
        try:
            tbl.delete(f"id IN ({quoted})")
        except Exception as exc:
            raise DriverError(f"lancedb: delete failed: {exc}") from exc
        return affected_result(len(q.ids), started=started, verb="DELETE")

    if q.op == "drop_collection":
        try:
            db.drop_table(name)
        except Exception as exc:
            raise DriverError(f"lancedb: drop failed: {exc}") from exc
        return affected_result(1, started=started, verb="DROP")

    # upsert / create_collection need a vector width and an embedding pipeline that
    # belongs to vectorstore.py, not to an inspector console.
    raise unsupported(
        provider,
        q.op,
        "Writes that create vectors go through the library/vectorstore APIs, "
        "which own the embedder and the collection's fixed vector width.",
    )


def introspect(config: dict[str, Any]) -> DatabaseSchema:
    """Collections-as-tables, so the console sidebar works for vector stores too."""
    db = _connect(config)
    tables: list[TableSchema] = []
    for name in _table_names(db):
        try:
            tbl = _open(db, name)
            columns = [
                ColumnSchema(name=f.name, type=str(f.type), nullable=f.nullable)
                for f in tbl.schema
            ]
        except DriverError:
            columns = []
        tables.append(TableSchema(name=name, columns=columns))
    return DatabaseSchema(tables=tables)


__all__ = ["provider", "dialect", "test", "run_query", "introspect", "default_path"]

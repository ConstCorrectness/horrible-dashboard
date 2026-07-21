"""Weaviate driver (client v4) — local instance or Weaviate Cloud.

Weaviate is the least SQL-shaped of the four, and two things shape this driver:

* **Connections must be closed.** The v4 client holds a gRPC channel; leaking one per
  console query would exhaust connections in a long session. Every entry point wraps
  its work in try/finally.
* **Collection names are capitalized classes.** Weaviate title-cases collection names
  on create, so ``library`` and ``Library`` are the same collection. Lookups retry
  with the capitalized form rather than reporting a missing collection that's plainly
  in the sidebar.

``near_text`` is preferred over ``near_vector`` when the collection has a vectorizer
module, for the same reason as Chroma: the collection's own model owns its space.
"""

from __future__ import annotations

import time
from typing import Any

from backend.modules.database.drivers.base import (
    ColumnSchema,
    DatabaseSchema,
    DriverError,
    QueryResult,
    TableSchema,
)
from backend.modules.database.drivers.vector_base import (
    affected_result,
    flatten_record,
    parse_query,
    records_to_result,
    resolve_vector,
    unsupported,
)

provider = "weaviate"
dialect = "json"


def _import_weaviate():  # type: ignore[no-untyped-def]
    try:
        import weaviate  # noqa: PLC0415
        from weaviate.classes.query import Filter, MetadataQuery  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - depends on optional install
        raise DriverError(
            "Weaviate support needs the 'weaviate-client' package "
            "(uv sync --extra weaviate)."
        ) from exc
    return weaviate, Filter, MetadataQuery


def _connect(config: dict[str, Any]):  # type: ignore[no-untyped-def]
    weaviate, _, _ = _import_weaviate()
    url = str(config.get("url") or "").strip()
    api_key = str(config.get("api_key") or "").strip()
    host = str(config.get("host") or "").strip()
    try:
        if url:
            from weaviate.classes.init import Auth  # noqa: PLC0415

            return weaviate.connect_to_weaviate_cloud(
                cluster_url=url,
                auth_credentials=Auth.api_key(api_key) if api_key else None,
            )
        return weaviate.connect_to_local(
            host=host or "localhost",
            port=int(config.get("port") or 8080),
            grpc_port=int(config.get("grpc_port") or 50051),
        )
    except Exception as exc:
        raise DriverError(f"weaviate: cannot connect: {exc}") from exc


def _close(client: Any) -> None:
    try:
        client.close()
    except Exception:  # noqa: BLE001 — closing must never mask the real error
        pass


def _get_collection(client: Any, name: str):  # type: ignore[no-untyped-def]
    """Fetch a collection, tolerating Weaviate's title-casing of class names."""
    try:
        if client.collections.exists(name):
            return client.collections.get(name)
        capitalized = name[:1].upper() + name[1:]
        if capitalized != name and client.collections.exists(capitalized):
            return client.collections.get(capitalized)
    except Exception as exc:
        raise DriverError(f"weaviate: cannot open {name!r}: {exc}") from exc
    raise DriverError(f"weaviate: no collection {name!r}.")


def _build_filter(Filter: Any, where: dict[str, Any] | None):  # type: ignore[no-untyped-def]
    if not where:
        return None
    clauses = []
    for key, value in where.items():
        if isinstance(value, (dict, list)):
            raise DriverError(
                f"weaviate: filter on {key!r} must be a scalar — this driver builds "
                "equality conditions only."
            )
        clauses.append(Filter.by_property(key).equal(value))
    if len(clauses) == 1:
        return clauses[0]
    return Filter.all_of(clauses)


def _object_to_record(obj: Any) -> dict[str, Any]:
    props = dict(getattr(obj, "properties", None) or {})
    text = (
        props.pop("text", None)
        or props.pop("content", None)
        or props.pop("chunk", None)
    )
    metadata = getattr(obj, "metadata", None)
    distance = getattr(metadata, "distance", None) if metadata else None
    extra = {}
    if distance is not None:
        extra["distance"] = round(float(distance), 6)
    return flatten_record(
        doc_id=str(getattr(obj, "uuid", "")) or None,
        text=text,
        metadata=props,
        score=(1.0 - float(distance)) if distance is not None else None,
        extra=extra or None,
    )


def test(config: dict[str, Any]) -> None:
    client = _connect(config)
    try:
        if not client.is_ready():
            raise DriverError("weaviate: server is not ready.")
    except DriverError:
        raise
    except Exception as exc:
        raise DriverError(f"weaviate: {exc}") from exc
    finally:
        _close(client)


def run_query(
    config: dict[str, Any],
    sql: str,
    params: list[Any] | None = None,
    *,
    read_only: bool = False,
    row_limit: int = 1000,
) -> QueryResult:
    _, Filter, MetadataQuery = _import_weaviate()
    q = parse_query(sql, provider=provider)
    if read_only and not q.is_read_only:
        raise DriverError(f"weaviate: op '{q.op}' writes; uncheck read-only to run it.")
    started = time.perf_counter()
    client = _connect(config)
    limit = min(q.limit, row_limit)

    try:
        if q.op == "collections":
            names = list(client.collections.list_all().keys())
            records = []
            for n in names:
                try:
                    total = client.collections.get(n).aggregate.over_all(
                        total_count=True
                    )
                    records.append({"collection": n, "count": total.total_count})
                except Exception:  # noqa: BLE001 — a broken collection still lists
                    records.append({"collection": n, "count": None})
            return records_to_result(records, started=started, row_limit=row_limit)

        name = q.require_collection(provider)
        coll = _get_collection(client, name)

        if q.op == "count":
            total = coll.aggregate.over_all(total_count=True)
            return records_to_result(
                [{"collection": name, "count": total.total_count}],
                started=started,
                row_limit=row_limit,
            )

        if q.op == "describe":
            cfg = coll.config.get()
            records = [
                {
                    "property": p.name,
                    "type": str(getattr(p, "data_type", "")),
                }
                for p in (cfg.properties or [])
            ]
            return records_to_result(records, started=started, row_limit=row_limit)

        if q.op == "search":
            filters = _build_filter(Filter, q.where)
            meta = MetadataQuery(distance=True)
            message: str | None = None
            if q.vector is not None:
                response = coll.query.near_vector(
                    near_vector=q.vector,
                    limit=limit,
                    offset=q.offset or None,
                    filters=filters,
                    return_metadata=meta,
                )
            elif q.query:
                # Prefer the collection's own vectorizer (see module docstring).
                try:
                    response = coll.query.near_text(
                        query=q.query,
                        limit=limit,
                        offset=q.offset or None,
                        filters=filters,
                        return_metadata=meta,
                    )
                except Exception:
                    response = coll.query.near_vector(
                        near_vector=resolve_vector(q, provider=provider),
                        limit=limit,
                        offset=q.offset or None,
                        filters=filters,
                        return_metadata=meta,
                    )
                    message = (
                        "embedded with this node's embedder — the collection has no "
                        "usable vectorizer; results are only meaningful if it was "
                        "built with the same model"
                    )
            else:
                raise DriverError('weaviate: a search needs "query" or "vector".')
            records = [_object_to_record(o) for o in response.objects]
            return records_to_result(
                records,
                started=started,
                row_limit=row_limit,
                select=q.select,
                message=message,
            )

        if q.op in {"list", "peek", "get"}:
            if q.op == "get":
                if not q.ids:
                    raise DriverError('weaviate: op "get" needs "ids".')
                records = []
                for doc_id in q.ids:
                    obj = coll.query.fetch_object_by_id(doc_id)
                    if obj is not None:
                        records.append(_object_to_record(obj))
            else:
                response = coll.query.fetch_objects(
                    limit=limit,
                    offset=q.offset or None,
                    filters=_build_filter(Filter, q.where),
                )
                records = [_object_to_record(o) for o in response.objects]
            return records_to_result(
                records, started=started, row_limit=row_limit, select=q.select
            )

        if q.op == "upsert":
            if not q.documents:
                raise DriverError('weaviate: op "upsert" needs "documents".')
            written = 0
            with coll.batch.dynamic() as batch:
                for doc in q.documents:
                    props = dict(doc.get("metadata") or {})
                    if doc.get("text") is not None:
                        props["text"] = doc["text"]
                    batch.add_object(
                        properties=props,
                        uuid=doc.get("id") or None,
                        vector=doc.get("vector"),
                    )
                    written += 1
            return affected_result(written, started=started, verb="UPSERT")

        if q.op == "delete":
            if not q.ids:
                raise DriverError('weaviate: op "delete" needs "ids".')
            for doc_id in q.ids:
                coll.data.delete_by_id(doc_id)
            return affected_result(len(q.ids), started=started, verb="DELETE")

        if q.op == "create_collection":
            client.collections.create(name)
            return affected_result(1, started=started, verb="CREATE")

        if q.op == "drop_collection":
            client.collections.delete(name)
            return affected_result(1, started=started, verb="DROP")

        raise unsupported(provider, q.op)
    except DriverError:
        raise
    except Exception as exc:
        raise DriverError(f"weaviate: {q.op} failed: {exc}") from exc
    finally:
        _close(client)


def introspect(config: dict[str, Any]) -> DatabaseSchema:
    """Collections as tables, using Weaviate's declared property schema.

    Unlike Chroma and Qdrant this is a real schema, not a sample — Weaviate collections
    declare their properties up front.
    """
    client = _connect(config)
    tables: list[TableSchema] = []
    try:
        for name, cfg in client.collections.list_all().items():
            columns = [ColumnSchema(name="id", type="uuid", primary_key=True)]
            for prop in getattr(cfg, "properties", None) or []:
                columns.append(
                    ColumnSchema(
                        name=prop.name, type=str(getattr(prop, "data_type", ""))
                    )
                )
            tables.append(TableSchema(name=name, columns=columns))
    except Exception as exc:
        raise DriverError(f"weaviate: introspect failed: {exc}") from exc
    finally:
        _close(client)
    return DatabaseSchema(tables=tables)

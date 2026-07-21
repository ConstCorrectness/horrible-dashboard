"""Qdrant driver — remote server (URL + API key) or a local embedded directory.

Two Qdrant-specific shapes the shared query body has to be adapted to:

* **Filters are typed.** Qdrant wants a ``Filter(must=[FieldCondition(...)])`` object,
  not a dict, so ``where`` is translated into one. Only equality (``MatchValue``) is
  built here; a caller needing ranges or geo should say so and get a clear error
  rather than a filter that silently matches nothing.
* **Point ids are unsigned ints or UUIDs.** Arbitrary strings are rejected by the
  server, so string ids that look like integers are coerced and anything else is
  passed through for Qdrant to validate and reject with its own message.

Payload keys are hoisted to grid columns, the same as metadata elsewhere.
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

provider = "qdrant"
dialect = "json"


def _import_qdrant():  # type: ignore[no-untyped-def]
    try:
        from qdrant_client import QdrantClient, models  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - depends on optional install
        raise DriverError(
            "Qdrant support needs the 'qdrant-client' package (uv sync --extra qdrant)."
        ) from exc
    return QdrantClient, models


def _client(config: dict[str, Any]):  # type: ignore[no-untyped-def]
    QdrantClient, _ = _import_qdrant()
    url = str(config.get("url") or "").strip()
    path = str(config.get("path") or "").strip()
    host = str(config.get("host") or "").strip()
    api_key = str(config.get("api_key") or "").strip() or None
    try:
        if url:
            return QdrantClient(url=url, api_key=api_key, timeout=15)
        if host:
            return QdrantClient(
                host=host,
                port=int(config.get("port") or 6333),
                api_key=api_key,
                https=bool(config.get("https") or False),
                timeout=15,
            )
        if path:
            # Embedded mode: a local directory, single-process (Qdrant takes a lock).
            return QdrantClient(path=path)
        raise DriverError("qdrant: set 'url', 'host', or 'path' (embedded).")
    except DriverError:
        raise
    except Exception as exc:
        raise DriverError(f"qdrant: cannot connect: {exc}") from exc


def _coerce_id(value: Any) -> Any:
    """Qdrant point ids are uint64 or UUID; coerce numeric strings so ids round-trip."""
    if isinstance(value, int):
        return value
    text = str(value)
    return int(text) if text.isdigit() else text


def _build_filter(models: Any, where: dict[str, Any] | None):  # type: ignore[no-untyped-def]
    if not where:
        return None
    conditions = []
    for key, value in where.items():
        if isinstance(value, (dict, list)):
            raise DriverError(
                f"qdrant: filter on {key!r} must be a scalar — this driver builds "
                "equality (MatchValue) conditions only."
            )
        conditions.append(
            models.FieldCondition(key=key, match=models.MatchValue(value=value))
        )
    return models.Filter(must=conditions)


def _point_to_record(point: Any, *, with_score: bool) -> dict[str, Any]:
    payload = dict(getattr(point, "payload", None) or {})
    # Qdrant has no first-class "document" field; these are the conventional keys
    # clients store page content under.
    text = (
        payload.pop("text", None)
        or payload.pop("document", None)
        or payload.pop("page_content", None)
    )
    score = getattr(point, "score", None) if with_score else None
    return flatten_record(
        doc_id=getattr(point, "id", None),
        text=text,
        metadata=payload,
        score=float(score) if score is not None else None,
    )


def test(config: dict[str, Any]) -> None:
    client = _client(config)
    try:
        client.get_collections()
    except Exception as exc:
        raise DriverError(f"qdrant: {exc}") from exc
    finally:
        _close(client)


def _close(client: Any) -> None:
    try:
        client.close()
    except Exception:  # noqa: BLE001 — closing must never mask the real error
        pass


def run_query(
    config: dict[str, Any],
    sql: str,
    params: list[Any] | None = None,
    *,
    read_only: bool = False,
    row_limit: int = 1000,
) -> QueryResult:
    _, models = _import_qdrant()
    q = parse_query(sql, provider=provider)
    if read_only and not q.is_read_only:
        raise DriverError(f"qdrant: op '{q.op}' writes; uncheck read-only to run it.")
    started = time.perf_counter()
    client = _client(config)
    limit = min(q.limit, row_limit)

    try:
        if q.op == "collections":
            cols = client.get_collections().collections
            records = []
            for c in cols:
                try:
                    count = client.count(collection_name=c.name, exact=True).count
                except Exception:  # noqa: BLE001 — a broken collection still lists
                    count = None
                records.append({"collection": c.name, "count": count})
            return records_to_result(records, started=started, row_limit=row_limit)

        name = q.require_collection(provider)

        if q.op == "count":
            count = client.count(collection_name=name, exact=True).count
            return records_to_result(
                [{"collection": name, "count": count}],
                started=started,
                row_limit=row_limit,
            )

        if q.op == "describe":
            info = client.get_collection(collection_name=name)
            vectors = info.config.params.vectors
            size = getattr(vectors, "size", None)
            distance = getattr(vectors, "distance", None)
            return records_to_result(
                [
                    {
                        "collection": name,
                        "points": info.points_count,
                        "vector_size": size,
                        "distance": str(distance) if distance else None,
                    }
                ],
                started=started,
                row_limit=row_limit,
            )

        if q.op == "search":
            vector = resolve_vector(q, provider=provider)
            response = client.query_points(
                collection_name=name,
                query=vector,
                limit=limit,
                offset=q.offset or None,
                query_filter=_build_filter(models, q.where),
                with_payload=True,
            )
            records = [_point_to_record(p, with_score=True) for p in response.points]
            return records_to_result(
                records, started=started, row_limit=row_limit, select=q.select
            )

        if q.op == "get":
            if not q.ids:
                raise DriverError('qdrant: op "get" needs "ids".')
            points = client.retrieve(
                collection_name=name,
                ids=[_coerce_id(i) for i in q.ids],
                with_payload=True,
            )
            records = [_point_to_record(p, with_score=False) for p in points]
            return records_to_result(
                records, started=started, row_limit=row_limit, select=q.select
            )

        if q.op in {"list", "peek"}:
            points, _next = client.scroll(
                collection_name=name,
                limit=limit,
                offset=q.offset or None,
                scroll_filter=_build_filter(models, q.where),
                with_payload=True,
            )
            records = [_point_to_record(p, with_score=False) for p in points]
            return records_to_result(
                records, started=started, row_limit=row_limit, select=q.select
            )

        if q.op == "upsert":
            if not q.documents:
                raise DriverError('qdrant: op "upsert" needs "documents".')
            points = []
            for i, doc in enumerate(q.documents):
                if doc.get("vector") is None:
                    raise DriverError(
                        f'qdrant: documents[{i}] needs a "vector" — Qdrant does not '
                        "embed text server-side."
                    )
                payload = dict(doc.get("metadata") or {})
                if doc.get("text") is not None:
                    payload["text"] = doc["text"]
                points.append(
                    models.PointStruct(
                        id=_coerce_id(doc.get("id")),
                        vector=doc["vector"],
                        payload=payload,
                    )
                )
            client.upsert(collection_name=name, points=points)
            return affected_result(len(points), started=started, verb="UPSERT")

        if q.op == "delete":
            if not q.ids:
                raise DriverError('qdrant: op "delete" needs "ids".')
            client.delete(
                collection_name=name,
                points_selector=models.PointIdsList(
                    points=[_coerce_id(i) for i in q.ids]
                ),
            )
            return affected_result(len(q.ids), started=started, verb="DELETE")

        if q.op == "drop_collection":
            client.delete_collection(collection_name=name)
            return affected_result(1, started=started, verb="DROP")

        # create_collection needs a vector size and distance metric the body doesn't carry.
        raise unsupported(
            provider,
            q.op,
            "Create collections with Qdrant's own API — it needs a vector size and "
            "distance metric this query body doesn't express.",
        )
    except DriverError:
        raise
    except Exception as exc:
        raise DriverError(f"qdrant: {q.op} failed: {exc}") from exc
    finally:
        _close(client)


def introspect(config: dict[str, Any]) -> DatabaseSchema:
    """Collections as tables; payload keys sampled from one scrolled point."""
    client = _client(config)
    tables: list[TableSchema] = []
    try:
        for c in client.get_collections().collections:
            columns = [ColumnSchema(name="id", type="point_id", primary_key=True)]
            try:
                points, _ = client.scroll(
                    collection_name=c.name, limit=1, with_payload=True
                )
                for key in sorted((points[0].payload or {}) if points else {}):
                    columns.append(ColumnSchema(name=key, type="payload"))
            except Exception:  # noqa: BLE001 — an unreadable collection still lists
                pass
            tables.append(TableSchema(name=c.name, columns=columns))
    except Exception as exc:
        raise DriverError(f"qdrant: introspect failed: {exc}") from exc
    finally:
        _close(client)
    return DatabaseSchema(tables=tables)

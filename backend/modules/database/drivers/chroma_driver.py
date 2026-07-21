"""ChromaDB driver — embedded (persistent directory) or remote (HTTP server).

Chroma's ``where`` syntax is already a metadata-filter dict (``{"kind": "note"}`` or
``{"kind": {"$eq": "note"}}``), so the shared query body's ``where`` passes straight
through with no translation — the one store where the console's filter shape is
native rather than adapted.

Embedding a text query is deliberately delegated to *Chroma* first: a collection's
vectors were produced by that collection's own embedding function, so searching it
with this node's embedder would compare vectors from two different spaces and return
confident nonsense. Only when Chroma can't embed client-side (a server-side or
unavailable embedding function) does this fall back to the node's embedder, and the
result says so.
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
    embed_text,
    flatten_record,
    parse_query,
    records_to_result,
    similarity_from_distance,
    unsupported,
)

provider = "chroma"
dialect = "json"

_INCLUDE_QUERY = ["documents", "metadatas", "distances"]
_INCLUDE_GET = ["documents", "metadatas"]


def _import_chromadb():  # type: ignore[no-untyped-def]
    try:
        import chromadb  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - depends on optional install
        raise DriverError(
            "ChromaDB support needs the 'chromadb' package (uv sync --extra chroma)."
        ) from exc
    return chromadb


def _client(config: dict[str, Any]):  # type: ignore[no-untyped-def]
    """Persistent (local directory) or HTTP (remote server) client.

    ``path`` and ``host`` are mutually exclusive; ``host`` wins when both are set so
    a saved connection that gained a host doesn't silently keep reading a stale dir.
    """
    chromadb = _import_chromadb()
    host = str(config.get("host") or "").strip()
    path = str(config.get("path") or "").strip()
    try:
        if host:
            headers = {}
            token = str(config.get("token") or "").strip()
            if token:
                headers["Authorization"] = f"Bearer {token}"
            return chromadb.HttpClient(
                host=host,
                port=int(config.get("port") or 8000),
                ssl=bool(config.get("ssl") or False),
                headers=headers or None,
            )
        if not path:
            raise DriverError(
                "chroma: set either 'path' (embedded, a directory) or 'host' (server)."
            )
        return chromadb.PersistentClient(path=path)
    except DriverError:
        raise
    except Exception as exc:
        raise DriverError(f"chroma: cannot connect: {exc}") from exc


def _collection_names(client: Any) -> list[str]:
    """List collections across Chroma versions.

    Chroma 0.6 changed ``list_collections()`` from returning Collection objects to
    returning bare names; both shapes are still in the wild.
    """
    try:
        items = client.list_collections()
    except Exception as exc:
        raise DriverError(f"chroma: list_collections failed: {exc}") from exc
    return [
        item if isinstance(item, str) else getattr(item, "name", str(item))
        for item in items
    ]


def _get_collection(client: Any, name: str):  # type: ignore[no-untyped-def]
    try:
        return client.get_collection(name)
    except Exception as exc:
        raise DriverError(f"chroma: no collection {name!r} ({exc})") from exc


def _collection_space(coll: Any) -> str:
    """The distance space a collection was built with (Chroma's default is L2).

    Read from ``configuration_json`` first (authoritative in Chroma 1.x) and fall
    back to the legacy ``hnsw:space`` metadata key. This decides whether a similarity
    score can honestly be derived from the returned distance.
    """
    try:
        config = getattr(coll, "configuration_json", None) or {}
        space = ((config.get("hnsw") or {}) or {}).get("space")
        if space:
            return str(space)
    except Exception:  # noqa: BLE001 — fall through to metadata
        pass
    metadata = getattr(coll, "metadata", None) or {}
    return str(metadata.get("hnsw:space") or "l2")


def _zip_query_result(payload: dict[str, Any], space: str) -> list[dict[str, Any]]:
    """Flatten Chroma's column-of-lists query response into records.

    ``query()`` nests one list per query vector; we always send exactly one, so the
    first bucket is the whole result.
    """

    def bucket(key: str) -> list[Any]:
        raw = payload.get(key) or []
        return list(raw[0]) if raw and isinstance(raw[0], list) else []

    ids = bucket("ids")
    docs = bucket("documents")
    metas = bucket("metadatas")
    dists = bucket("distances")

    records: list[dict[str, Any]] = []
    for i, doc_id in enumerate(ids):
        distance = dists[i] if i < len(dists) else None
        records.append(
            flatten_record(
                doc_id=doc_id,
                text=docs[i] if i < len(docs) else None,
                metadata=metas[i] if i < len(metas) else {},
                score=similarity_from_distance(distance, space),
                extra={"distance": round(float(distance), 6)}
                if distance is not None
                else None,
            )
        )
    return records


def _zip_get_result(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten Chroma's flat-list ``get()`` response into records."""
    ids = list(payload.get("ids") or [])
    docs = list(payload.get("documents") or [])
    metas = list(payload.get("metadatas") or [])
    return [
        flatten_record(
            doc_id=doc_id,
            text=docs[i] if i < len(docs) else None,
            metadata=metas[i] if i < len(metas) else {},
        )
        for i, doc_id in enumerate(ids)
    ]


def test(config: dict[str, Any]) -> None:
    client = _client(config)
    try:
        client.heartbeat()
    except AttributeError:
        _collection_names(client)  # embedded clients have no heartbeat
    except Exception as exc:
        raise DriverError(f"chroma: {exc}") from exc


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
        raise DriverError(f"chroma: op '{q.op}' writes; uncheck read-only to run it.")
    started = time.perf_counter()
    client = _client(config)
    limit = min(q.limit, row_limit)

    if q.op == "collections":
        records = []
        for name in _collection_names(client):
            try:
                records.append(
                    {"collection": name, "count": _get_collection(client, name).count()}
                )
            except DriverError:
                records.append({"collection": name, "count": None})
        return records_to_result(records, started=started, row_limit=row_limit)

    name = q.require_collection(provider)
    coll = _get_collection(client, name)

    if q.op == "count":
        return records_to_result(
            [{"collection": name, "count": coll.count()}],
            started=started,
            row_limit=row_limit,
        )

    if q.op == "describe":
        peek = coll.peek(limit=1)
        meta_keys = (
            sorted((peek.get("metadatas") or [{}])[0] or {}) if peek.get("ids") else []
        )
        records = [
            {"collection": name, "count": coll.count(), "metadata_keys": meta_keys}
        ]
        return records_to_result(records, started=started, row_limit=row_limit)

    if q.op == "search":
        message: str | None = None
        try:
            if q.vector is not None:
                payload = coll.query(
                    query_embeddings=[q.vector],
                    n_results=limit,
                    where=q.where or None,
                    include=_INCLUDE_QUERY,
                )
            elif q.query:
                # Let Chroma embed with the collection's own function (see module docstring).
                try:
                    payload = coll.query(
                        query_texts=[q.query],
                        n_results=limit,
                        where=q.where or None,
                        include=_INCLUDE_QUERY,
                    )
                except Exception:
                    payload = coll.query(
                        query_embeddings=[embed_text(q.query)],
                        n_results=limit,
                        where=q.where or None,
                        include=_INCLUDE_QUERY,
                    )
                    message = (
                        "embedded with this node's embedder — Chroma could not embed "
                        "client-side; results are only meaningful if the collection "
                        "was built with the same model"
                    )
            else:
                raise DriverError('chroma: a search needs "query" or "vector".')
        except DriverError:
            raise
        except Exception as exc:
            raise DriverError(f"chroma: search failed: {exc}") from exc
        return records_to_result(
            _zip_query_result(payload, _collection_space(coll)),
            started=started,
            row_limit=row_limit,
            select=q.select,
            message=message,
        )

    if q.op in {"get", "list", "peek"}:
        try:
            payload = coll.get(
                ids=q.ids or None,
                where=q.where or None,
                limit=limit,
                offset=q.offset or None,
                include=_INCLUDE_GET,
            )
        except Exception as exc:
            raise DriverError(f"chroma: {q.op} failed: {exc}") from exc
        return records_to_result(
            _zip_get_result(payload),
            started=started,
            row_limit=row_limit,
            select=q.select,
        )

    if q.op == "upsert":
        if not q.documents:
            raise DriverError('chroma: op "upsert" needs "documents".')
        ids, docs, metas, vectors = [], [], [], []
        for i, doc in enumerate(q.documents):
            doc_id = doc.get("id")
            if not doc_id:
                raise DriverError(f'chroma: documents[{i}] needs an "id".')
            ids.append(str(doc_id))
            docs.append(doc.get("text") or doc.get("document") or "")
            metas.append(doc.get("metadata") or {})
            if doc.get("vector") is not None:
                vectors.append(doc["vector"])
        if vectors and len(vectors) != len(ids):
            raise DriverError(
                "chroma: supply a vector for every document or for none — a partial "
                "set would leave Chroma embedding only some of them."
            )
        try:
            coll.upsert(
                ids=ids,
                documents=docs,
                metadatas=metas,
                embeddings=vectors or None,
            )
        except Exception as exc:
            raise DriverError(f"chroma: upsert failed: {exc}") from exc
        return affected_result(len(ids), started=started, verb="UPSERT")

    if q.op == "delete":
        if not q.ids and not q.where:
            raise DriverError(
                'chroma: op "delete" needs "ids" or "where" — refusing to delete a '
                "whole collection implicitly."
            )
        try:
            coll.delete(ids=q.ids or None, where=q.where or None)
        except Exception as exc:
            raise DriverError(f"chroma: delete failed: {exc}") from exc
        return affected_result(len(q.ids or []), started=started, verb="DELETE")

    if q.op == "create_collection":
        try:
            client.get_or_create_collection(name)
        except Exception as exc:
            raise DriverError(f"chroma: create failed: {exc}") from exc
        return affected_result(1, started=started, verb="CREATE")

    if q.op == "drop_collection":
        try:
            client.delete_collection(name)
        except Exception as exc:
            raise DriverError(f"chroma: drop failed: {exc}") from exc
        return affected_result(1, started=started, verb="DROP")

    raise unsupported(provider, q.op)


def introspect(config: dict[str, Any]) -> DatabaseSchema:
    """Collections as tables, with metadata keys sampled from one document.

    Chroma collections are schemaless, so "columns" are inferred from a peeked row
    rather than declared — an approximation, and the sidebar is the only consumer.
    """
    client = _client(config)
    tables: list[TableSchema] = []
    for name in _collection_names(client):
        columns = [
            ColumnSchema(name="id", type="str", primary_key=True),
            ColumnSchema(name="text", type="str"),
        ]
        try:
            peek = _get_collection(client, name).peek(limit=1)
            metas = peek.get("metadatas") or []
            for key in sorted((metas[0] if metas else {}) or {}):
                columns.append(ColumnSchema(name=key, type="metadata"))
        except Exception:  # noqa: BLE001 — an unreadable collection still lists
            pass
        tables.append(TableSchema(name=name, columns=columns))
    return DatabaseSchema(tables=tables)

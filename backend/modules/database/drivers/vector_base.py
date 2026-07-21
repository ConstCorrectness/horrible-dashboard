"""Shared plumbing for ``json``-dialect (vector-store) drivers.

Chroma, LanceDB, Qdrant and Weaviate have no SQL dialect: a query is a collection,
an optional metadata filter, and a vector or a piece of text to embed. Rather than
fake a SQL surface over that, ``json``-dialect drivers take a **query body** — the
console's editor sends JSON and the driver maps it onto the store's own API.

The body is uniform across stores so one editor and one set of snippets work
everywhere::

    {"op": "search", "collection": "library", "query": "how does docking work", "limit": 5}
    {"op": "search", "collection": "library", "vector": [0.1, ...], "where": {"kind": "note"}}
    {"op": "get",    "collection": "library", "ids": ["abc", "def"]}
    {"op": "list",   "collection": "library", "limit": 50, "offset": 100}
    {"op": "count",  "collection": "library"}
    {"op": "collections"}
    {"op": "upsert", "collection": "library", "documents": [{"id": "x", "text": "...",
                                                             "metadata": {"kind": "note"}}]}
    {"op": "delete", "collection": "library", "ids": ["x"]}

Where a store can't express something (Weaviate has no offset-free cursor for some
paths, Qdrant filters are typed), the driver raises ``DriverError`` naming the
provider and the unsupported field — an honest failure, never a silent reinterpretation.

**Output is still tabular.** Every op flattens to columns + rows via
``records_to_result``, so the results grid, CSV export and the agent's reader are
dialect-agnostic — only the input surface differs.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

from backend.modules.database.drivers.base import (
    ColumnInfo,
    DriverError,
    QueryResult,
    READ_ONLY_OPS,
    jsonable,
)

# Columns that lead the grid when present, in this order. Everything else (metadata
# keys) follows alphabetically, with the raw vector last since it's the least readable.
_LEADING_COLUMNS = ("id", "score", "distance", "text", "document")
_TRAILING_COLUMNS = ("vector", "embedding")

# Ops every vector driver is expected to understand. A driver that can't serve one
# raises DriverError rather than returning an empty result.
KNOWN_OPS = READ_ONLY_OPS | {"upsert", "delete", "create_collection", "drop_collection"}


@dataclass
class VectorQuery:
    """One parsed query body. Drivers read only the fields their op needs."""

    op: str
    collection: str | None = None
    query: str | None = None
    vector: list[float] | None = None
    where: dict[str, Any] | None = None
    ids: list[str] | None = None
    documents: list[dict[str, Any]] = field(default_factory=list)
    limit: int = 10
    offset: int = 0
    select: list[str] | None = None
    # Distance metric override for stores where it's per-query rather than fixed at
    # index time (LanceDB). None means "use the driver's default".
    metric: str | None = None

    @property
    def is_read_only(self) -> bool:
        return self.op in READ_ONLY_OPS

    def require_collection(self, provider: str) -> str:
        if not self.collection:
            raise DriverError(
                f"{provider}: op '{self.op}' needs a \"collection\". "
                'Example: {"op": "%s", "collection": "my_collection"}' % self.op
            )
        return self.collection


def parse_query(raw: str, *, provider: str, default_limit: int = 10) -> VectorQuery:
    """Parse a JSON query body, or raise DriverError with a usable example.

    Error messages matter here: this is the surface a user types into by hand, and
    a bare "expecting value: line 1 column 1" teaches them nothing.
    """
    text = (raw or "").strip()
    if not text:
        raise DriverError(
            f'{provider}: empty query. Try {{"op": "collections"}} to list collections.'
        )
    try:
        body = json.loads(text)
    except ValueError as exc:
        raise DriverError(
            f"{provider}: query must be a JSON object ({exc}). "
            'Example: {"op": "search", "collection": "my_collection", '
            '"query": "some text", "limit": 5}'
        ) from exc
    if not isinstance(body, dict):
        raise DriverError(
            f"{provider}: query must be a JSON object, got {type(body).__name__}."
        )

    op = str(body.get("op") or "").strip().lower()
    if not op:
        raise DriverError(
            f'{provider}: query needs an "op". Known ops: {", ".join(sorted(KNOWN_OPS))}.'
        )
    if op not in KNOWN_OPS:
        raise DriverError(
            f"{provider}: unknown op {op!r}. Known ops: {', '.join(sorted(KNOWN_OPS))}."
        )

    vector = body.get("vector")
    if vector is not None:
        if not isinstance(vector, list) or not all(
            isinstance(x, (int, float)) for x in vector
        ):
            raise DriverError(f'{provider}: "vector" must be an array of numbers.')
        vector = [float(x) for x in vector]

    where = body.get("where")
    if where is not None and not isinstance(where, dict):
        raise DriverError(f'{provider}: "where" must be an object of metadata filters.')

    ids = body.get("ids")
    if ids is not None:
        if not isinstance(ids, list):
            raise DriverError(f'{provider}: "ids" must be an array.')
        ids = [str(i) for i in ids]

    documents = body.get("documents") or []
    if documents and not isinstance(documents, list):
        raise DriverError(f'{provider}: "documents" must be an array of objects.')
    if documents and not all(isinstance(d, dict) for d in documents):
        raise DriverError(
            f'{provider}: each entry in "documents" must be an object '
            'like {"id": "x", "text": "...", "metadata": {}}.'
        )

    select = body.get("select")
    if select is not None:
        if not isinstance(select, list):
            raise DriverError(f'{provider}: "select" must be an array of column names.')
        select = [str(s) for s in select]

    limit = body.get("limit", default_limit)
    offset = body.get("offset", 0)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise DriverError(f'{provider}: "limit" must be a positive integer.')
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise DriverError(f'{provider}: "offset" must be a non-negative integer.')

    metric = body.get("metric")
    if metric is not None and not isinstance(metric, str):
        raise DriverError(
            f'{provider}: "metric" must be a string (e.g. "cosine", "l2").'
        )

    collection = body.get("collection")
    return VectorQuery(
        metric=metric,
        op=op,
        collection=str(collection) if collection else None,
        query=str(body["query"]) if body.get("query") is not None else None,
        vector=vector,
        where=where,
        ids=ids,
        documents=list(documents),
        limit=limit,
        offset=offset,
        select=select,
    )


def embed_text(text: str) -> list[float]:
    """Embed query text with the node's configured embedder.

    Drivers are synchronous and routes call them via ``asyncio.to_thread``, so this
    spins a loop in the worker thread. Calling it from the event-loop thread is a
    bug — it would deadlock — so that fails loudly instead.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass  # no loop in this thread: the expected worker-thread case
    else:
        raise DriverError(
            "embed_text() called on the event loop; run the driver via asyncio.to_thread."
        )

    from backend.modules.database.embeddings import get_embedding  # noqa: PLC0415

    vector, _model = asyncio.run(get_embedding(text))
    return vector


def resolve_vector(q: VectorQuery, *, provider: str) -> list[float]:
    """The query vector for a search: an explicit one, else the embedded query text."""
    if q.vector is not None:
        return q.vector
    if q.query:
        return embed_text(q.query)
    raise DriverError(
        f'{provider}: a search needs either "query" (text to embed) or "vector".'
    )


def _column_order(keys: set[str], select: list[str] | None) -> list[str]:
    if select:
        return select
    leading = [c for c in _LEADING_COLUMNS if c in keys]
    trailing = [c for c in _TRAILING_COLUMNS if c in keys]
    middle = sorted(keys - set(leading) - set(trailing))
    return leading + middle + trailing


def records_to_result(
    records: list[dict[str, Any]],
    *,
    started: float,
    row_limit: int,
    select: list[str] | None = None,
    message: str | None = None,
) -> QueryResult:
    """Flatten heterogeneous store records into the shared columns/rows grid.

    Records are dicts with differing key sets (metadata varies per document), so the
    column set is their union, ordered id/score/text first and the raw vector last.
    """
    elapsed = (time.perf_counter() - started) * 1000
    if not records:
        return QueryResult(
            columns=[],
            rows=[],
            rowcount=0,
            elapsed_ms=elapsed,
            message=message or "OK",
        )

    truncated = len(records) > row_limit
    kept = records[:row_limit]
    keys: set[str] = set()
    for rec in kept:
        keys.update(rec.keys())
    order = _column_order(keys, select)

    rows = [[jsonable(rec.get(col)) for col in order] for rec in kept]
    return QueryResult(
        columns=[ColumnInfo(name=c) for c in order],
        rows=rows,
        rowcount=len(rows),
        elapsed_ms=elapsed,
        truncated=truncated,
        message=message,
    )


def flatten_record(
    *,
    doc_id: Any = None,
    text: Any = None,
    metadata: dict[str, Any] | None = None,
    score: float | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one grid record, lifting metadata keys to top-level columns.

    Metadata is hoisted rather than nested because the whole point of the console is
    filtering and eyeballing by metadata; a single JSON blob column defeats that. A
    metadata key colliding with a reserved column is prefixed to keep both visible.
    """
    rec: dict[str, Any] = {}
    if doc_id is not None:
        rec["id"] = doc_id
    if score is not None:
        rec["score"] = round(float(score), 6)
    if text is not None:
        rec["text"] = text
    for key, value in (metadata or {}).items():
        col = f"meta.{key}" if key in rec or key in _LEADING_COLUMNS else key
        rec[col] = value
    for key, value in (extra or {}).items():
        rec[key] = value
    return rec


def affected_result(count: int, *, started: float, verb: str) -> QueryResult:
    """Result for a write op (upsert/delete/create/drop) — no grid, just a count."""
    elapsed = (time.perf_counter() - started) * 1000
    return QueryResult(
        columns=[],
        rows=[],
        rowcount=0,
        elapsed_ms=elapsed,
        affected=count,
        message=f"{verb} OK",
    )


# Distance spaces where a bounded 0..1-ish similarity is meaningful. L2/euclidean
# distance is unbounded, so there is no honest "score" to derive from it.
_COSINE_SPACES = {"cosine", "cos", "cosine_distance"}


def similarity_from_distance(distance: float | None, space: str | None) -> float | None:
    """Convert a distance to a similarity **only when the metric allows it**.

    Returning ``1 - distance`` regardless of metric is a real trap: Chroma and
    LanceDB both default to squared **L2**, where that expression yields negative,
    meaningless "scores" that still sort plausibly. When the space isn't cosine this
    returns ``None`` and the caller shows the raw distance instead — ascending
    distance is the correct ranking either way.
    """
    if distance is None:
        return None
    if space and space.strip().lower() in _COSINE_SPACES:
        return round(1.0 - float(distance), 6)
    return None


def unsupported(provider: str, op: str, detail: str = "") -> DriverError:
    """Uniform 'this store can't do that' error."""
    tail = f" {detail}" if detail else ""
    return DriverError(
        f"{provider}: op '{op}' is not supported by this provider.{tail}"
    )

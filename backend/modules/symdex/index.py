"""The symdex index: reindex/search over the shared vector store, with an
embedding-model drift guard.

Modeled on `code/semantic.py` (`SemanticIndex`), with two differences worth
having: per-kind incremental rebuilds (an id-prefix delete instead of dropping
the whole collection), and a **meta sidecar** (`$HORRIBLE_DATA_DIR/symdex.meta.json`)
recording `{embed_model, dim}` — `get_embedding` picks its model dynamically and
silently falls back to a 384-dim hash offline, so without the guard a model
change would crash deep inside Arrow (`DimensionMismatch`) and a fallback write
would poison a real-model collection. Instead: a model change forces a full
rebuild, and a mismatched searcher gets `status="reindex_needed"`, never a crash.
Progress streams on the `symdex` /ws channel (broadcaster pattern from
library/code). See docs/modules/symdex.mdx.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from backend.modules.database.embeddings import get_embedding, get_embeddings
from backend.modules.database.vectorstore import (
    delete_collection,
    delete_documents_with_prefix,
    init_db,
    list_documents,
    search_documents,
    upsert_documents,
)
from backend.modules.lsp import symbol_store
from backend.modules.symdex.extract_docs import extract_docs
from backend.modules.symdex.extract_packages import extract_packages
from backend.modules.symdex.extract_schema import extract_schemas
from backend.modules.symdex.extract_stdlib import STDLIB_EMBED, extract_stdlib
from backend.modules.symdex.models import KIND_PREFIXES
from backend import jsonstore, paths

logger = logging.getLogger(__name__)

_COLLECTION = "symdex"
_FALLBACK_PREFIX = "local-fallback"

# Documents per embed+write round. Large enough that the fixed per-call costs (one
# whole-table `merge_insert`, one model resolution, one HTTP round-trip) are amortized
# across many symbols; small enough that progress still streams and a provider dropping
# out costs at most one chunk of work.
EMBED_CHUNK = 256


def _meta_path() -> Path:
    return paths.data_dir() / "symdex.meta.json"


def _read_meta() -> dict[str, Any]:
    try:
        return json.loads(_meta_path().read_text())
    except (OSError, ValueError):
        return {}


def _write_meta(meta: dict[str, Any]) -> None:
    jsonstore.write_text(_meta_path(), json.dumps(meta))


def _is_fallback(method: str) -> bool:
    return method.startswith(_FALLBACK_PREFIX)


class SymdexBroadcaster:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    def publish(self, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass


symdex_events = SymdexBroadcaster()


async def push_symdex_events(conn: Any) -> None:
    """Per-connection pump: forward index progress to a browser (see app.py)."""
    queue = symdex_events.subscribe()
    try:
        while True:
            event = await queue.get()
            await conn.send_json(
                {"channel": "symdex", "event": event["event"], "data": event["data"]}
            )
    finally:
        symdex_events.unsubscribe(queue)


class SymdexIndex:
    def __init__(self) -> None:
        self._building = False

    @property
    def building(self) -> bool:
        return self._building

    def _publish(self, state: str, kind: str, done: int, total: int) -> None:
        symdex_events.publish(
            {
                "event": "index",
                "data": {"state": state, "kind": kind, "done": done, "total": total},
            }
        )

    def status(self) -> dict[str, Any]:
        init_db()
        _docs, total = list_documents(_COLLECTION, 1, 0)
        meta = _read_meta()
        return {
            "building": self._building,
            "total": total,
            "counts": dict(meta.get("counts", {})),
            "embed_model": meta.get("embed_model"),
            "reindex_needed": False,
        }

    async def _probe_embedder(self) -> tuple[str, int] | None:
        """The live embedder's (model, dim), or None when only the offline hash
        fallback is available (we refuse to build an index out of hashes)."""
        vector, method = await get_embedding("symdex embedder probe")
        if _is_fallback(method):
            return None
        return method, len(vector)

    async def reindex(
        self, kinds: list[str], interpreter: str | None = None
    ) -> dict[str, Any]:
        """(Re)build the given kinds. Guarded against concurrent kicks; a change
        of embedding model forces a FULL rebuild of every kind (the collection's
        vector width is fixed at first write). Never raises."""
        if self._building:
            return {"started": False, "reason": "already building"}
        self._building = True
        try:
            init_db()
            probe = await self._probe_embedder()
            if probe is None:
                # No embedder — but the *relational* projection (`code_symbols`, what
                # editor completion actually reads) needs no vectors at all. Build it
                # anyway, so intellisense works offline; only semantic search waits.
                built = 0
                for kind in kinds:
                    if kind in ("packages", "stdlib"):
                        built += len(await self._collect(kind, interpreter))
                self._publish("offline", ",".join(kinds), 0, 0)
                return {
                    "started": False,
                    "reason": "embedding provider offline (hash fallback refused)",
                    "relational_symbols": built,
                }
            model, dim = probe
            meta = _read_meta()
            if meta.get("embed_model") not in (None, model):
                logger.info(
                    "symdex embed model changed (%s -> %s); full rebuild",
                    meta.get("embed_model"),
                    model,
                )
                delete_collection(_COLLECTION)
                # Rebuild only the kinds that actually had vectors under the old model
                # (plus whatever was asked for). A kind that was never built has nothing
                # to invalidate, and rebuilding it here would drag the whole stdlib and
                # every installed package into what should be a targeted repair.
                previous = [k for k, n in meta.get("counts", {}).items() if n]
                kinds = list(dict.fromkeys([*kinds, *previous]))
                meta = {}

            counts = dict(meta.get("counts", {}))
            for kind in kinds:
                jobs = await self._collect(kind, interpreter)
                delete_documents_with_prefix(_COLLECTION, KIND_PREFIXES[kind])
                total = len(jobs)
                self._publish("building", kind, 0, total)
                # Chunked, not per-document: one embedding round-trip and one LanceDB
                # write per chunk. Both of those costs are per-*call*, not per-row —
                # a per-document loop paid a whole-table `merge_insert` (~1.5s on a 5k
                # table) and a fresh model-discovery probe for every single symbol,
                # which is what turned a ~40k-symbol build into hours.
                done = 0
                for start in range(0, total, EMBED_CHUNK):
                    chunk = jobs[start : start + EMBED_CHUNK]
                    vectors, method = await get_embeddings(
                        [text for _i, text, _m in chunk]
                    )
                    if (
                        _is_fallback(method)
                        or len(vectors) != len(chunk)
                        or any(len(v) != dim for v in vectors)
                    ):
                        # Provider dropped mid-build: stop rather than poison.
                        self._publish("failed", kind, done, total)
                        return {"started": True, "error": "embedding provider lost"}
                    await asyncio.to_thread(
                        upsert_documents,
                        _COLLECTION,
                        [
                            (doc_id, text, metadata, vector)
                            for (doc_id, text, metadata), vector in zip(chunk, vectors)
                        ],
                    )
                    done += len(chunk)
                    self._publish("building", kind, done, total)
                counts[kind] = total
                self._publish("ready", kind, total, total)

            _write_meta(
                {
                    "embed_model": model,
                    "dim": dim,
                    "counts": counts,
                    "updated": time.time(),
                }
            )
            return {"started": True, "counts": counts, "embed_model": model}
        except Exception as exc:  # noqa: BLE001 — a reindex failure shouldn't crash callers
            logger.exception("symdex reindex failed")
            self._publish("failed", ",".join(kinds), 0, 0)
            return {"started": False, "error": str(exc)}
        finally:
            self._building = False

    async def _collect(
        self, kind: str, interpreter: str | None
    ) -> list[tuple[str, str, dict[str, Any]]]:
        """Gather (id, text, metadata) jobs for one kind. Extraction is sync and
        file/subprocess-bound, so it runs on a thread. The packages and stdlib kinds
        also project their rows into the `code_symbols` prefix index."""
        if kind in ("packages", "stdlib"):
            if not interpreter:
                from backend.modules.lsp.pyenv import resolve_python_interpreter

                interpreter = await asyncio.to_thread(resolve_python_interpreter, None)
            if not interpreter:
                return []
            stdlib = kind == "stdlib"
            harvests = await asyncio.to_thread(
                extract_stdlib if stdlib else extract_packages, interpreter
            )
            jobs: list[tuple[str, str, dict[str, Any]]] = []
            for harvest in harvests:
                # The relational projection is exhaustive — it's a cheap SQLite prefix
                # scan and completion must offer everything.
                await asyncio.to_thread(
                    symbol_store.replace_source,
                    f"{'std' if stdlib else 'pkg'}:{harvest.dist}",
                    "python",
                    [d.store_row() for d in harvest.docs],
                )
                # The embedding projection is bounded: for the stdlib, only the
                # curated modules are worth the vectors (see extract_stdlib).
                if stdlib and harvest.dist not in STDLIB_EMBED:
                    continue
                # ...and only symbols that actually carry a docstring. Without one the
                # embeddable text degenerates to the name restated as a sentence
                # ("class numpy.linalg.LinAlgError"), which adds nothing semantic —
                # you find that by prefix, and the relational index already does,
                # instantly. It isn't free either: 58% of the corpus has no docstring,
                # so embedding them more than doubled build time while diluting the
                # vector space with near-duplicate name-only rows.
                jobs.extend((d.id, d.text, d.metadata) for d in harvest.docs if d.doc)
            return jobs
        if kind == "schema":
            schemas = await asyncio.to_thread(extract_schemas)
            return [(d.id, d.text, d.metadata) for d in schemas]
        if kind == "docs":
            chunks = await asyncio.to_thread(extract_docs)
            return [(d.id, d.text, d.metadata) for d in chunks]
        return []

    async def search(
        self, query: str, kind: str | None = None, limit: int = 8
    ) -> dict[str, Any]:
        """Embed `query` and cosine-rank it against the index, optionally filtered
        to one kind (client-side by id prefix). A drifted embedder (model change
        or offline fallback) returns `reindex_needed` — never a dimension crash."""
        init_db()
        _docs, total = list_documents(_COLLECTION, 1, 0)
        if total == 0:
            return {
                "query": query,
                "status": "building" if self._building else "empty",
                "results": [],
            }
        meta = _read_meta()
        embedding, method = await get_embedding(query)
        if _is_fallback(method) or (meta.get("dim") and len(embedding) != meta["dim"]):
            return {"query": query, "status": "reindex_needed", "results": []}
        prefix = KIND_PREFIXES.get(kind or "")
        rows = search_documents(_COLLECTION, embedding, limit * 4 if prefix else limit)
        results = []
        for row in rows:
            if prefix and not str(row["id"]).startswith(prefix):
                continue
            row_kind = next(
                (k for k, p in KIND_PREFIXES.items() if str(row["id"]).startswith(p)),
                "unknown",
            )
            results.append(
                {
                    "id": row["id"],
                    "kind": row_kind,
                    "text": row["text"],
                    "metadata": row["metadata"],
                    "score": row["score"],
                }
            )
            if len(results) >= limit:
                break
        return {"query": query, "status": "ok", "results": results}


# Process-global singleton (import and use directly, like `semantic_index`).
symdex_index = SymdexIndex()

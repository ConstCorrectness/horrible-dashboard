"""Semantic code search over the shared vector store (Slice 2 of the code module).

Embeds each tree-sitter **definition** (from the Slice 1 index) into the `documents`
table under the `code` collection — the same `get_embedding` → `upsert_document`
pipeline the library ingest uses ([library/ingest.py]) — then cosine-ranks a query
against it. Works offline (the embeddings module falls back to a deterministic hash,
so search degrades to lexical), with real semantics when an embedding model is
available. A reindex is a **full rebuild** (delete the `code` collection, re-embed);
incremental-on-save is a follow-up. See docs/modules/code.mdx.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from backend.modules.code.index import _walk_source_files, code_index
from backend.modules.code.locus import code_events
from backend.modules.code.models import Symbol
from backend.modules.database.embeddings import get_embedding, get_embeddings
from backend.modules.database.vectorstore import (
    delete_collection,
    init_db,
    list_documents,
    search_documents,
    upsert_documents,
)

logger = logging.getLogger(__name__)

_COLLECTION = "code"

# Documents per embed+write round; see `symdex/index.py` for why this is chunked.
EMBED_CHUNK = 256
_MAX_EMBED_CHARS = 2000


def _embed_text(sym: Symbol, lines: list[str]) -> str:
    """What we embed for a definition: a short header (kind + name + container) for
    signal, then the symbol's own source, capped so a huge function stays bounded."""
    header = f"{sym.kind} {sym.name}"
    if sym.container:
        header += f" in {sym.container}"
    start = max(sym.range.start.line - 1, 0)
    end = min(sym.range.end.line, len(lines))
    body = "\n".join(lines[start:end])
    return f"{header}\n{body}"[:_MAX_EMBED_CHARS]


class SemanticIndex:
    def __init__(self) -> None:
        self._building = False

    @property
    def building(self) -> bool:
        return self._building

    def count(self) -> int:
        """How many definitions are currently embedded in the `code` collection."""
        init_db()
        _docs, total = list_documents(_COLLECTION, 1, 0)
        return total

    def _publish(self, state: str, done: int, total: int) -> None:
        # Rides the existing `code` /ws broadcaster beside the `locus` event.
        code_events.publish(
            {"event": "index", "data": {"state": state, "done": done, "total": total}}
        )

    async def reindex(self, roots: list[Path]) -> dict[str, Any]:
        """Full rebuild: embed every definition across the roots. Guarded so two
        concurrent kicks don't double-build (the synchronous check-and-set runs
        before the first await). Never raises — failures are logged."""
        if self._building:
            return {"started": False, "reason": "already building"}
        self._building = True
        try:
            init_db()
            delete_collection(_COLLECTION)
            # Collect (path, symbol, text) up front so `total` is exact for progress.
            jobs: list[tuple[Path, Symbol, str]] = []
            for root in roots:
                for path in _walk_source_files(root):
                    try:
                        lines = path.read_text(
                            encoding="utf-8", errors="replace"
                        ).splitlines()
                    except OSError:
                        continue
                    for sym in code_index.document_symbols(path):
                        jobs.append((path, sym, _embed_text(sym, lines)))

            total = len(jobs)
            self._publish("building", 0, total)
            # Chunked embed + write. Both costs are per-*call*, not per-row: a LanceDB
            # `merge_insert` rewrites the table whatever you hand it (~1.5s on a 5k-row
            # table), and model discovery used to run once per document. Indexing a
            # repo's symbols one at a time made this a multi-hour job; see
            # `symdex/index.py`, which has the same shape.
            done = 0
            for start in range(0, total, EMBED_CHUNK):
                chunk = jobs[start : start + EMBED_CHUNK]
                vectors, _method = await get_embeddings(
                    [text for _p, _s, text in chunk]
                )
                if len(vectors) != len(chunk):
                    raise RuntimeError("embedding provider returned a short batch")
                await asyncio.to_thread(
                    upsert_documents,
                    _COLLECTION,
                    [
                        (
                            f"code:{path}#{sym.range.start.line}",
                            text,
                            {
                                "path": str(path),
                                "name": sym.name,
                                "kind": sym.kind,
                                "container": sym.container,
                                "range": sym.range.model_dump(),
                            },
                            vector,
                        )
                        for (path, sym, text), vector in zip(chunk, vectors)
                    ],
                )
                done += len(chunk)
                self._publish("building", done, total)
            self._publish("ready", total, total)
            return {"started": True, "indexed": total}
        except Exception as exc:  # noqa: BLE001 — a reindex failure shouldn't crash callers
            logger.exception("code reindex failed")
            self._publish("failed", 0, 0)
            return {"started": False, "error": str(exc)}
        finally:
            self._building = False

    async def search(
        self, query: str, roots: list[Path], limit: int = 20
    ) -> dict[str, Any]:
        """Embed `query` and cosine-rank it against the `code` collection. Returns an
        empty result (with `building` reflecting any in-flight rebuild) when nothing is
        indexed yet — the route decides whether to auto-kick a reindex."""
        init_db()
        if self.count() == 0:
            return {"query": query, "building": self._building, "results": []}
        embedding, _method = await get_embedding(query)
        rows = search_documents(_COLLECTION, embedding, limit)
        results = [
            {
                "name": md.get("name"),
                "kind": md.get("kind"),
                "container": md.get("container"),
                "path": md.get("path"),
                "range": md.get("range"),
                "score": row["score"],
            }
            for row in rows
            if (md := row["metadata"])
        ]
        return {"query": query, "building": self._building, "results": results}


# Process-global singleton (import and use directly, like `code_index`).
semantic_index = SemanticIndex()

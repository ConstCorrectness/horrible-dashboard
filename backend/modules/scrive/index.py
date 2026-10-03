"""The semantic half of `scrive.searchSite`: each site's pages as vectors.

One LanceDB collection per site, `scrive-<site>`, holding a page's sections as
chunks with ids `<path>#<n>`. It is a separate collection rather than library
sources: a library source is ingested once, while a page changes on every save
and is re-indexed whole.

Two rules from the vector store that are quiet if wrong:

- **Batch.** `merge_insert` rewrites the table on every call (~1.5 s on a few
  thousand rows), so changes are coalesced: a save schedules its page, and after
  `DEBOUNCE_S` of quiet every scheduled page of a site is embedded in one request
  and written in one upsert.
- **Never persist the hash fallback.** When no embedding model answers,
  `get_embeddings` returns deterministic hash vectors. Those would sit in the index
  looking like real ones, so nothing is written, and search runs on keywords alone.

Off with the `scrive.semanticSearch` setting: page text is sent to whatever
embedding model the agent is configured with, which may not be local.
"""

from __future__ import annotations

import asyncio
import logging
import re

from backend.modules.database import vectorstore
from backend.modules.database.embeddings import get_embeddings
from backend.modules.scrive import sections, store
from backend.modules.settings.routes import get_value

logger = logging.getLogger(__name__)

SETTING = "scrive.semanticSearch"
DEBOUNCE_S = 10.0
_CHUNK_CHARS = 1800

_pending: dict[str, set[str]] = {}
_timers: dict[str, asyncio.TimerHandle] = {}
_building: set[str] = set()
#: Background tasks, held so they are not collected mid-run.
_tasks: set[asyncio.Task] = set()
#: Why the last attempt wrote nothing, per site — surfaced in search results.
unavailable: dict[str, str] = {}


def enabled() -> bool:
    return vectorstore.lancedb is not None and bool(get_value(SETTING, True))


def collection(site_id: str) -> str:
    return f"scrive-{site_id}"


def chunks(rel_path: str, text: str) -> list[str]:
    """A page as indexable text: one chunk per section (split further when long),
    each prefixed with the page title and its heading so a chunk read alone still
    says where it is from."""
    fm = sections.frontmatter_of(text)
    title = str(fm.get("title") or rel_path)
    lines = text.split("\n")
    start = sections.body_start(lines)
    heads = [h for h in sections.headings(text) if h.level <= 3]
    bounds = [(start, "")] + [(h.line, h.text) for h in heads]
    out: list[str] = []
    for i, (first, heading) in enumerate(bounds):
        last = bounds[i + 1][0] if i + 1 < len(bounds) else len(lines)
        body = "\n".join(line.rstrip("\r") for line in lines[first:last]).strip()
        if not body:
            continue
        label = f"{title} — {heading}" if heading else title
        for part in _split(body):
            out.append(f"{label}\n{part}")
    if fm.get("description"):
        out.insert(0, f"{title}\n{fm['description']}")
    return out


def _split(body: str) -> list[str]:
    if len(body) <= _CHUNK_CHARS:
        return [body]
    parts, current = [], ""
    for para in re.split(r"\n\s*\n", body):
        if current and len(current) + len(para) > _CHUNK_CHARS:
            parts.append(current)
            current = ""
        current = f"{current}\n\n{para}" if current else para
    if current:
        parts.append(current)
    return [p[: _CHUNK_CHARS * 2] for p in parts]


async def index_pages(site_id: str, paths: set[str]) -> bool:
    """Re-index `paths` of a site (a missing page is removed). Answers whether
    anything was written; `unavailable[site]` says why not."""
    base = store.site_dir(site_id)
    name = collection(site_id)
    rows_text: list[tuple[str, str, dict]] = []
    for rel in sorted(paths):
        path = base / rel
        if not path.is_file() or path.suffix != ".md":
            continue
        text = path.read_bytes().decode("utf-8", "replace")
        meta = {
            "path": rel,
            "title": str(sections.frontmatter_of(text).get("title") or rel),
        }
        for n, chunk in enumerate(chunks(rel, text)):
            rows_text.append((f"{rel}#{n}", chunk, {**meta, "chunk": n}))
    vectors: list[list[float]] = []
    if rows_text:
        vectors, method = await get_embeddings([t for _i, t, _m in rows_text])
        if method == "local-fallback":
            unavailable[site_id] = (
                "no embedding model answered; searching by keywords only"
            )
            return False
    await asyncio.to_thread(_write, name, paths, rows_text, vectors)
    unavailable.pop(site_id, None)
    return True


def _write(name: str, paths: set[str], rows_text, vectors) -> None:
    for rel in paths:
        vectorstore.delete_documents_with_prefix(name, f"{rel}#")
    rows = [(i, t, m, v) for (i, t, m), v in zip(rows_text, vectors, strict=True)]
    try:
        vectorstore.upsert_documents(name, rows)
    except vectorstore.DimensionMismatch:
        # The embedding model changed width: the old vectors are useless with the
        # new model. Start the site's index over.
        vectorstore.delete_collection(name)
        vectorstore.upsert_documents(name, rows)


def _all_pages(site_id: str) -> set[str]:
    return {m.path for m in store.list_pages(site_id) if m.path.endswith(".md")}


async def build(site_id: str) -> None:
    """Index every page of a site (first search, or after the model changed)."""
    if site_id in _building:
        return
    _building.add(site_id)
    try:
        await asyncio.to_thread(vectorstore.delete_collection, collection(site_id))
        await index_pages(site_id, _all_pages(site_id))
    except Exception:  # noqa: BLE001 - an index is a convenience; search still works
        logger.exception("scrive: indexing site %s failed", site_id)
    finally:
        _building.discard(site_id)


def has_index(site_id: str) -> bool:
    db = vectorstore._get_db()  # noqa: SLF001 - the store has no public "exists"
    return db is not None and collection(site_id) in db.table_names()


def _spawn(coro) -> None:
    task = asyncio.ensure_future(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def schedule(site_id: str, rel_path: str) -> None:
    """Note that a page changed; it is re-indexed after `DEBOUNCE_S` of quiet."""
    if not enabled() or not rel_path.endswith(".md"):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _pending.setdefault(site_id, set()).add(rel_path)
    if timer := _timers.pop(site_id, None):
        timer.cancel()
    _timers[site_id] = loop.call_later(DEBOUNCE_S, lambda: _spawn(_flush(site_id)))


async def _flush(site_id: str) -> None:
    _timers.pop(site_id, None)
    paths = _pending.pop(site_id, set())
    # A site never searched has no index yet; the first search builds it whole.
    if not paths or not has_index(site_id):
        return
    try:
        await index_pages(site_id, paths)
    except Exception:  # noqa: BLE001
        logger.exception("scrive: re-indexing %s failed", site_id)


async def search(site_id: str, query: str, limit: int) -> list[dict]:
    """Semantic hits `{path, title, text, score}`, best first; `[]` when there is
    no index, no model, or the feature is off. Building a missing index is started
    here and answered next time."""
    if not enabled():
        return []
    if not has_index(site_id):
        _spawn(build(site_id))
        return []
    vectors, method = await get_embeddings([query])
    if method == "local-fallback" or not vectors:
        unavailable[site_id] = "no embedding model answered; searching by keywords only"
        return []
    rows = await asyncio.to_thread(
        vectorstore.search_documents, collection(site_id), vectors[0], limit * 3
    )
    return [
        {
            "path": r["metadata"].get("path", ""),
            "title": r["metadata"].get("title", ""),
            "text": r["text"],
            "score": r["score"],
        }
        for r in rows
    ]

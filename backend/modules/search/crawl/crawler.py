"""The focused crawler: a bounded BFS over a seed's own domain, into `webindex`.

Deliberately small. This is not a web crawler in the Common Crawl sense — the honest
economics say a general index loses to a search API below roughly 60M queries/month.
What it *is* good at is the thing search APIs are worst at: a handful of sites you
read constantly (framework docs, a few ML blogs, an API reference), indexed in full,
searchable instantly, free per query, and current for the sites that matter to you.

**No JavaScript rendering.** The `browser-engine` extra could drive Playwright here,
but a rendered crawl is roughly 100× the cost per page, and at 200 pages a seed the
trade is obviously wrong. JS-only docs sites simply don't index. That's a known,
accepted gap — please don't "fix" it by wiring in Chromium.

**The two costs that actually bite**, both handled here:

- `upsert_documents` is a whole-table `merge_insert`, ~1.5s per call regardless of
  batch size. A docs page is 3–10 chunks, so flushing per page would spend five
  minutes in Arrow to index two hundred pages. The buffer therefore spans **pages**,
  not chunks within a page.
- Embedding is the other half, and `crawl_pages`'s content hash is what avoids it: an
  unchanged page costs a conditional GET and nothing else.

**Three ingest paths, tried in order** (see `llmstxt.py` for why): the publisher's
`llms-full.txt` corpus, the `llms.txt` link index used as the frontier, and this
BFS. Only the third one is a crawl in the ordinary sense; the first two are the
publisher telling us what its documentation is, which is strictly better information
than anything link-following can recover.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit

from backend.modules.search.canonical import canonical_url, host_of
from backend.modules.search.crawl import index as webindex
from backend.modules.search.crawl import llmstxt, store, versions
from backend.modules.search.crawl.robots import USER_AGENT, HostLimiter, RobotsCache

logger = logging.getLogger(__name__)

_MAX_PAGE_BYTES = 3_000_000
# A corpus is legitimately much larger than a page — but not unboundedly so.
# `docs.claude.com/llms-full.txt` is 25 MB, which at the default chunk size is ~25,000
# chunks for one seed: more than the whole index holds today, and hours of embedding.
# Refusing it isn't a loss, because that site also publishes an `llms.txt` index and
# the fallback honours `max_pages`.
_MAX_CORPUS_BYTES = 8_000_000
_FETCH_TIMEOUT_S = 20.0
# Below this, "extracted text" is a nav bar or a cookie banner, not a document.
_MIN_TEXT_CHARS = 200

ProgressFn = Callable[[dict[str, Any]], None]


@dataclass
class CrawlStats:
    seed_id: str
    fetched: int = 0
    indexed: int = 0
    unchanged: int = 0
    not_modified: int = 0
    skipped: int = 0
    errors: int = 0
    chunks: int = 0
    notes: list[str] = field(default_factory=list)
    # Which of the three ingest paths ran: "corpus", "index" or "crawl".
    source: str = "crawl"
    version: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "seed_id": self.seed_id,
            "fetched": self.fetched,
            "indexed": self.indexed,
            "unchanged": self.unchanged,
            "not_modified": self.not_modified,
            "skipped": self.skipped,
            "errors": self.errors,
            "chunks": self.chunks,
            "notes": self.notes,
            "source": self.source,
            "version": self.version,
        }


class _LinkParser(HTMLParser):
    """Collect `<a href>` values. stdlib rather than a parser dependency — we need
    hrefs, and trafilatura is already doing the content extraction."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        for name, value in attrs:
            if name == "href" and value:
                self.hrefs.append(value)


def extract_links(html: str, base_url: str) -> list[str]:
    """Absolute http(s) links from a page, fragments dropped. Pure — testable."""
    parser = _LinkParser()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001 — malformed HTML is the normal case
        pass
    out: list[str] = []
    seen: set[str] = set()
    for href in parser.hrefs:
        href = href.strip()
        if not href or href.startswith(
            ("#", "javascript:", "mailto:", "tel:", "data:")
        ):
            continue
        absolute = urljoin(base_url, href)
        parts = urlsplit(absolute)
        if parts.scheme not in ("http", "https"):
            continue
        clean = absolute.split("#", 1)[0]
        if clean not in seen:
            seen.add(clean)
            out.append(clean)
    return out


def in_scope(url: str, spec: dict[str, Any], *, apply_deny: bool = True) -> bool:
    """Whether a URL belongs to this seed. Pure — this is the rule worth testing.

    Host match allows subdomains of an allowed domain (`docs.example.com` under
    `example.com`) but not suffix collisions (`notexample.com`).

    `apply_deny=False` is used for links taken from the publisher's own `llms.txt`.
    Deny patterns exist to keep *link-following* out of junk — `/genindex`,
    `/_sources`, archived version trees — and a curated index contains none of that by
    construction. It matters concretely: Hugging Face's index links pinned
    `/docs/transformers/v5.14.0/…` URLs, which `deny_patterns: ["/v[0-9]"]` would
    reject, discarding the entire index. Domain and allow patterns still apply, so an
    index that links off-site or outside its own docs tree can't drag the crawl along.
    """
    host = host_of(url)
    if not host:
        return False
    allowed = [d.lower().removeprefix("www.") for d in spec.get("allow_domains") or []]
    if allowed and not any(host == d or host.endswith("." + d) for d in allowed):
        return False

    path = urlsplit(url).path or "/"
    allow_patterns = spec.get("allow_patterns") or []
    if allow_patterns and not any(re.search(p, path) for p in allow_patterns):
        return False
    if not apply_deny:
        return True
    return not any(re.search(p, url) for p in spec.get("deny_patterns") or [])


def content_hash(text: str) -> str:
    """Hash of the *extracted* text, not the raw HTML.

    Deliberate: docs sites embed build ids, CSRF tokens and rotating banner copy in
    the markup, so hashing HTML would report a change on every crawl and defeat the
    whole skip path. The extracted article is what we actually index, so it's what
    "changed" should mean.
    """
    return hashlib.sha256(" ".join(text.split()).encode("utf-8", "replace")).hexdigest()


async def crawl_seed(
    seed_id: str,
    *,
    force: bool = False,
    on_progress: ProgressFn | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> CrawlStats:
    """Crawl one seed into `webindex`. Returns what happened.

    Never raises for ordinary web trouble — a dead host, a 500, an unparseable page
    are all recorded and stepped over. It *does* stop early when the embedding
    provider is unavailable, because continuing would write hash-fallback vectors
    that permanently pin the collection's width.
    """
    from backend.modules.database.vectorstore import init_db

    seed = store.get_seed(seed_id)
    if seed is None:
        raise ValueError(f"unknown crawl seed {seed_id!r}")
    spec = seed["config"]
    stats = CrawlStats(seed_id=seed_id)

    init_db()
    robots = RobotsCache()
    limiter = HostLimiter(_crawl_delay_setting())

    # Resolved and recorded before a single page is written, because every page this
    # run indexes is stamped with it. See `store.set_seed_version`.
    version = await _resolve_version(spec)
    stats.version = version
    store.set_seed_version(seed_id, version)

    max_depth = int(spec.get("max_depth") or 2)
    max_pages = int(spec.get("max_pages") or 200)
    tags = list(spec.get("tags") or [])

    published = await _llms_txt(
        seed_id, spec, robots, limiter, force=force, version=version
    )
    if published is not None and (published.docs or published.unchanged):
        return await _ingest_corpus(
            seed_id,
            published,
            stats=stats,
            tags=tags,
            version=version,
            force=force,
            max_pages=max_pages,
            on_progress=on_progress,
            is_cancelled=is_cancelled,
        )

    frontier: deque[tuple[str, int]] = deque(
        (str(u), 0) for u in spec.get("start_urls") or []
    )
    seen: set[str] = {canonical_url(u) for u, _d in frontier}

    # The publisher's own table of contents, when it has one. Every entry enters at
    # depth 0: these are documentation by declaration, not links that happened to be
    # within `max_depth` of a start page.
    curated: dict[str, dict[str, Any]] = {}
    if published is not None and published.index is not None:
        stats.source = "index"
        curated = llmstxt.entry_metadata(published.index.entries)
        for entry in published.index.entries:
            key = canonical_url(entry.url)
            if key not in seen and in_scope(entry.url, spec, apply_deny=False):
                seen.add(key)
                frontier.append((entry.url, 0))

    # Re-check everything already indexed, not just what this pass can rediscover.
    # On a re-crawl the start page answers 304, which means no HTML and therefore no
    # links — so a frontier built purely from discovery would check one URL and stop,
    # and a changed article would never be seen again. Known pages cost a conditional
    # request each and almost always come back 304.
    for url in store.known_urls(seed_id):
        key = canonical_url(url)
        if key not in seen:
            seen.add(key)
            frontier.append((url, max_depth))
    pending: list[tuple[str, str, dict[str, Any]]] = []  # (doc_id, text, metadata)

    def progress() -> None:
        if on_progress:
            on_progress(stats.as_dict())

    while frontier and stats.fetched < max_pages:
        if is_cancelled and is_cancelled():
            stats.notes.append("cancelled")
            break

        url, depth = frontier.popleft()
        canonical = canonical_url(url)

        if not await robots.allowed(url):
            stats.skipped += 1
            continue
        limiter.note_crawl_delay(urlsplit(url).netloc, await robots.crawl_delay(url))

        known = store.get_page(canonical, seed_id)
        # A version change has to defeat both skip levels. Level 2 compares hashes
        # below, but level 1 never gets a body at all — so the conditional headers
        # are withheld rather than sent, or a page whose text is unchanged would keep
        # chunk metadata naming the previous release and vanish from a
        # version-filtered search.
        restale = _version_changed(known, version)
        conditional: dict[str, str] = {}
        if known and not force and not restale:
            if known.get("etag"):
                conditional["If-None-Match"] = str(known["etag"])
            if known.get("last_modified"):
                conditional["If-Modified-Since"] = str(known["last_modified"])

        host = urlsplit(url).netloc
        await limiter.acquire(host)
        try:
            outcome = await _fetch(url, conditional)
        finally:
            limiter.release(host)

        if outcome.get("gone"):
            # 404/410: forget it rather than leaving a phantom in the index.
            webindex.forget_page(canonical)
            store.drop_page(canonical, seed_id)
            stats.skipped += 1
            progress()
            continue
        if error := outcome.get("error"):
            stats.errors += 1
            store.record_page(
                canonical, seed_id, content_hash="", status="error", error=str(error)
            )
            progress()
            continue

        stats.fetched += 1

        # Level 1: the server said nothing changed. No body, no extraction, no embed.
        if outcome.get("not_modified"):
            stats.not_modified += 1
            store.touch_page(canonical, seed_id)
            progress()
            continue

        html = str(outcome["html"])
        final_url = str(outcome["final_url"])

        if depth < max_depth:
            for link in extract_links(html, final_url):
                key = canonical_url(link)
                if key in seen or not in_scope(link, spec):
                    continue
                seen.add(key)
                frontier.append((link, depth + 1))

        from backend.modules.library.extract import extract_article

        if llmstxt.looks_like_markdown(html):
            # A `.md` twin, which trafilatura would return nothing from.
            extracted_title, text = llmstxt.markdown_article(html)
        else:
            article = extract_article(html, final_url)
            extracted_title, text = article.title, (article.text or "").strip()
        # The publisher's own title for the page beats the HTML's, which on a docs
        # site is frequently the product name repeated on all two hundred pages.
        curated_meta = curated.get(url) or curated.get(final_url) or {}
        title = str(curated_meta.get("title") or "") or extracted_title
        if len(text) < _MIN_TEXT_CHARS:
            stats.skipped += 1
            store.record_page(
                canonical,
                seed_id,
                title=title,
                content_hash="",
                status="error",
                error="no extractable content",
            )
            progress()
            continue

        digest = content_hash(text)

        # Level 2: body came back but says the same thing. Skip the expensive half.
        if known and not force and not restale and known.get("content_hash") == digest:
            stats.unchanged += 1
            store.touch_page(canonical, seed_id)
            progress()
            continue

        # Level 3: genuinely new or changed — replace, don't duplicate.
        webindex.forget_page(canonical)
        chunks = _chunk(text)
        pending.extend(
            _chunk_rows(
                canonical,
                chunks,
                url=final_url,
                title=title or canonical,
                seed_id=seed_id,
                tags=tags,
                version=version,
            )
        )
        store.record_page(
            canonical,
            seed_id,
            title=title,
            etag=outcome.get("etag"),
            last_modified=outcome.get("last_modified"),
            content_hash=digest,
            chunk_count=len(chunks),
            status="ok",
            indexed=True,
            version=version,
        )
        stats.indexed += 1
        stats.chunks += len(chunks)

        # The buffer spans pages on purpose — see the module docstring.
        if len(pending) >= webindex.EMBED_CHUNK:
            if note := await _flush(pending):
                stats.notes.append(note)
                stats.errors += 1
                break
        progress()

    if pending:
        if note := await _flush(pending):
            stats.notes.append(note)
            stats.errors += 1

    progress()
    return stats


@dataclass
class _Published:
    """What the publisher told us about its own docs, if anything."""

    corpus_url: str = ""
    docs: list[llmstxt.LlmsDoc] = field(default_factory=list)
    index: llmstxt.LlmsIndex | None = None
    digest: str = ""
    etag: str | None = None
    last_modified: str | None = None
    # The corpus answered 304 — the entire seed is unchanged in one request.
    unchanged: bool = False


async def _resolve_version(spec: dict[str, Any]) -> str | None:
    ref = versions.parse_package(spec.get("package"))
    return await versions.resolve_latest(ref) if ref else None


def _version_changed(known: dict[str, Any] | None, version: str | None) -> bool:
    """Whether a stored page describes a different release than this run does.

    Compared by **series**, not by exact version: docs are written per `major.minor`,
    so comparing patch versions would re-embed the whole index on every point release
    for no change in the text. A page stored before versioning has no version at all,
    which is a difference — it gets reindexed once, and only for seeds that declare a
    package.
    """
    if not known or not version:
        return False
    return versions.version_series(known.get("version")) != versions.version_series(
        version
    )


def _chunk_rows(
    canonical: str,
    chunks: list[str],
    *,
    url: str,
    title: str,
    seed_id: str,
    tags: list[str],
    version: str | None,
) -> list[tuple[str, str, dict[str, Any]]]:
    """Chunk texts paired with the metadata a search hit is rebuilt from."""
    crawled_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    meta: dict[str, Any] = {
        "url": url,
        "title": title,
        "seed_id": seed_id,
        "tags": tags,
        "crawled_at": crawled_at,
    }
    # Absent rather than empty when unknown: retrieval treats "no version recorded" as
    # no signal, and an empty string would compare unequal to every real version.
    if version:
        meta["version"] = version
    return [
        (webindex.doc_id(canonical, i), chunk, {**meta, "chunk_index": i})
        for i, chunk in enumerate(chunks)
    ]


async def _llms_txt(
    seed_id: str,
    spec: dict[str, Any],
    robots: RobotsCache,
    limiter: HostLimiter,
    *,
    force: bool,
    version: str | None,
) -> _Published | None:
    """Probe for the publisher's llms.txt files. None means "crawl the HTML"."""
    if spec.get("prefer_llms_txt") is False:
        return None
    starts = [str(u) for u in spec.get("start_urls") or [] if str(u).strip()]
    if not starts:
        return None

    guessed_full, guessed_index = llmstxt.llms_txt_urls(starts[0])
    full_urls = (
        [str(spec["llms_full_url"])] if spec.get("llms_full_url") else guessed_full
    )
    index_urls = (
        [str(spec["llms_txt_url"])] if spec.get("llms_txt_url") else guessed_index
    )

    for full_url in full_urls:
        corpus = await _probe(
            full_url,
            robots,
            limiter,
            conditional=_corpus_conditional(
                full_url, seed_id, force=force, version=version
            ),
            max_bytes=_MAX_CORPUS_BYTES,
        )
        if corpus is None:
            continue
        if corpus.get("not_modified"):
            return _Published(corpus_url=canonical_url(full_url), unchanged=True)
        text = str(corpus.get("text") or "")
        docs, total = llmstxt.parse_llms_full(text, full_url)
        if llmstxt.usable_as_corpus(docs, total):
            return _Published(
                corpus_url=canonical_url(full_url),
                docs=docs,
                digest=content_hash(text),
                etag=corpus.get("etag"),
                last_modified=corpus.get("last_modified"),
            )
        logger.info(
            "%s: %s has %d/%d attributable documents — falling back",
            seed_id,
            full_url,
            len(docs),
            total,
        )

    for index_url in index_urls:
        # Never conditional: the index is read for its links every run, and a 304
        # would hand back a frontier of nothing.
        listing = await _probe(index_url, robots, limiter, conditional={})
        if listing is None:
            continue
        parsed = llmstxt.parse_llms_txt(str(listing.get("text") or ""), index_url)
        if parsed.entries:
            return _Published(index=parsed)
    return None


def _corpus_conditional(
    url: str, seed_id: str, *, force: bool, version: str | None
) -> dict[str, str]:
    known = store.get_page(canonical_url(url), seed_id)
    if not known or force or _version_changed(known, version):
        return {}
    headers: dict[str, str] = {}
    if known.get("etag"):
        headers["If-None-Match"] = str(known["etag"])
    if known.get("last_modified"):
        headers["If-Modified-Since"] = str(known["last_modified"])
    return headers


async def _probe(
    url: str,
    robots: RobotsCache,
    limiter: HostLimiter,
    *,
    conditional: dict[str, str],
    max_bytes: int = _MAX_PAGE_BYTES,
) -> dict[str, Any] | None:
    """One llms.txt fetch. None means "there isn't one here", for any reason.

    A missing file is the common case and is not an error — most sites don't publish
    these yet, and a seed that doesn't have one must crawl exactly as it always did.
    """
    if not await robots.allowed(url):
        return None
    host = urlsplit(url).netloc
    await limiter.acquire(host)
    try:
        outcome = await _fetch(url, conditional, max_bytes=max_bytes)
    finally:
        limiter.release(host)

    if outcome.get("not_modified"):
        return {"not_modified": True}
    if outcome.get("gone") or outcome.get("error"):
        return None
    text = str(outcome.get("html") or "")
    # A docs site answers a missing path with its SPA shell and a 200 far more often
    # than with a 404, so the body has to be checked, not just the status.
    if not llmstxt.looks_like_markdown(text):
        return None
    return {
        "text": text,
        "etag": outcome.get("etag"),
        "last_modified": outcome.get("last_modified"),
    }


async def _ingest_corpus(
    seed_id: str,
    published: _Published,
    *,
    stats: CrawlStats,
    tags: list[str],
    version: str | None,
    force: bool,
    max_pages: int,
    on_progress: ProgressFn | None,
    is_cancelled: Callable[[], bool] | None,
) -> CrawlStats:
    """Index an `llms-full.txt` corpus: one fetch, many documents.

    Per-document hashes are kept exactly as in the HTML path, so a corpus whose one
    changed page is a typo fix still costs one embedding rather than two hundred.
    """
    stats.source = "corpus"
    stats.fetched += 1

    def progress() -> None:
        if on_progress:
            on_progress(stats.as_dict())

    if published.unchanged:
        stats.not_modified += 1
        store.touch_page(published.corpus_url, seed_id, status="corpus")
        progress()
        return stats

    # The corpus row is bookkeeping, not a page: its `corpus` status keeps it out of
    # `known_urls` (so it can never enter a frontier) and out of the page count.
    store.record_page(
        published.corpus_url,
        seed_id,
        title="llms-full.txt",
        etag=published.etag,
        last_modified=published.last_modified,
        content_hash=published.digest,
        status="corpus",
        indexed=True,
        version=version,
    )

    pending: list[tuple[str, str, dict[str, Any]]] = []
    live: set[str] = set()
    cancelled = False

    for doc in published.docs[:max_pages]:
        if is_cancelled and is_cancelled():
            stats.notes.append("cancelled")
            cancelled = True
            break

        canonical = canonical_url(doc.url)
        live.add(canonical)
        digest = content_hash(doc.text)
        known = store.get_page(canonical, seed_id)
        if (
            known
            and not force
            and not _version_changed(known, version)
            and known.get("content_hash") == digest
        ):
            stats.unchanged += 1
            store.touch_page(canonical, seed_id)
            continue

        webindex.forget_page(canonical)
        chunks = _chunk(doc.text)
        pending.extend(
            _chunk_rows(
                canonical,
                chunks,
                url=doc.url,
                title=doc.title or canonical,
                seed_id=seed_id,
                tags=tags,
                version=version,
            )
        )
        store.record_page(
            canonical,
            seed_id,
            title=doc.title,
            content_hash=digest,
            chunk_count=len(chunks),
            status="ok",
            indexed=True,
            version=version,
        )
        stats.indexed += 1
        stats.chunks += len(chunks)

        if len(pending) >= webindex.EMBED_CHUNK:
            if note := await _flush(pending):
                stats.notes.append(note)
                stats.errors += 1
                cancelled = True
                break
        progress()

    if pending:
        if note := await _flush(pending):
            stats.notes.append(note)
            stats.errors += 1
            cancelled = True

    # A corpus is the complete list of what the publisher documents, so a page that
    # left it is gone rather than merely undiscovered this run — the same
    # replace-don't-duplicate rule the Drive sync follows. Skipped after a partial
    # run, where "not in `live`" means "we stopped early", not "it was removed".
    if not cancelled:
        for url in store.known_urls(seed_id):
            key = canonical_url(url)
            if key not in live:
                webindex.forget_page(key)
                store.drop_page(key, seed_id)
                stats.skipped += 1

    progress()
    return stats


def _crawl_delay_setting() -> float:
    from backend.modules.settings.routes import get_value

    try:
        return max(0.0, float(get_value("search.crawlDelaySeconds", 1.0) or 1.0))
    except (TypeError, ValueError):
        return 1.0


def _chunk(text: str) -> list[str]:
    from backend.modules.library.chunking import chunk_text
    from backend.modules.settings.routes import get_value

    return chunk_text(text, size=int(get_value("library.chunkSize", 1000) or 1000))


async def _fetch(
    url: str, conditional: dict[str, str], *, max_bytes: int = _MAX_PAGE_BYTES
) -> dict[str, Any]:
    """One guarded, conditional GET. Returns an outcome dict, never raises."""
    import httpx

    from backend.modules.browser.fetch import UnsafeUrlError, _fetch_guarded

    try:
        final_url, resp = await asyncio.wait_for(
            _fetch_guarded(
                url,
                accept=("html", "xml", "text"),
                max_bytes=max_bytes,
                user_agent=USER_AGENT,
                headers=conditional or None,
            ),
            timeout=_FETCH_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        return {"error": "timed out"}
    except UnsafeUrlError as exc:
        return {"error": f"blocked: {exc}"}
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (404, 410):
            return {"gone": True}
        return {"error": f"HTTP {exc.response.status_code}"}
    except httpx.HTTPError as exc:
        return {"error": str(exc)}

    if resp.status_code == 304:
        return {"not_modified": True, "final_url": final_url}
    return {
        "html": resp.text,
        "final_url": final_url,
        "etag": resp.headers.get("etag"),
        "last_modified": resp.headers.get("last-modified"),
    }


async def _flush(pending: list[tuple[str, str, dict[str, Any]]]) -> str | None:
    """Embed and write one buffer. Returns an error note, or None on success.

    Refuses to write hash-fallback vectors: a collection's vector width is fixed at
    creation, so one crawl run started while the embedder was down would pin
    `webindex` to 384-dim noise permanently.
    """
    from backend.modules.database.embeddings import get_embeddings
    from backend.modules.database.vectorstore import upsert_documents

    batch = list(pending)
    pending.clear()
    if not batch:
        return None

    try:
        vectors, method = await get_embeddings([text for _id, text, _meta in batch])
    except Exception as exc:  # noqa: BLE001
        return f"embedding failed: {exc}"

    if webindex.is_fallback(method):
        return (
            "the embedding provider is unavailable — stopping rather than writing "
            "hash-fallback vectors, which would permanently pin the index"
        )
    if len(vectors) != len(batch):
        return "embedding returned the wrong number of vectors"

    dim = len(vectors[0])
    if drift := webindex.drift_error(method, dim):
        return drift

    rows = [
        (doc_id, text, {**meta, "embed_model": method}, vector)
        for (doc_id, text, meta), vector in zip(batch, vectors)
    ]
    try:
        await asyncio.to_thread(upsert_documents, webindex.COLLECTION, rows)
    except Exception as exc:  # noqa: BLE001
        return f"index write failed: {exc}"

    webindex.note_build(method, dim, webindex.status().get("docs", 0))
    return None

"""The search layer that is actually ours: fan-out, fuse, fetch, rerank.

A raw provider call is a commodity — Tavily, Brave and Serper will each hand back ten
plausible links. What makes results *good* is what happens around that call, and this
is where it happens:

1. **rewrite** one question into a few differently-worded queries, because keyword
   engines are brittle about phrasing and the model knows synonyms the user didn't use;
2. **fan out** across every configured provider in parallel, so an independent index
   (Brave), a Google reseller (Serper), a neural index (Exa) and our own crawl each
   get a vote;
3. **canonicalize and dedupe**, so the same article shared six ways is one result;
4. **fuse by rank**, never by score — provider scores are incomparable units;
5. **fetch and extract** the top few through the SSRF guard, so the model reads pages
   rather than snippets;
6. **rerank** the candidates against the original question by embedding similarity,
   which is the step that punishes a keyword match on a page that isn't really about
   the question.

**Two entry points, because they have different latency budgets.** `deep_search` runs
all six stages and costs 2–8s; `quick_search` runs the fan-out only and costs well
under a second. An 8-second tool call in the middle of a chat turn is unusable, so
the interactive agent gets `quick_search` by default and research subagents get
`deep_search`. That is the whole reason there are two.

Every stage degrades rather than fails. A dead provider, a model that won't answer,
an unfetchable page and an embedder that's offline each subtract capability and add a
note; none of them raise. The notes ride out with the results so the model can tell
"nothing exists about this" apart from "your Tavily key is wrong".
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from backend.modules.search import cache
from backend.modules.search.base import (
    SearchProvider,
    SearchProviderError,
    SearchResult,
    resolve_providers,
)
from backend.modules.search.canonical import canonical_url, host_of
from backend.modules.search.fusion import fuse

logger = logging.getLogger(__name__)

# Per-stage ceilings. Every one of them is a *budget*, not an expectation: blowing
# through it costs the stage, not the search.
REWRITE_TIMEOUT_S = 8.0
PROVIDER_TIMEOUT_S = 10.0
FETCH_TIMEOUT_S = 12.0
EMBED_TIMEOUT_S = 15.0

# How much extracted text is embedded for reranking. The lead of a page decides what
# it's about; feeding a whole article in dilutes the signal with boilerplate.
_RERANK_CHARS = 2_000
# What a `search.read`/deep hit carries back to the model.
_SNIPPET_CHARS = 400
MAX_PAGE_TEXT = 8_000


@dataclass
class SearchHit:
    url: str
    title: str
    snippet: str = ""
    text: str | None = None  # extracted body, when the page was fetched
    score: float = 0.0  # fused rank score
    providers: list[str] = field(default_factory=list)
    published: str | None = None
    host: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SearchAnswer:
    query: str
    hits: list[SearchHit]
    rewrites: list[str] = field(default_factory=list)
    providers_used: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    cached: int = 0
    elapsed_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "results": [h.to_dict() for h in self.hits],
            "rewrites": self.rewrites,
            "providers_used": self.providers_used,
            "notes": self.notes,
            "cached": self.cached,
            "elapsed_ms": self.elapsed_ms,
        }


# --- stage 1: query rewriting ----------------------------------------------

_REWRITE_PROMPT = """You rewrite a search question into alternative web queries.

Return ONLY a JSON array of {n} short query strings. No prose, no markdown fence.

Rules:
- Keep every rewrite about the SAME question. Do not broaden the topic.
- Vary the vocabulary: use the terms a page answering this would actually contain,
  not the terms the asker happened to choose.
- Prefer noun phrases over questions — search engines index documents, not dialogue.
- Include proper names, versions and error strings verbatim when present.

Question: {query}"""


def _rewrite_model() -> Any:
    """The model choice for rewriting: `search.rewriteModel` over the agent's own.

    Reuses the research module's resolver so provider/endpoint/model precedence is
    identical everywhere in the app rather than reinvented per feature.
    """
    from backend.modules.research.engine import resolve_models

    lead, _sub = resolve_models(
        {
            "provider": str(_setting("search.rewriteProvider", "")),
            "model": str(_setting("search.rewriteModel", "")),
        }
    )
    return lead


def _setting(key: str, default: Any) -> Any:
    from backend.modules.settings.routes import get_value

    return get_value(key, default)


def parse_rewrites(raw: str, *, query: str, limit: int) -> list[str]:
    """Pull the rewrite list out of a model reply. Pure — testable.

    The original query is always first and is never dropped: a rewrite is an
    *addition*, and a model that decides to "improve" the question shouldn't be able
    to replace it.
    """
    import json
    import re

    out: list[str] = [query.strip()]
    match = re.search(r"\[.*\]", raw or "", re.DOTALL)
    if not match:
        return out
    try:
        parsed = json.loads(match.group(0))
    except ValueError:
        return out
    if not isinstance(parsed, list):
        return out

    seen = {out[0].lower()}
    for item in parsed:
        text = " ".join(str(item).split())
        if not text or len(text) > 300 or text.lower() in seen:
            continue
        seen.add(text.lower())
        out.append(text)
        if len(out) >= limit:
            break
    return out


async def rewrite_queries(query: str, n: int) -> tuple[list[str], list[str]]:
    """`(queries, notes)` — the original plus up to `n-1` rewrites."""
    if n <= 1:
        return [query], []
    try:
        import httpx

        from backend.modules.agent import providers as P

        choice = _rewrite_model()
        async with httpx.AsyncClient(timeout=REWRITE_TIMEOUT_S) as client:
            result = await asyncio.wait_for(
                P.chat(
                    client,
                    choice.info,
                    choice.endpoint,
                    choice.model,
                    [
                        {
                            "role": "user",
                            "content": _REWRITE_PROMPT.format(n=n - 1, query=query),
                        }
                    ],
                    [],
                ),
                timeout=REWRITE_TIMEOUT_S,
            )
    except Exception as exc:  # noqa: BLE001 — rewriting is an optimization
        logger.info("query rewriting unavailable: %s", exc)
        return [query], [f"query rewriting skipped ({type(exc).__name__})"]

    return parse_rewrites(result.content, query=query, limit=n), []


# --- stage 2: provider fan-out ---------------------------------------------


async def _call_provider(
    provider: SearchProvider,
    query: str,
    *,
    limit: int,
    site: str | None,
    freshness: str | None,
    semaphore: asyncio.Semaphore,
    use_cache: bool,
) -> tuple[str, list[SearchResult], bool, str | None]:
    """`(provider_id, results, was_cached, error)`. Never raises."""
    key = cache.result_key(
        provider.id, query, limit=limit, site=site, freshness=freshness
    )
    if use_cache and (cached := cache.get_results(key)) is not None:
        return provider.id, [SearchResult(**row) for row in cached], True, None

    async with semaphore:
        try:
            results = await asyncio.wait_for(
                provider.search(query, limit=limit, site=site, freshness=freshness),
                timeout=PROVIDER_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            return provider.id, [], False, f"{provider.id}: timed out"
        except SearchProviderError as exc:
            return provider.id, [], False, str(exc)
        except Exception as exc:  # noqa: BLE001 — a provider bug is not a search failure
            logger.exception("provider %s raised", provider.id)
            return provider.id, [], False, f"{provider.id}: {type(exc).__name__}: {exc}"

    if use_cache and results:
        cache.put_results(key, provider.id, query, [asdict(r) for r in results])
    return provider.id, results, False, None


# --- stage 5: fetch + extract ----------------------------------------------


async def fetch_page(url: str, *, use_cache: bool = True) -> dict[str, Any]:
    """Extracted article text for one URL, through the SSRF guard and the page cache.

    This is the read half of search, shared by `search.read`, the deep pipeline's
    fetch stage, and the research subagents' `fetch_page` tool — one implementation,
    one cache, one guarded egress path.
    """
    canonical = canonical_url(url)
    if use_cache and (hit := cache.get_page(canonical)) is not None:
        return {
            "url": hit.final_url,
            "title": hit.title,
            "author": hit.author,
            "text": hit.text[:MAX_PAGE_TEXT],
            "truncated": len(hit.text) > MAX_PAGE_TEXT,
            "cached": True,
        }

    from backend.modules.browser.fetch import fetch_readable

    article = await asyncio.wait_for(fetch_readable(url), timeout=FETCH_TIMEOUT_S)
    if use_cache:
        cache.put_page(
            canonical,
            final_url=article.url,
            title=article.title or "",
            author=article.author or "",
            text=article.text or "",
        )
    return {
        "url": article.url,
        "title": article.title,
        "author": article.author,
        "text": (article.text or "")[:MAX_PAGE_TEXT],
        "truncated": len(article.text or "") > MAX_PAGE_TEXT,
        "cached": False,
    }


async def _fetch_into(hits: list[SearchHit], *, use_cache: bool) -> list[str]:
    """Fill `hit.text` for each hit, in parallel. Returns notes for what failed."""
    semaphore = asyncio.Semaphore(4)
    notes: list[str] = []

    async def one(hit: SearchHit) -> None:
        async with semaphore:
            try:
                page = await fetch_page(hit.url, use_cache=use_cache)
            except Exception as exc:  # noqa: BLE001 — snippet-only is a fine outcome
                notes.append(f"couldn't read {host_of(hit.url)}: {type(exc).__name__}")
                return
        hit.text = page["text"]
        if page.get("title") and len(str(page["title"])) > len(hit.title):
            hit.title = str(page["title"])[:300]
        if not hit.snippet and hit.text:
            hit.snippet = " ".join(hit.text.split())[:_SNIPPET_CHARS]

    await asyncio.gather(*(one(h) for h in hits))
    return notes


# --- stage 6: embedding rerank ---------------------------------------------


async def rerank_ranking(
    query: str, hits: list[SearchHit]
) -> tuple[list[str], str | None]:
    """A ranked list of URLs by embedding similarity to `query`, plus a skip note.

    **Refuses to rank on hash-fallback embeddings.** When the embedding provider is
    down, `get_embeddings` degrades to a deterministic hash — cosine over those is
    noise, and feeding noise into the fusion would actively *worsen* a ranking that
    was fine without it. Same guard symdex applies before indexing.
    """
    if len(hits) < 2:
        return [], None

    from backend.modules.database.embeddings import get_embeddings

    texts = [
        (h.text or f"{h.title}\n{h.snippet}")[:_RERANK_CHARS] or h.title for h in hits
    ]
    try:
        vectors, method = await asyncio.wait_for(
            get_embeddings([query, *texts]), timeout=EMBED_TIMEOUT_S
        )
    except Exception as exc:  # noqa: BLE001 — reranking is additive
        return [], f"rerank skipped ({type(exc).__name__})"

    if method.startswith("local-fallback"):
        return [], "rerank skipped (embedding provider unavailable)"
    if len(vectors) != len(hits) + 1:
        return [], "rerank skipped (unexpected embedding count)"

    query_vec, doc_vecs = vectors[0], vectors[1:]
    scored = sorted(
        zip(hits, doc_vecs),
        key=lambda pair: _cosine(query_vec, pair[1]),
        reverse=True,
    )
    return [hit.url for hit, _vec in scored], None


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity without numpy — these lists are a few hundred floats and a
    handful of documents, so the import isn't worth it."""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


# --- the pipeline -----------------------------------------------------------


async def deep_search(
    query: str,
    *,
    limit: int = 8,
    fanout: int | None = None,
    fetch_top: int = 5,
    rerank: bool = True,
    providers: list[str] | None = None,
    site: str | None = None,
    freshness: str | None = None,
    use_cache: bool = True,
) -> SearchAnswer:
    """The full pipeline. See the module docstring for the six stages."""
    started = time.perf_counter()
    query = " ".join((query or "").split())
    if not query:
        return SearchAnswer(query="", hits=[], notes=["empty query"])

    chosen, notes = resolve_providers(providers)
    if not chosen:
        return SearchAnswer(
            query=query,
            hits=[],
            notes=notes,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )

    if fanout is None:
        fanout = max(1, int(_setting("search.fanout", 3) or 1))
    queries, rewrite_notes = await rewrite_queries(query, fanout)
    notes.extend(rewrite_notes)

    semaphore = asyncio.Semaphore(max(1, int(_setting("search.concurrency", 6) or 6)))
    calls = [
        _call_provider(
            provider,
            q,
            limit=limit,
            site=site,
            freshness=freshness,
            semaphore=semaphore,
            use_cache=use_cache,
        )
        for provider in chosen
        for q in queries
    ]
    outcomes = await asyncio.gather(*calls)

    # Each (provider, rewrite) pair is one ranked list — that's the unit of fusion.
    ranked_lists: list[list[str]] = []
    merged: dict[str, SearchHit] = {}
    cached_count = 0
    used: list[str] = []

    for provider_id, results, was_cached, error in outcomes:
        if error:
            notes.append(error)
            continue
        cached_count += 1 if was_cached else 0
        if results and provider_id not in used:
            used.append(provider_id)

        ranked: list[str] = []
        for result in results:
            key = canonical_url(result.url)
            if not key:
                continue
            ranked.append(key)
            hit = merged.get(key)
            if hit is None:
                merged[key] = SearchHit(
                    url=result.url,
                    title=result.title,
                    snippet=result.snippet,
                    published=result.published,
                    providers=[provider_id],
                    host=host_of(result.url),
                )
                continue
            # Same page from a second provider: keep the richer metadata and record
            # the agreement, which is what fusion rewards.
            if provider_id not in hit.providers:
                hit.providers.append(provider_id)
            if len(result.title) > len(hit.title):
                hit.title = result.title
            if len(result.snippet) > len(hit.snippet):
                hit.snippet = result.snippet
            hit.published = hit.published or result.published
        ranked_lists.append(ranked)

    if not merged:
        return SearchAnswer(
            query=query,
            hits=[],
            rewrites=queries[1:],
            providers_used=used,
            notes=notes,
            cached=cached_count,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )

    fused = fuse(ranked_lists)
    ordered = sorted(merged.items(), key=lambda kv: fused.get(kv[0], 0.0), reverse=True)
    hits = [hit for _key, hit in ordered]

    # Fetch and rerank only the head — the tail is what the ranking already rejected.
    head = hits[: max(fetch_top, limit)]
    if fetch_top > 0:
        notes.extend(await _fetch_into(head[:fetch_top], use_cache=use_cache))

    if rerank:
        embed_ranking, note = await rerank_ranking(query, head)
        if note:
            notes.append(note)
        elif embed_ranking:
            # Added as one more ranked list rather than blended into the score, so
            # the embedding vote is weighted like every other voter.
            fused = fuse([*ranked_lists, [canonical_url(u) for u in embed_ranking]])

    final = sorted(merged.items(), key=lambda kv: fused.get(kv[0], 0.0), reverse=True)
    out: list[SearchHit] = []
    for key, hit in final[:limit]:
        hit.score = round(fused.get(key, 0.0), 6)
        out.append(hit)

    return SearchAnswer(
        query=query,
        hits=out,
        rewrites=queries[1:],
        providers_used=used,
        notes=notes,
        cached=cached_count,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
    )


async def quick_search(
    query: str,
    *,
    limit: int = 8,
    providers: list[str] | None = None,
    site: str | None = None,
    freshness: str | None = None,
    use_cache: bool = True,
) -> SearchAnswer:
    """Fan-out and fusion only — no rewriting, no fetching, no reranking.

    The interactive default. Sub-second, so it can sit inside a chat turn without the
    user watching a spinner.
    """
    return await deep_search(
        query,
        limit=limit,
        fanout=1,
        fetch_top=0,
        rerank=False,
        providers=providers,
        site=site,
        freshness=freshness,
        use_cache=use_cache,
    )

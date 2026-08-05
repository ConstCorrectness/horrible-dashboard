"""The documentation sources behind the popup, and the chain that walks them.

Three, deliberately different in what they know:

- **kernel** — Jupyter's `inspect_request` against the *live* notebook kernel. The
  only source that knows what `df` actually is, because it asks the namespace the
  user is working in. Useless when nothing is running, which is why it is first
  rather than only.
- **index** — the symdex `code_symbols` projection: signatures and docstrings for
  installed packages, the stdlib, and this workspace's own files. Offline, instant,
  and answers for a symbol that has never been imported.
- **web** — the open web through the existing search pipeline plus the SSRF-guarded
  reader. Answers for prose that isn't in any docstring (guides, changelogs), at the
  cost of a network round trip.

The chain stops at the first source that answers, rather than merging all three:
three renderings of the same function is not three times the information, and the
order already expresses which one to believe when they disagree — the live object
beats the index, and the index beats a search result.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from backend.modules.docs.models import DocEntry, DocLookupContext, DocSourceId

logger = logging.getLogger(__name__)

#: Cap on a doc body handed to a tooltip. A full pandas docstring is ~40 KB and
#: nobody reads it in a popup; the truncation is marked so it doesn't read as the
#: end of the text.
MAX_BODY_CHARS = 6000

#: ANSI colour codes. IPython renders `inspect_reply` text/plain *for a terminal*,
#: so it arrives full of escape sequences that would otherwise show up as literal
#: `[0;31m` in the popup.
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def _truncate(text: str) -> str:
    if len(text) <= MAX_BODY_CHARS:
        return text
    return text[:MAX_BODY_CHARS].rstrip() + "\n\n… (truncated)"


# --- kernel -----------------------------------------------------------------


def _split_signature(body: str) -> tuple[str, str]:
    """Pull a leading `Signature: …` / `Init signature: …` line out of IPython's
    plain-text inspect output so it can be rendered as the popup's header.

    Everything else is left alone: IPython's layout (Docstring/File/Type sections)
    is already the most readable arrangement of what it knows, and reformatting it
    here would just be a worse version of it.
    """
    lines = body.splitlines()
    for i, line in enumerate(lines[:3]):
        if line.startswith(("Signature:", "Init signature:")):
            sig = line.split(":", 1)[1].strip()
            return sig, "\n".join(lines[:i] + lines[i + 1 :]).strip()
    return "", body


async def lookup_kernel(
    symbol: str, context: DocLookupContext | None
) -> list[DocEntry]:
    from backend.modules.notebook.manager import notebook_manager

    if context is None or not context.notebook_path:
        return []
    session = notebook_manager.session_for(f"nb:{context.notebook_path}")
    if session is None:
        return []

    # `inspect_request` takes code plus a cursor, not a name. When the caller has
    # only a symbol (the editor, say), the symbol *is* a valid one-expression
    # program, so a cursor at its end resolves the same thing.
    code = context.code if context.code is not None else symbol
    cursor = context.cursor_pos if context.cursor_pos is not None else len(code)
    if not code:
        return []

    # Off the event loop: `inspect` blocks on the worker thread's reply queue. A
    # plain `to_thread` is safe here *because* it only waits on a `queue.Queue` —
    # every zmq call still happens on the session's own worker thread.
    reply: dict[str, Any] = await asyncio.to_thread(session.inspect, code, cursor, 0)
    if reply.get("status") != "ok" or not reply.get("found"):
        return []
    text = str(reply.get("data", {}).get("text/plain", ""))
    if not text.strip():
        return []
    signature, body = _split_signature(strip_ansi(text))
    return [
        DocEntry(
            source="kernel",
            title=symbol,
            signature=signature,
            body=_truncate(body),
        )
    ]


# --- index ------------------------------------------------------------------


async def lookup_index(symbol: str, lang: str) -> list[DocEntry]:
    from backend.modules.lsp import symbol_store

    # The leaf name is what the index is keyed on; a dotted path narrows it. For
    # `pandas.DataFrame.merge` that means asking for `merge` and preferring a hit
    # whose module matches the prefix, rather than getting nothing at all.
    parts = symbol.split(".")
    leaf = parts[-1]
    if not leaf:
        return []
    rows = await asyncio.to_thread(symbol_store.query, lang, leaf, 12, None)
    if not rows:
        return []

    exact = [r for r in rows if r.get("symbol") == leaf]
    candidates = exact or rows

    # Rank by how much of the dotted prefix the row's module/import path accounts
    # for, **component by component**. Substring matching looks equivalent and is
    # not: `pandas.DataFrame.merge` has the prefix `pandas.DataFrame`, which does
    # not appear anywhere in the real module `pandas.core.frame`, so a substring
    # test scores the right row zero and hands back whichever unrelated `merge`
    # happened to sort first.
    prefix_parts = {p for p in parts[:-1] if p}
    if prefix_parts:

        def overlap(row: dict[str, Any]) -> int:
            path = f"{row.get('module') or ''}.{row.get('imp') or ''}"
            return len(prefix_parts & set(path.split(".")))

        best = max(overlap(r) for r in candidates)
        if best:
            candidates = [r for r in candidates if overlap(r) == best]

    row = candidates[0]
    doc = str(row.get("doc") or "")
    detail = str(row.get("detail") or "")
    module = str(row.get("module") or "")
    if not doc and not detail:
        return []
    return [
        DocEntry(
            source="index",
            title=f"{module}.{row.get('symbol')}" if module else str(row.get("symbol")),
            signature=detail,
            body=_truncate(doc),
        )
    ]


# --- web --------------------------------------------------------------------


async def lookup_web(symbol: str, lang: str) -> list[DocEntry]:
    """Search for a doc page and read it through the guarded fetcher.

    Deliberately built on the search module rather than on a hardcoded URL scheme
    per package: `docs.python.org/3/library/<mod>.html` works for the stdlib and
    for nothing else, and guessing a readthedocs URL for an arbitrary package is a
    404 generator. The one URL contract that *is* reliable — that a search engine
    can find a library's documentation — is the one already implemented here, with
    the egress guard already on every result URL.
    """
    from backend.modules.browser.fetch import fetch_readable
    from backend.modules.search.pipeline import quick_search

    query = f"{symbol} {lang} documentation"
    try:
        answer = await quick_search(query, limit=5)
    except Exception:  # noqa: BLE001 — no search configured is a normal outcome
        logger.debug("docs web lookup: search failed for %r", symbol, exc_info=True)
        return []
    hits = getattr(answer, "hits", []) or []
    if not hits:
        return []

    hit = hits[0]
    url = getattr(hit, "url", "")
    if not url:
        return []
    try:
        article = await fetch_readable(url)
    except Exception:  # noqa: BLE001 — an unreachable or blocked page is not an error
        logger.debug("docs web lookup: fetch failed for %s", url, exc_info=True)
        # The snippet is still better than nothing, and it cost nothing extra.
        snippet = str(getattr(hit, "snippet", "") or "")
        if not snippet:
            return []
        return [
            DocEntry(
                source="web",
                title=str(getattr(hit, "title", url)),
                body=snippet,
                url=url,
            )
        ]
    text = str(getattr(article, "text", "") or "")
    if not text.strip():
        return []
    return [
        DocEntry(
            source="web",
            title=str(getattr(article, "title", "") or getattr(hit, "title", url)),
            body=_truncate(text),
            url=url,
        )
    ]


# --- the chain --------------------------------------------------------------


async def resolve_docs(
    symbol: str,
    sources: list[DocSourceId],
    context: DocLookupContext | None,
    lang: str,
) -> tuple[list[DocEntry], list[DocSourceId], list[str]]:
    """Walk `sources` in order, stopping at the first that answers.

    Returns `(entries, tried, notes)`. `tried` names every source consulted, so a
    caller can distinguish "nothing has documentation for this" from "no source was
    enabled" — the second is a configuration problem and must not be reported as
    the first.
    """
    tried: list[DocSourceId] = []
    notes: list[str] = []
    for source in sources:
        tried.append(source)
        try:
            if source == "kernel":
                entries = await lookup_kernel(symbol, context)
            elif source == "index":
                entries = await lookup_index(symbol, lang)
            elif source == "web":
                entries = await lookup_web(symbol, lang)
            else:
                notes.append(f"unknown source {source!r}")
                continue
        except Exception:  # noqa: BLE001 — one broken source must not sink the rest
            logger.exception("docs source %s failed for %r", source, symbol)
            notes.append(f"{source} failed")
            continue
        if entries:
            return entries, tried, notes
    return [], tried, notes

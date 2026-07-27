"""The search-provider contract and the process-global provider registry.

A provider is anything that turns a query string into ranked URLs: a metered API
(Tavily, Brave, Exa, Serper), a self-hosted metasearch instance (SearXNG), a keyless
HTML scrape (DuckDuckGo), or this node's own crawled index. They all satisfy one
Protocol, so the pipeline fans out across whichever are configured and fuses the
results without knowing which is which — and swapping vendors is a settings change,
not a code change.

**Egress rules, which differ by leg and are the thing to get right:**

- *Provider API calls* go out over plain `httpx` to hardcoded hostnames. Not
  attacker-influenced, and the same thing every existing connector does.
- *The SearXNG base URL* also goes over plain `httpx`, and loopback is deliberately
  allowed — a local instance at `127.0.0.1:8888` is the normal deployment, and it is
  exactly what the SSRF guard exists to block. It is trusted because it comes from a
  *setting*. It must never be taken from model output or a tool argument.
- *Every result URL and every crawled URL* goes through `_fetch_guarded`. A search
  engine can return literally any URL, so that leg is the SSRF sink and has no
  exceptions.

Protocol rather than ABC, matching the duck-typed training `EnvironmentProvider`: a
backend plugin can contribute a provider (`host.add_search_provider`) without
importing a base class from here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchResult:
    """One result from one provider, before fusion."""

    url: str
    title: str
    snippet: str = ""
    # The provider's own relevance number, kept for debugging and for thresholding
    # *within* a single provider. NOT comparable across providers — Tavily's 0.83 and
    # Exa's 0.83 are different units, which is why fusion is rank-based (see
    # fusion.py). Nothing downstream may compare this field across providers.
    score: float | None = None
    published: str | None = None  # ISO-8601 when the provider supplies one
    provider: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


class SearchProviderError(RuntimeError):
    """A provider failed for this query. Caught per-provider by the fan-out, so one
    dead provider degrades the result set instead of failing the search."""


@runtime_checkable
class SearchProvider(Protocol):
    """What the pipeline needs from anything that can answer a query.

    `id` doubles as the settings suffix and the secret suffix (`search:<id>`), so it
    must be a stable lowercase slug.
    """

    id: str
    label: str
    needs_key: bool

    def configured(self) -> bool:
        """Whether this provider can run right now (key present, URL set, index
        non-empty). Must not raise and must not do network I/O — it is called on
        every search and on every render of the providers list."""
        ...

    async def search(
        self,
        query: str,
        *,
        limit: int = 8,
        site: str | None = None,
        freshness: str | None = None,
    ) -> list[SearchResult]:
        """Ranked results, best first. Raises `SearchProviderError` on failure.

        `site` restricts to one domain; `freshness` is a coarse recency hint
        (`day`|`week`|`month`|`year`) that providers map onto their own vocabulary or
        ignore.
        """
        ...


# --- registry ---------------------------------------------------------------
#
# Process-global, mirroring `registry.training_providers`. Built-ins register at
# module import via `register_builtin_providers()`; plugins via `host.add_search_provider`.

_providers: dict[str, SearchProvider] = {}


def register_provider(provider: SearchProvider) -> None:
    """Add or replace a provider. Later registrations win, so a plugin can override a
    built-in by reusing its id."""
    _providers[provider.id] = provider


def get_provider(provider_id: str) -> SearchProvider | None:
    return _providers.get(provider_id)


def all_providers() -> list[SearchProvider]:
    return list(_providers.values())


def available_providers() -> list[SearchProvider]:
    """Every provider that could answer a query right now."""
    out: list[SearchProvider] = []
    for provider in _providers.values():
        try:
            if provider.configured():
                out.append(provider)
        except Exception:  # noqa: BLE001 — a broken `configured` must not break search
            logger.exception("provider %s configured() failed", provider.id)
    return out


def _reason_unavailable(provider: SearchProvider) -> str:
    if provider.needs_key:
        return f"{provider.id}: no API key (add one in the Search connector)"
    if provider.id == "searxng":
        return "searxng: no instance URL (set search.searxngUrl)"
    if provider.id == "crawl":
        return "crawl: the focused index is empty (run a crawl first)"
    return f"{provider.id}: not configured"


def resolve_providers(
    names: list[str] | None = None,
) -> tuple[list[SearchProvider], list[str]]:
    """The providers to actually query, plus human-readable notes about the rest.

    Never raises and never returns an empty list silently: the notes explain what was
    skipped and why, and they ride out in the tool result so the model can route
    around a missing key instead of concluding the web is empty. That's the same
    "a degraded tool result beats a crashed run" contract the research subagent tools
    have always had.
    """
    notes: list[str] = []

    if names:
        chosen: list[SearchProvider] = []
        for name in names:
            provider = _providers.get(name)
            if provider is None:
                notes.append(f"{name}: unknown provider")
            elif not provider.configured():
                notes.append(_reason_unavailable(provider))
            else:
                chosen.append(provider)
        if chosen:
            return chosen, notes
        notes.append("falling back to whatever is configured")

    ready = available_providers()
    ready_ids = {p.id for p in ready}
    for provider in _providers.values():
        if provider.id not in ready_ids:
            notes.append(_reason_unavailable(provider))

    if not ready:
        notes.append(
            "no search provider is available — add an API key in the Search "
            "connector, set search.searxngUrl, or check network access"
        )
    return ready, notes


def auto_provider_ids() -> list[str]:
    """The default fan-out set: every configured provider except the DDG scrape,
    which is a last resort rather than a peer.

    DDG is keyless and always "configured", so including it unconditionally would
    mean every search pays for a fragile HTML scrape even when three good providers
    are available. It stays in only when it's the only thing left.
    """
    ready = [p.id for p in available_providers()]
    without_ddg = [pid for pid in ready if pid != "ddg"]
    return without_ddg or ready

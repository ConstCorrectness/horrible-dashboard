"""Per-provider API-key custody. The only place a search key is read or written.

**Why not settings.** `GET /api/settings` hands the whole settings bag to the
browser, so a key stored there would be readable by any page the app renders. Keys go
to the Fernet-encrypted `secrets.db` under `search:<provider>`, and are never echoed
back — not in a form prefill, not in a status payload, not in an error message.

**Precedence is env → stored**, matching `connectors/config.py`. An operator who pins
`SEARCH_TAVILY_API_KEY` means it, so the UI must not silently shadow it with a stored
value that will never be read: an env-pinned field renders with an explanatory `help`
line and its submitted value is discarded rather than persisted.

The provider *choice* is an ordinary setting (`search.provider`) — a vendor's name is
public. Only the key is secret. Keeping that line clear is what lets the provider
list render in the browser at all.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Providers that authenticate with a single API key. SearXNG (URL, not key), DDG
# (keyless) and the local crawl index are deliberately absent.
KEYED_PROVIDERS: tuple[str, ...] = ("tavily", "brave", "exa", "serper")

LABELS: dict[str, str] = {
    "tavily": "Tavily",
    "brave": "Brave Search",
    "exa": "Exa",
    "serper": "Serper",
}

# Where to get one, shown as the field's help text when no key is stored.
SIGNUP_HINTS: dict[str, str] = {
    "tavily": "Free tier at tavily.com — purpose-built for agents, answers included.",
    "brave": "Free tier at brave.com/search/api — an independent index, not a Google scraper.",
    "exa": "exa.ai — neural/semantic search, good for 'find me things like this'.",
    "serper": "serper.dev — cheapest per query; Google results, no extraction.",
}


def _secret_key(provider: str) -> str:
    return f"search:{provider}"


def env_var(provider: str) -> str:
    return f"SEARCH_{provider.upper()}_API_KEY"


def key_from_env(provider: str) -> bool:
    return bool(os.environ.get(env_var(provider), ""))


def get_key(provider: str) -> str | None:
    """The effective key for a provider: environment first, then the encrypted store.

    Returns None rather than an empty string so callers can use a plain truthiness
    check without an empty stored value looking like a configured provider.
    """
    from backend.modules.database.secrets_store import get_secret_or_none

    if pinned := os.environ.get(env_var(provider), "").strip():
        return pinned
    try:
        stored = get_secret_or_none(_secret_key(provider))
    except Exception:  # noqa: BLE001 — an unreadable key is "no key", not a crash
        logger.exception("couldn't read the stored key for %s", provider)
        return None
    return stored.strip() or None if stored else None


def set_key(provider: str, value: str) -> None:
    """Persist a key. No-op when the environment pins one — storing a value that will
    never be read is worse than doing nothing, because it looks like it worked."""
    from backend.modules.database.secrets_store import upsert_secret

    if key_from_env(provider):
        return
    upsert_secret(_secret_key(provider), value.strip())


def clear_keys() -> int:
    """Forget every stored key. Returns how many were removed. Env-pinned keys are
    untouched — this module doesn't own the environment."""
    from backend.modules.database.secrets_store import delete_secret

    removed = 0
    for provider in KEYED_PROVIDERS:
        try:
            if delete_secret(_secret_key(provider)):
                removed += 1
        except Exception:  # noqa: BLE001 — best-effort teardown
            logger.exception("couldn't clear the stored key for %s", provider)
    return removed


def configured_providers() -> list[str]:
    """Keyed providers that have a usable key, in declaration order."""
    return [p for p in KEYED_PROVIDERS if get_key(p)]

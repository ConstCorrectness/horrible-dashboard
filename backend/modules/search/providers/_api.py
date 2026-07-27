"""Shared HTTP plumbing for the metered search providers.

These calls go to hardcoded vendor hostnames over plain `httpx`, deliberately *not*
through `_fetch_guarded`. The guard exists to stop attacker-chosen URLs reaching
internal addresses; a constant like `api.tavily.com` is neither attacker-chosen nor
internal, and every existing connector (`github.py`, `drive_api.py`, …) talks to its
vendor exactly this way. The guard's job starts at the *result* URLs, which the
pipeline fetches — see `base.py`'s egress table.

Every failure becomes `SearchProviderError` so the fan-out can drop one provider and
keep the rest. Nothing here retries: the pipeline queries several providers at once,
so a retry costs latency on the critical path to salvage a result the others already
covered.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.modules.search.base import SearchProviderError

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 10.0


async def request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
    provider: str = "search",
) -> Any:
    """One JSON call to a provider API, or `SearchProviderError`.

    HTTP error bodies are surfaced (truncated) rather than swallowed: "401
    Unauthorized" tells the user their key is wrong, which is the single most common
    thing to go wrong here, and a bare "provider failed" would hide it.
    """
    import httpx

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            res = await client.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json_body,
            )
    except httpx.TimeoutException as exc:
        raise SearchProviderError(f"{provider}: timed out after {timeout:g}s") from exc
    except httpx.HTTPError as exc:
        raise SearchProviderError(f"{provider}: {exc}") from exc

    if res.status_code >= 400:
        detail = (res.text or "").strip()[:200]
        raise SearchProviderError(
            f"{provider}: HTTP {res.status_code}{f' — {detail}' if detail else ''}"
        )
    try:
        return res.json()
    except ValueError as exc:
        raise SearchProviderError(f"{provider}: unreadable JSON response") from exc


def clean(text: Any, *, limit: int = 500) -> str:
    """Collapse whitespace and cap length. Provider snippets arrive with newlines,
    stray tabs and occasional HTML entities; the model reads them better flat."""
    if not text:
        return ""
    return " ".join(str(text).split())[:limit]

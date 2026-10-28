"""A read-only, cached client for Neuronpedia's public API.

Neuronpedia hosts what a local SAE cannot provide on its own: human and automated
*explanations* of each feature, its top activating examples across a large corpus,
and the logits it promotes and suppresses. A feature lookup is a GET by public id
(`/api/feature/{model}/{source}/{index}`) and sends nothing about the user.

Two rules:

- **Cached in `app.db`** (`neuronpedia_cache`, a week). Feature pages do not change
  by the minute, a feature list re-renders constantly, and a hosted API is not a
  thing to hammer from a hover.
- **Nothing of the user's leaves without consent.** The endpoints that take text —
  testing activations on a prompt, steering, NLA explanations, generating a graph —
  send the user's own words to a third party. They go through
  `require_text_allowed()`, which refuses unless `interp.neuronpedia.allowText` is on.
  None exist yet; the gate is here so the first one cannot be written without it.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Generator
from urllib.parse import quote

import httpx

from backend.modules.database.app_db import ensure_app_db_dir

BASE = "https://www.neuronpedia.org"
TTL_SECONDS = 7 * 24 * 3600
#: Top activating examples kept per feature: enough for a card, a fraction of the
#: payload (each example carries every token of its context).
MAX_EXAMPLES = 8


class TextNotAllowed(Exception):
    """A request would send the user's text to Neuronpedia, and that is off."""


def require_text_allowed() -> None:
    from backend.modules.settings.routes import get_value

    if not get_value("interp.neuronpedia.allowText", False):
        raise TextNotAllowed(
            "This sends your text to neuronpedia.org. Turn on "
            "interp.neuronpedia.allowText to allow it."
        )


@contextmanager
def _db() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(str(ensure_app_db_dir()))
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS neuronpedia_cache (
                url TEXT PRIMARY KEY, body TEXT NOT NULL, fetched_at REAL NOT NULL
            )"""
        )
        yield conn
        conn.commit()
    finally:
        conn.close()


async def get_json(
    path: str, *, client: httpx.AsyncClient | None = None
) -> tuple[Any, bool]:
    """GET `path` from Neuronpedia, through the cache. Returns `(body, cached)`."""
    url = f"{BASE}{path}"
    now = time.time()
    with _db() as conn:
        row = conn.execute(
            "SELECT body, fetched_at FROM neuronpedia_cache WHERE url = ?", (url,)
        ).fetchone()
    if row and now - row[1] < TTL_SECONDS:
        return json.loads(row[0]), True
    owns = client is None
    client = client or httpx.AsyncClient(timeout=30, follow_redirects=True)
    try:
        res = await client.get(url, headers={"Accept": "application/json"})
        res.raise_for_status()
        body = res.json()
    finally:
        if owns:
            await client.aclose()
    with _db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO neuronpedia_cache (url, body, fetched_at) VALUES (?, ?, ?)",
            (url, json.dumps(body), now),
        )
    return body, False


def _example(raw: dict[str, Any]) -> dict[str, Any]:
    tokens = list(raw.get("tokens") or [])
    values = list(raw.get("values") or [])
    peak = int(
        raw.get("maxValueTokenIndex") or (values.index(max(values)) if values else 0)
    )
    # A window around the peak: the card shows *why* it fired, not 128 tokens.
    lo, hi = max(0, peak - 12), min(len(tokens), peak + 6)
    return {
        "tokens": tokens[lo:hi],
        "values": values[lo:hi],
        "peak": peak - lo,
        "maxValue": raw.get("maxValue"),
    }


async def feature(model: str, source: str, index: int | str) -> dict[str, Any]:
    """One feature's page, trimmed to what a card shows."""
    path = f"/api/feature/{quote(model)}/{quote(source)}/{quote(str(index))}"
    body, cached = await get_json(path)
    if not isinstance(body, dict):
        return {"error": "Neuronpedia returned no feature", "cached": cached}
    return {
        "model": body.get("modelId"),
        "source": body.get("layer"),
        "index": body.get("index"),
        "hook": body.get("hookName"),
        "explanations": [
            {
                "text": e.get("description"),
                "by": e.get("typeName") or e.get("explanationModelName"),
            }
            for e in body.get("explanations") or []
            if e.get("description")
        ],
        "promotes": body.get("pos_str") or [],
        "suppresses": body.get("neg_str") or [],
        "maxActivation": body.get("maxActApprox"),
        "density": body.get("frac_nonzero"),
        "examples": [
            _example(a) for a in (body.get("activations") or [])[:MAX_EXAMPLES]
        ],
        "url": f"{BASE}/{model}/{source}/{index}",
        "cached": cached,
    }

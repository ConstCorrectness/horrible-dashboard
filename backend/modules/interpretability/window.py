"""What context window the loaded model *actually* has.

`requestedNumCtx` is what we ask for; this is what we get. They differ more often
than you would expect, and the gap is the difference between "my prompt fit" and
"my prompt was silently truncated" — which is the one question the pane's budget
bar exists to answer, and it is unanswerable without a denominator.

Only the server knows, and **every server answers somewhere different**. The `/model`
route already asks Ollama; asking Ollama alone would have made this dead code on the
most common local setup here, which is LM Studio (an OpenAI-dialect server that has
never heard of `/api/show`). So the probe branches on the provider *kind*, not just
the dialect:

| kind       | asked                      | read                                  |
|------------|----------------------------|---------------------------------------|
| `ollama`   | `POST /api/show`           | the `*.context_length` metadata key   |
| `llamacpp` | `GET /props`               | `default_generation_settings.n_ctx`   |
| `lmstudio` | `GET /api/v0/models/{id}`  | `loaded_context_length`, else max     |
| `vllm`     | `GET /v1/models`           | that model's `max_model_len`          |

A hosted `litellm` provider is deliberately not probed: the window is the remote
model's business, no endpoint of ours reports it, and a guess here would be rendered
as a measurement.

Two properties this file has to keep, because it runs in `run_agent_loop`'s `finally`
on **every** turn:

* **It never raises.** Every caller is an observer of a turn that has already
  produced its answer.
* **It is cached and short-timeout.** Unbounded, an unreachable server would add its
  connect timeout to the tail of every single turn.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from backend.modules.interpretability.tokenizer import context_length_from_show

logger = logging.getLogger(__name__)

# Short on purpose: this is a footnote to a turn that is already finished, and a
# slow answer is worth less than a fast None.
_TIMEOUT_S = 2.0

# How long an answer — including "this server won't say" — stays good. Not
# unbounded: a model reloaded at a different `n_ctx` is a normal thing to do in LM
# Studio or llama.cpp, and a stale denominator is the exact lie this module exists
# to stop telling.
_TTL_S = 300.0

# (endpoint, model) -> (answer, expires_at). Negative answers are cached too — a
# server that has no such endpoint will not grow one within the TTL.
_cache: dict[tuple[str, str], tuple[int | None, float]] = {}


def reset_cache() -> None:
    _cache.clear()


async def context_length(info: Any, endpoint: str, model: str) -> int | None:
    """The model's true context window, or None if this provider won't say."""
    base = (endpoint or "").rstrip("/")
    if not base or not model:
        return None
    key = (base, model)
    hit = _cache.get(key)
    now = time.monotonic()
    if hit is not None and hit[1] > now:
        return hit[0]
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            value = await _probe(client, info, base, model)
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        logger.debug("interpretability: context probe failed for %s (%s)", model, exc)
        value = None
    _cache[key] = (value, now + _TTL_S)
    return value


async def _probe(
    client: httpx.AsyncClient, info: Any, base: str, model: str
) -> int | None:
    kind = str(getattr(info, "kind", ""))
    dialect = str(getattr(info, "dialect", ""))

    if dialect == "ollama":
        res = await client.post(f"{base}/api/show", json={"model": model})
        res.raise_for_status()
        return context_length_from_show(res.json())

    if kind == "llamacpp":
        res = await client.get(f"{base}/props")
        res.raise_for_status()
        settings = res.json().get("default_generation_settings") or {}
        return _as_int(settings.get("n_ctx"))

    if kind == "lmstudio":
        res = await client.get(f"{base}/api/v0/models/{model}")
        res.raise_for_status()
        data = res.json()
        # `loaded_context_length` is what this instance was actually loaded with and
        # `max_context_length` is what the weights allow. Prefer the former: asking
        # for more than you loaded gets you what you loaded, silently.
        return _as_int(data.get("loaded_context_length")) or _as_int(
            data.get("max_context_length")
        )

    if kind == "vllm":
        res = await client.get(f"{base}/v1/models")
        res.raise_for_status()
        for entry in res.json().get("data") or []:
            if entry.get("id") == model:
                return _as_int(entry.get("max_model_len"))
    return None


def _as_int(value: Any) -> int | None:
    """Positive ints only. A 0 or a string here means the server declined to say,
    and passing it through would render as a budget bar over a window of zero."""
    return value if isinstance(value, int) and value > 0 else None

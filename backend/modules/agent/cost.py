"""What a round of inference cost, in dollars.

Three sources, in a strict order of trust, because conflating them is how a cost
readout becomes confidently wrong:

1. **The provider's own figure.** litellm prices the call it just made, against the
   model that actually served it, with a table that ships with the package. Nothing
   we compute can beat that.
2. **The bundled price table**, overlaid with the `agent.modelPricing` setting. An
   estimate, and a table in a git repo goes stale the week a vendor changes prices —
   which is exactly why the setting exists and why this ranks below (1).
3. **Zero, for a model running on hardware nobody bills for.**

Everything else is `None`, and `None` is not zero. A local run costs nothing and an
unpriced hosted model costs an amount we do not know; rendering both as `$0.00` — or
as blank — loses the distinction at the only point a human would notice it. The pane
prints `free` for the first and nothing at all for the second.
"""

from __future__ import annotations

import fnmatch
import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PRICING_PATH = Path(__file__).with_name("pricing.json")

#: Provider kinds that run on hardware the user already owns (or a peer's, which
#: bills them and not us). These cost a *known* zero.
#:
#: `nim` is deliberately absent: it is the same OpenAI dialect whether it is a local
#: container or NVIDIA's hosted API, and we cannot tell which from here. Calling a
#: metered API free is a worse error than admitting we do not know.
LOCAL_KINDS = frozenset({"ollama", "lmstudio", "llamacpp", "vllm", "peer"})


@lru_cache(maxsize=1)
def _bundled() -> dict[str, dict[str, float]]:
    try:
        raw = json.loads(PRICING_PATH.read_text(encoding="utf-8"))
        return dict(raw.get("prices") or {})
    except Exception:  # noqa: BLE001 - a broken table must not break a turn
        logger.warning("agent: could not read pricing.json", exc_info=True)
        return {}


def _overrides() -> dict[str, Any]:
    """The user's `agent.modelPricing`, tolerated in either shape.

    Accepts a JSON object or a JSON string holding one, because a settings field
    typed as text round-trips as a string and silently matching nothing would look
    exactly like a price that simply is not listed.
    """
    from backend.modules.settings.routes import get_value

    raw = get_value("agent.modelPricing", None)
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return {}
        try:
            raw = json.loads(raw)
        except ValueError:
            logger.warning("agent: agent.modelPricing is not valid JSON; ignoring")
            return {}
    return raw if isinstance(raw, dict) else {}


def _strip_prefix(model: str) -> str:
    """`anthropic/claude-sonnet-4` -> `claude-sonnet-4`.

    litellm needs a routing prefix on some ids (`ProviderInfo.model_prefix`); the
    price table is keyed by the bare model, so a prefixed id would match nothing.
    """
    return model.rsplit("/", 1)[-1] if "/" in model else model


def price_for(model: str) -> dict[str, float] | None:
    """Per-1M-token prices for a model, or None when it is not in any table.

    The **longest matching pattern wins**, so `claude-sonnet-4-5-20260101` takes a
    dated entry over the `claude-sonnet-4*` family it also matches. First-match-wins
    would make the result depend on dict ordering, which is a silent way for an
    override to be ignored.
    """
    if not model:
        return None
    table = {**_bundled(), **_overrides()}
    bare = _strip_prefix(model).lower()
    best: tuple[int, dict[str, float]] | None = None
    for pattern, entry in table.items():
        if not isinstance(entry, dict):
            continue
        if bare == pattern.lower() or fnmatch.fnmatch(bare, pattern.lower()):
            if best is None or len(pattern) > best[0]:
                best = (len(pattern), entry)
    return best[1] if best else None


def estimate(model: str, tokens_in: int | None, tokens_out: int | None) -> float | None:
    """Cost in USD from the price table, or None when it cannot be known.

    A missing token count on a priced model still yields a number — the half we do
    know — rather than None: "we counted the prompt but not the completion" is much
    closer to the truth than "we have no idea".
    """
    prices = price_for(model)
    if prices is None:
        return None
    if tokens_in is None and tokens_out is None:
        return None
    total = 0.0
    for count, key in ((tokens_in, "in"), (tokens_out, "out")):
        if count is None:
            continue
        try:
            total += (float(prices.get(key, 0.0)) * count) / 1_000_000
        except (TypeError, ValueError):
            return None
    return total


def resolve(usage: Any, *, model: str, provider_kind: str) -> float | None:
    """The cost of one round: provider figure, then table, then local-zero, else None.

    `usage` is a `providers.Usage` or None. Typed loosely to keep this module free of
    an import cycle — `providers` is imported by half the agent package.
    """
    reported = getattr(usage, "cost_usd", None)
    if reported is not None:
        return float(reported)

    priced = estimate(
        model,
        getattr(usage, "tokens_in", None),
        getattr(usage, "tokens_out", None),
    )
    if priced is not None:
        return priced

    if provider_kind in LOCAL_KINDS:
        # Known zero, not unknown. This is the branch that makes `free` honest.
        return 0.0
    return None

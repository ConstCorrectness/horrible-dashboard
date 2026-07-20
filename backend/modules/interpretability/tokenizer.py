"""Exact token counting for the interpretability pane.

The pane's whole value is telling you *how much* of the context window each piece
of the prompt actually costs, so an estimate that drifts 20% is worse than useless
— it would quietly under-report a prompt that is in fact overflowing `num_ctx`.
So we count with the model's real tokenizer.

`tokenizers` (Rust, ~5 MB) rather than `transformers`, deliberately: the backend env
has no torch and must not grow one (see backend/modules/training/envs.py). A bare
`tokenizer.json` is all a `Tokenizer` needs, and `huggingface-hub` is already a core
dep to fetch it.

**Gemma's tokenizer repo is gated** — anonymous downloads 401. Resolution order:
  1. the `interpretability.tokenizerRepo` setting, if set (an ungated mirror works)
  2. the Hugging Face connector's token, if that connector is connected
  3. anonymous (fine for ungated repos: Qwen, Mistral, Llama mirrors, …)

If every path fails we fall back to a chars/4 estimate and mark the result
`exact=False`. The pane **must** surface that flag — an estimate rendered as a
precise number is the failure mode this module exists to prevent.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Ollama model tags ("gemma4:e2b", "qwen3:8b-instruct-q4_K_M") aren't HF repo ids.
# Map the families we can as a LAST resort — see the warning on `repo_for_model`
# about why a family match is not the same thing as the right tokenizer.
_REPO_BY_FAMILY: tuple[tuple[str, str], ...] = (
    ("gemma", "google/gemma-2-2b-it"),
    ("qwen", "Qwen/Qwen2.5-7B-Instruct"),
    ("llama", "meta-llama/Llama-3.1-8B-Instruct"),
    ("mistral", "mistralai/Mistral-7B-Instruct-v0.3"),
    ("phi", "microsoft/Phi-3.5-mini-instruct"),
    ("deepseek", "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B"),
)

# An HF repo id: "owner/name", no Ollama tag separator. LM Studio reports models
# under exactly this form ("google/gemma-4-12b-qat"), so its model id IS the repo —
# the one case where we can be sure the tokenizer matches the running weights.
_REPO_ID = re.compile(r"^[\w.-]+/[\w.-]+$")

# How a tokenizer was chosen, in descending order of trust. This rides all the way
# out to the pane because it changes what the numbers mean:
#   model  — derived from the model's own id; the tokenizer matches the weights
#   setting— the user pinned it; assumed deliberate
#   family — a same-family default, possibly the WRONG GENERATION (Gemma 2 and 3
#            don't share a vocab), so counts are close but not authoritative
#   none   — no tokenizer at all; chars/4
TokenizerSource = str

# Loaded tokenizers, keyed by repo id. A Tokenizer is a few MB resident and
# entirely reusable across turns, so we never load one twice per process.
_CACHE: dict[str, Any] = {}
# Repos we already failed to load. Without this every single round would re-attempt
# a network fetch that we know 401s, and the capture path runs inside the turn.
_FAILED: set[str] = set()


def repo_for_model(
    model: str, configured: str = ""
) -> tuple[str | None, TokenizerSource]:
    """The HF repo to count `model` with, and how confident we are in the choice.

    Order: the user's setting, then the model id itself when it's already a repo id
    (LM Studio), then a same-family default.

    The family fallback is deliberately reported as `"family"` rather than folded in
    with the rest. A same-family tokenizer of the wrong generation produces numbers
    that look precise and are quietly wrong — Gemma 2 and Gemma 3 don't share a
    vocabulary, so counting a Gemma 3/4 model with the Gemma 2 tokenizer is a real
    error, not a rounding one. The pane labels it `approx` so nobody budgets against it.
    """
    if configured.strip():
        return configured.strip(), "setting"
    if _REPO_ID.match(model.strip()):
        return model.strip(), "model"
    name = model.lower()
    for family, repo in _REPO_BY_FAMILY:
        if family in name:
            return repo, "family"
    return None, "none"


async def hf_token() -> str | None:
    """The Hugging Face connector's access token, if that connector is connected.

    Shared with `architecture.py`, which needs the same token for the same reason:
    Gemma's repos are gated, so both `tokenizer.json` and `config.json` 401 without
    one. Imported lazily and defensively — the connector is optional, and neither
    lookup may ever be what breaks an agent turn.
    """
    try:
        from backend.modules.connectors.providers import huggingface

        return await huggingface.token()
    except Exception:
        return None


async def _load(repo: str) -> Any | None:
    """Fetch + cache `repo`'s tokenizer. Returns None if it can't be had."""
    if repo in _CACHE:
        return _CACHE[repo]
    if repo in _FAILED:
        return None
    try:
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer

        path = hf_hub_download(
            repo_id=repo, filename="tokenizer.json", token=await hf_token()
        )
        tok = Tokenizer.from_file(path)
    except Exception as exc:
        # Gated repo without a token, offline, no such file — all the same to us.
        logger.info("interpretability: no tokenizer for %s (%s); estimating", repo, exc)
        _FAILED.add(repo)
        return None
    _CACHE[repo] = tok
    return tok


def estimate(text: str) -> int:
    """Chars/4, the standard rough ratio for English + code. Only ever used when a
    real tokenizer is unavailable, and always reported as `exact=False`."""
    return max(1, round(len(text) / 4)) if text else 0


class Counter:
    """Counts tokens for one model, exactly if it can and approximately if it must.

    Built once per captured turn (`await Counter.create(model)`) so the tokenizer
    resolves a single time, then called synchronously per message — the capture path
    runs inside the agent loop and must not await per string.
    """

    def __init__(
        self, tokenizer: Any | None, repo: str | None, source: TokenizerSource = "none"
    ) -> None:
        self._tok = tokenizer
        self.repo = repo
        # How the repo was chosen — "model" / "setting" / "family" / "none". Falls
        # back to "none" whenever the load failed, so `source` never claims a
        # provenance for a tokenizer we don't actually have.
        self.source: TokenizerSource = source if tokenizer is not None else "none"
        # Exact means: we have a tokenizer AND it belongs to this model. A
        # same-family stand-in is not exact (see repo_for_model).
        self.exact = tokenizer is not None and source in ("model", "setting")

    @classmethod
    async def create(cls, model: str, configured_repo: str = "") -> Counter:
        repo, source = repo_for_model(model, configured_repo)
        return cls(await _load(repo) if repo else None, repo, source)

    def count(self, text: str) -> int:
        if not text:
            return 0
        if self._tok is None:
            return estimate(text)
        try:
            return len(self._tok.encode(text, add_special_tokens=False).ids)
        except Exception:
            return estimate(text)

    def count_json(self, value: Any) -> int:
        """Tokens for a structure serialized the way a provider sends it. Tool
        schemas reach the model as JSON, so their cost is the JSON's cost — this is
        the number that makes a tool list's true context weight visible."""
        import json

        try:
            return self.count(json.dumps(value, separators=(",", ":")))
        except (TypeError, ValueError):
            return self.count(str(value))


def reset_cache() -> None:
    """Drop cached tokenizers and failure memos — lets a test, or a user who just
    connected Hugging Face, retry a repo that previously 401'd."""
    _CACHE.clear()
    _FAILED.clear()


# Ollama reports context length under a few different keys depending on family
# ("gemma2.context_length", "llama.context_length", …). One regex beats a lookup
# table we'd have to keep chasing.
_CTX_KEY = re.compile(r"\.context_length$")


def context_length_from_show(info: dict[str, Any]) -> int | None:
    """The model's true context window from Ollama's `/api/show` payload — the
    denominator for the budget bar. Distinct from the `num_ctx` we *request*: asking
    for more than the model has silently gets you the model's real limit, and seeing
    both side by side is exactly the kind of surprise this pane should surface."""
    details = info.get("model_info") or {}
    for key, value in details.items():
        if _CTX_KEY.search(str(key)) and isinstance(value, int):
            return value
    return None

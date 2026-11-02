"""Exact token counting for the interpretability pane.

The pane's whole value is telling you *how much* of the context window each piece
of the prompt actually costs, so an estimate that drifts 20% is worse than useless
— it would quietly under-report a prompt that is in fact overflowing `num_ctx`.
So we count with the model's real tokenizer.

`tokenizers` (Rust, ~5 MB) rather than `transformers`, deliberately: the backend env
has no torch and must not grow one (see backend/modules/training/envs.py). A bare
`tokenizer.json` is all a `Tokenizer` needs, and `huggingface-hub` is already a core
dep to fetch it.

Which repo: the `interpretability.modelRepo` setting, else the repo the running
GGUF's own provenance names (`repo_from_weights`), else the model id if it's shaped
like a repo, else a same-family default — see `resolve_repo`.

**Gemma's tokenizer repo is gated** — anonymous downloads 401. Auth order:
  1. the `interpretability.tokenizerRepo` setting, if set (an ungated mirror works)
  2. the Hugging Face connector's token, if that connector is connected
  3. anonymous (fine for ungated repos: Qwen, Mistral, Llama mirrors, …)

If every path fails we fall back to a chars/4 estimate and mark the result
`exact=False`. The pane **must** surface that flag — an estimate rendered as a
precise number is the failure mode this module exists to prevent.
"""

from __future__ import annotations

import asyncio
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

# An HF repo id: "owner/name", no Ollama tag separator. LM Studio reports models in
# this *shape* ("google/gemma-4-12b-qat"), but the shape is all it shares with a
# repo: that id is LM Studio's own catalog key and 401s on the Hub. So an id-shaped
# model is only tried after the weights' provenance (`repo_from_weights`).
_REPO_ID = re.compile(r"^[\w.-]+/[\w.-]+$")

# How a tokenizer was chosen. This rides all the way out to the pane because it
# changes what the numbers mean:
#   setting— the user pinned it; assumed deliberate, and wins outright
#   weights— followed from the running GGUF's own identity (its source repo, its
#            name parts, or the Hub repo LM Studio downloaded it from); the
#            tokenizer matches the weights
#   model  — the model's own id, taken as a repo id
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
# Weights provenance, keyed by (model, dialect, ggufPath override). One resolution
# is a GGUF header read plus a few Hub round trips, and the capture path runs every
# round — a miss is memoized too, for the same reason `_FAILED` is.
_PROVENANCE: dict[tuple[str, str, str], str | None] = {}
# How many `base_model` links to follow before giving up. A quantization repo names
# the checkpoint it was made from, which is normally one hop; the cap is only there
# so a card cycle can't loop.
_MAX_HOPS = 3


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


async def resolve_repo(
    model: str, configured: str = "", dialect: str = ""
) -> tuple[str | None, TokenizerSource]:
    """`repo_for_model`, with the running weights' own provenance tried before the
    model id. The setting still wins outright; `dialect` only decides whether the
    weights are looked for in Ollama's store or LM Studio's."""
    if configured.strip():
        return configured.strip(), "setting"
    repo = await repo_from_weights(model, dialect)
    if repo:
        return repo, "weights"
    return repo_for_model(model)


async def repo_from_weights(model: str, dialect: str = "") -> str | None:
    """The Hub repo the running model's GGUF says it is, or None.

    Never raises: like every lookup here, this is an observer's convenience and may
    not be what breaks a turn."""
    if not model:
        return None
    override = _gguf_override()
    key = (model, dialect, override)
    if key in _PROVENANCE:
        return _PROVENANCE[key]
    try:
        token = await hf_token()
        repo = await asyncio.to_thread(
            _weights_provenance, model, dialect, override, token
        )
    except Exception as exc:
        logger.info("interpretability: no provenance for %s (%s)", model, exc)
        repo = None
    _PROVENANCE[key] = repo
    return repo


def _gguf_override() -> str:
    try:
        from backend.modules.settings.routes import _read

        value = _read().get("interpretability.ggufPath")
    except Exception:
        return ""
    return value if isinstance(value, str) else ""


def _weights_provenance(
    model: str, dialect: str, override: str, token: str | None
) -> str | None:
    """The repo of the weights themselves, from the strongest claim available:

    1. **`general.source.repo_url`** — the GGUF naming its own repo.
    2. **A `general.base_model` whose name is the file's own.** `base_model` names
       the model a GGUF was fine-tuned *from* (an `-it` file names its `-pt`
       parent), so it is only taken when its name part equals the file's own
       `basename`-`size_label`-`finetune` — the match `interp.compat` makes for
       SAEs. A plain re-quantization lists the checkpoint it was made from: itself.
    3. **The repo LM Studio downloaded the file from.** Its models directory is laid
       out `<owner>/<repo>/<file>.gguf`, mirroring the Hub. A GGUF repo has no
       `config.json`, so its card's `base_model` is followed to the checkpoint that
       does — on a Hub card, unlike in the header, a quantization repo's
       `base_model` is the model it quantized.
    4. **The file's own name under its parent's owner** (parent
       `google/gemma-3-1b-pt`, name `gemma-3-1b-it` → `google/gemma-3-1b-it`), only
       if the Hub has that repo with a `config.json`.

    Source 3 is only read for a file LM Studio's index resolved: an Ollama blob's
    parents are `models/blobs`, which is repo-*shaped* and means nothing.
    """
    from pathlib import Path

    from backend.modules.interp import compat
    from backend.modules.interpretability import gguf

    path = gguf.resolve_model_path(model, dialect, override)
    if not path:
        return None
    try:
        metadata = gguf.read_header_cached(path).metadata
    except (gguf.GgufError, OSError):
        metadata = {}
    target = compat.gguf_target(metadata, path)
    own = _hub_repo(target["repo"])
    if own:
        return own
    name = str(target["name"] or "").lower()
    parents = [r for r in map(_hub_repo, target["parents"]) if r]
    for parent in parents:
        if name and parent.split("/", 1)[1].lower() == name:
            return parent
    if dialect != "ollama" and path == gguf.resolve_lmstudio_model(model):
        folder = Path(path).parent
        candidate = f"{folder.parent.name}/{folder.name}"
        if _REPO_ID.match(candidate):
            repo = _follow_base_model(candidate, token)
            if repo:
                return repo
    if name:
        for owner in dict.fromkeys(p.split("/", 1)[0] for p in parents):
            info = _model_info(f"{owner}/{name}", token)
            if info is not None and _has_config(info):
                return info.id
    return None


def _hub_repo(value: Any) -> str | None:
    """`https://huggingface.co/google/gemma-3-1b-it` → `google/gemma-3-1b-it`, or
    None. `compat._repo` without the lowercasing: this one is handed to the Hub."""
    if not isinstance(value, str) or not value:
        return None
    if "huggingface.co/" in value:
        value = value.split("huggingface.co/", 1)[1]
    repo = "/".join([p for p in value.strip("/").split("/") if p][:2])
    return repo if _REPO_ID.match(repo) else None


def _model_info(repo: str, token: str | None) -> Any | None:
    """The Hub's record for `repo`, or None — a 404 here is an answer, not an error."""
    try:
        from huggingface_hub import model_info

        return model_info(repo, token=token)
    except Exception as exc:
        logger.info("interpretability: no Hub record for %s (%s)", repo, exc)
        return None


def _has_config(info: Any) -> bool:
    return any(s.rfilename == "config.json" for s in info.siblings or [])


def _follow_base_model(repo: str, token: str | None) -> str | None:
    """Walk `base_model` links from `repo` to the first repo with a `config.json`.

    Stops at an ambiguous link (a merge lists several bases; picking one would be a
    guess), at a card that calls the link anything but a quantization (a fine-tune's
    base is its parent — the header's trap again), and at anything that isn't an
    `owner/name` id."""
    seen: set[str] = set()
    for _ in range(_MAX_HOPS):
        if repo in seen:
            return None
        seen.add(repo)
        info = _model_info(repo, token)
        if info is None:
            return None
        if _has_config(info):
            # `info.id` is the canonical casing; the directory may differ.
            return info.id
        card = info.card_data or {}
        relation = card.get("base_model_relation")
        if relation and relation != "quantized":
            return None
        base = card.get("base_model")
        if isinstance(base, list):
            base = base[0] if len(base) == 1 else None
        if not isinstance(base, str) or not _REPO_ID.match(base):
            return None
        repo = base
    return None


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
        # How the repo was chosen — "setting" / "weights" / "model" / "family" /
        # "none". Falls back to "none" whenever the load failed, so `source` never
        # claims a provenance for a tokenizer we don't actually have.
        self.source: TokenizerSource = source if tokenizer is not None else "none"
        # Exact means: we have a tokenizer AND it belongs to this model. A
        # same-family stand-in is not exact (see repo_for_model).
        self.exact = tokenizer is not None and source in ("setting", "weights", "model")

    @classmethod
    async def create(
        cls, model: str, configured_repo: str = "", dialect: str = ""
    ) -> Counter:
        repo, source = await resolve_repo(model, configured_repo, dialect)
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
    _PROVENANCE.clear()


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

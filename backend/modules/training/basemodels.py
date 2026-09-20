"""Which model a fine-tune starts from: searching for one, and checking the name.

The base model was free text with nothing behind it. A name goes straight into
`from_pretrained(...)` in the generated cell, so every way of being wrong about it
— a typo, an Ollama tag, a repo that does not exist, one that has no weights this
stack can load — surfaced minutes into a run as a stack trace from inside
`transformers`, after the dataset had downloaded.

Two halves, and they answer different questions:

- **`search`** gives a list to pick from, so the name is *copied* rather than
  remembered. Text-generation models only, biggest first: a fine-tune's base is a
  causal LM, and offering the whole Hub means offering embedding models and
  diffusers as candidates for `SFTTrainer`.
- **`check`** judges whatever is in the box, including a name typed by hand or
  pasted from a blog post. It is **advisory** — the recipe still emits what was
  typed. A gated repo is a real answer ("accept the licence"), and being offline is
  not evidence that a model does not exist, so neither may block a run.

The spelling this exists for: `qwen3:0.6b`. That is the **Ollama** name for the
model the Hub calls `Qwen/Qwen3-0.6B`, and it is what a user who runs the model
locally will type, because it is the name the rest of this app shows them.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from backend.modules.settings.routes import get_value

logger = logging.getLogger(__name__)

#: `qwen3:0.6b`, `llama3.2:3b-instruct-q4_K_M` — an Ollama/LM Studio tag, which is
#: a model *name* plus a quantization, and never a Hub id.
_LOCAL_TAG = re.compile(r"^(?P<name>[^/\s:]+):(?P<tag>[^/\s:]+)$")

#: Architectures `AutoModelForCausalLM` cannot load, recognised so the answer is
#: the reason rather than "not found". Read off `config.json`'s `model_type`.
_NOT_CAUSAL = {
    "bert": "an encoder (BERT-family) model, which has no language-modelling head",
    "t5": "an encoder-decoder model, which SFTTrainer's causal setup cannot train",
    "clip": "a vision-text embedding model, not a text generator",
}


def _api() -> Any:
    from huggingface_hub import HfApi

    token = str(get_value("training.hf.token", "") or "") or None
    return HfApi(token=token)


def search(query: str, limit: int = 20) -> list[dict[str, Any]]:
    """Text-generation models on the Hub matching `query`, most downloaded first."""
    api = _api()
    # `pipeline_tag`, not `task`: the argument was renamed, and the old spelling
    # is a TypeError rather than a broader search. `sort="downloads"` already
    # descends in the current client, which dropped `direction`.
    found = api.list_models(
        search=query or None,
        pipeline_tag="text-generation",
        sort="downloads",
        limit=max(1, min(limit, 50)),
    )
    out: list[dict[str, Any]] = []
    for model in found:
        model_id = str(getattr(model, "id", "") or "")
        if not model_id:
            continue
        out.append(
            {
                "id": model_id,
                "downloads": int(getattr(model, "downloads", 0) or 0),
                "likes": int(getattr(model, "likes", 0) or 0),
                "url": f"https://huggingface.co/{model_id}",
                # Gated repos are listed rather than hidden: the fix is to accept
                # the licence, and a base model missing from the list with no
                # explanation is the worse failure.
                "gated": bool(getattr(model, "gated", False)),
                # By naming convention (`…-GGUF`), so the picker can caution rather
                # than hide: these are serving artifacts, and picking one as a base
                # fails inside `from_pretrained` after the download.
                "servingOnly": model_id.upper().endswith("-GGUF"),
            }
        )
    return out


def _local_tag_suggestion(name: str) -> str:
    """`qwen3:0.6b` → `Qwen/Qwen3-0.6B`-shaped guess, or "".

    A guess, and described as one wherever it is shown. The transformation is a
    real convention (Ollama lowercases a Hub name and joins the size with a colon)
    but the owner cannot be recovered from the tag, so `search` is what confirms it.
    """
    match = _LOCAL_TAG.match(name)
    if not match:
        return ""
    base, tag = match.group("name"), match.group("tag")
    return f"{base}-{tag}"


def check(base_model: str) -> list[str]:
    """Advisory warnings about `base_model`, in the recipe form's warning list.

    Never raises and never blocks: an offline node cannot tell "no such model" from
    "no network", and a recipe that refuses to generate because the Hub was
    unreachable would be worse than one whose cell fails honestly at run time.
    """
    name = (base_model or "").strip()
    if not name:
        return [
            "No base model is set, so the generated cell would train from an empty "
            "model name. Pick one — `Qwen/Qwen3-0.6B` is a small, permissive start."
        ]
    if guess := _local_tag_suggestion(name):
        return [
            f"`{name}` looks like an Ollama/LM Studio tag, not a Hugging Face id. "
            f"Training loads the model from the Hub, so it needs `owner/name` — "
            f"search for `{guess}` and pick the repo.",
        ]
    # A bare name is NOT rejected: the Hub's canonical models (`gpt2`,
    # `bert-base-uncased`) have no owner, and refusing them would be the form
    # telling the user that a name which works is wrong. The Hub decides.
    return _check_on_hub(name)


#: name -> (checked at, warnings). The recipe form is fetched on every pane open,
#: every task change and every save, and each fetch would otherwise be a network
#: round-trip to the Hub — slow when it is reachable and a stall when it is not.
#: A short TTL, because the answer does change: accepting a licence should stop the
#: gated warning without restarting the backend.
_CACHE: dict[str, tuple[float, list[str]]] = {}
_CACHE_TTL_S = 300.0


def _check_on_hub(name: str) -> list[str]:
    """What the Hub says about `name`, or nothing at all when it cannot be asked."""
    cached = _CACHE.get(name)
    if cached is not None and time.monotonic() - cached[0] < _CACHE_TTL_S:
        return list(cached[1])
    result = _ask_hub(name)
    _CACHE[name] = (time.monotonic(), list(result))
    return result


def _ask_hub(name: str) -> list[str]:
    try:
        info = _api().model_info(name)
    except Exception as exc:  # noqa: BLE001 — offline is not evidence of absence
        text = str(exc)
        kind = type(exc).__name__
        # Checked before the auth codes, and worded to cover both: the Hub answers
        # a missing repo and a private one the same way on purpose, and its own
        # 404 text mentions "private or gated" — so branching on the word `gated`
        # called every typo a licence problem.
        if kind == "RepositoryNotFoundError" or "404" in text:
            return [
                f"`{name}` was not found on the Hub — either the name is wrong, or "
                "the repo is private and this node has no `training.hf.token`. The "
                "generated cell loads exactly this string."
            ]
        if kind == "GatedRepoError" or "401" in text or "403" in text:
            return [
                f"`{name}` is gated. Accept its licence on the Hub and set "
                "`training.hf.token`, or the run stops at the download."
            ]
        logger.info("base-model check skipped for %s (%s)", name, exc)
        return []
    out: list[str] = []
    if getattr(info, "gated", False):
        out.append(
            f"`{name}` is gated: accept its licence on the Hub and set "
            "`training.hf.token`, or the download will fail partway into the run."
        )
    config = getattr(info, "config", None) or {}
    model_type = str(config.get("model_type") or "")
    if reason := _NOT_CAUSAL.get(model_type):
        out.append(
            f"`{name}` is {reason}. A fine-tune here needs a causal language model."
        )
    files = {str(getattr(f, "rfilename", "")) for f in getattr(info, "siblings", [])}
    if files and not any(
        f.endswith((".safetensors", ".bin")) for f in files if "/" not in f
    ):
        if any(f.endswith(".gguf") for f in files):
            out.append(
                f"`{name}` holds GGUF files, which are for serving with llama.cpp. "
                "Training needs the original weights — usually the repo this one "
                "was quantized from."
            )
        else:
            out.append(
                f"`{name}` has no weight files this stack can load (no "
                "`.safetensors` or `.bin` at the top level)."
            )
    return out

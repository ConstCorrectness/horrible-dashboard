"""Pairing a draft model with a target: is it compatible, and does it fit?

Speculative decoding runs a small model ahead of a large one and has the large one
verify its guesses in a single pass. Two things have to be true, and each fails in
its own unhelpful way if unchecked.

**The vocabularies must match.** The draft proposes *token ids*, and the target
verifies them as ids. Two models with different tokenizers agree on almost nothing,
so the acceptance rate collapses to near zero -- which does not error, it just
makes generation *slower* than not using a draft at all, while looking like it
worked. So `check_compatible` **refuses** rather than warns: a warning on a feature
whose only symptom is "mysteriously slower" will be ignored.

**Both models must be resident.** `hardware.defaults().gpu_layers` is 0-or-999 and
deliberately stays that way -- every caller depends on that number, and this is the
only place that needs to divide a card between two models. `spec_plan` does that
division here rather than generalizing `defaults()` into a budgeter.

It inherits `layer_plan`'s honesty about what it does not know: that function
reports weights and KV cache and explicitly declines to invent a compute-buffer
fudge factor, so **headroom is a caller-supplied parameter here too**, not a
constant baked in where it would be wrong on some machine and trusted on all of
them.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from backend.modules.interpretability import gguf
from backend.modules.llamacpp.offload import layer_plan

logger = logging.getLogger(__name__)

#: Compared between target and draft. `model` is the tokenizer family (`gpt2`,
#: `llama`, `spm`); `pre` is the pre-tokenizer regex set, which differs between
#: model families sharing a family name and changes tokenization outright.
_TOKENIZER_KEYS = ("tokenizer.ggml.model", "tokenizer.ggml.pre")

#: Special ids must line up: the draft emitting a different EOS means it and the
#: target disagree about where a sequence ends.
_SPECIAL_KEYS = ("tokenizer.ggml.bos_token_id", "tokenizer.ggml.eos_token_id")


def _vocab_fingerprint(metadata: dict[str, Any]) -> str | None:
    """A cheap hash over the token list.

    Size alone is not enough: two 128k-token vocabularies can be entirely
    different, and comparing them element-wise on every check would be slow. The
    header is already read, so hashing the list costs one pass and turns the
    comparison into a string equality.
    """
    tokens = metadata.get("tokenizer.ggml.tokens")
    if not isinstance(tokens, list) or not tokens:
        return None
    digest = hashlib.sha256()
    digest.update(str(len(tokens)).encode("utf-8"))
    # A sample rather than the whole list: 128k tokens hashed on every catalogue
    # render is real time, and a stride across the range catches a reordered or
    # substituted vocabulary just as reliably as hashing all of it.
    stride = max(1, len(tokens) // 512)
    for index in range(0, len(tokens), stride):
        token = tokens[index]
        digest.update(str(token).encode("utf-8", "replace"))
        digest.update(b"\x00")
    return digest.hexdigest()[:16]


def _vocab_size(metadata: dict[str, Any]) -> int | None:
    tokens = metadata.get("tokenizer.ggml.tokens")
    if isinstance(tokens, list):
        return len(tokens)
    arch = str(metadata.get("general.architecture") or "")
    value = metadata.get(f"{arch}.vocab_size") if arch else None
    return int(value) if isinstance(value, int) else None


def _describe(path: str | Path) -> dict[str, Any] | None:
    try:
        header = gguf.read_header(path)
    except (OSError, gguf.GgufError, ValueError) as exc:
        logger.info("speculative: cannot read %s (%s)", path, exc)
        return None
    meta = header.metadata
    return {
        "path": str(header.path),
        "arch": str(meta.get("general.architecture") or ""),
        "tokenizer": {key: meta.get(key) for key in _TOKENIZER_KEYS},
        "special": {key: meta.get(key) for key in _SPECIAL_KEYS},
        "vocabSize": _vocab_size(meta),
        "fingerprint": _vocab_fingerprint(meta),
    }


def check_compatible(target_path: str | Path, draft_path: str | Path) -> dict[str, Any]:
    """Whether `draft_path` may be used as a draft for `target_path`.

    Returns `{compatible, reason, detail}`. `compatible` is False whenever we could
    not establish that it *is* -- an unreadable header is a refusal, not a pass,
    because the failure mode of getting this wrong is silent.
    """
    target = _describe(target_path)
    draft = _describe(draft_path)
    if target is None or draft is None:
        which = "target" if target is None else "draft"
        return {
            "compatible": False,
            "reason": f"could not read the {which} GGUF header",
            "detail": {},
        }

    if Path(target["path"]).resolve() == Path(draft["path"]).resolve():
        return {
            "compatible": False,
            "reason": "the draft and the target are the same file",
            "detail": {},
        }

    detail = {"target": target, "draft": draft}

    for key in _TOKENIZER_KEYS:
        if target["tokenizer"].get(key) != draft["tokenizer"].get(key):
            return {
                "compatible": False,
                "reason": (
                    f"tokenizers differ ({key}: "
                    f"{target['tokenizer'].get(key)!r} vs "
                    f"{draft['tokenizer'].get(key)!r})"
                ),
                "detail": detail,
            }

    if target["vocabSize"] != draft["vocabSize"]:
        return {
            "compatible": False,
            "reason": (
                f"vocabulary sizes differ ({target['vocabSize']} vs "
                f"{draft['vocabSize']})"
            ),
            "detail": detail,
        }

    if (
        target["fingerprint"]
        and draft["fingerprint"]
        and target["fingerprint"] != draft["fingerprint"]
    ):
        return {
            "compatible": False,
            "reason": "vocabularies are the same size but hold different tokens",
            "detail": detail,
        }

    for key in _SPECIAL_KEYS:
        t_val, d_val = target["special"].get(key), draft["special"].get(key)
        if t_val is not None and d_val is not None and t_val != d_val:
            return {
                "compatible": False,
                "reason": f"{key} differs ({t_val} vs {d_val})",
                "detail": detail,
            }

    return {"compatible": True, "reason": "", "detail": detail}


def find_drafts(target_path: str | Path, *, limit: int = 12) -> list[dict[str, Any]]:
    """Catalogue entries that could serve as a draft for `target_path`.

    Compatibility is decided by reading headers, never by name heuristics: two
    files whose names share a prefix routinely have different tokenizers, and a
    name-matched pair that is not actually compatible is exactly the silent
    slowdown this module exists to prevent.

    Candidates are ordered smallest-first, because a draft only pays for itself if
    it is much cheaper than the target.
    """
    from backend.modules.llamacpp.catalog import list_models

    target = Path(target_path).resolve()
    out: list[dict[str, Any]] = []
    for model in sorted(list_models(), key=lambda m: m.size_bytes or 0):
        try:
            if Path(model.path).resolve() == target:
                continue
        except OSError:
            continue
        verdict = check_compatible(target_path, model.path)
        if not verdict["compatible"]:
            continue
        out.append(
            {"path": model.path, "name": model.name, "sizeBytes": model.size_bytes}
        )
        if len(out) >= limit:
            break
    return out


def spec_plan(
    target_path: str | Path,
    draft_path: str | Path,
    *,
    vram_mb: int | None,
    context: int,
    headroom_mb: int = 1024,
) -> dict[str, Any]:
    """How many layers of each model fit on the card.

    `headroom_mb` is the caller's, not ours. `layer_plan` reports weights and KV
    only and says so; a compute-buffer allowance depends on the build and the batch
    size, so inventing one here would produce a number that is wrong on some
    machines and trusted on all of them.

    Returns `{targetGpuLayers, draftGpuLayers, reason, fits}`. When the inputs are
    not knowable -- an unrecognised quantization, no VRAM figure -- it says so and
    offers **no** plan rather than a guess, because a guess here is an OOM at load.
    """
    target = layer_plan(target_path)
    draft = layer_plan(draft_path)
    for plan, label in ((target, "target"), (draft, "draft")):
        if plan.get("error"):
            return _no_plan(f"could not measure the {label}: {plan['error']}")
        if not plan.get("complete"):
            return _no_plan(
                f"the {label} contains a quantization we cannot size, so any "
                "budget would be a floor rather than a total"
            )

    if not vram_mb:
        return _no_plan(
            "no VRAM figure for this machine, so there is nothing to divide"
        )

    budget = (vram_mb - headroom_mb) * 1024 * 1024
    if budget <= 0:
        return _no_plan(f"headroom ({headroom_mb} MB) exceeds the card's memory")

    draft_total = _model_bytes(draft, context)
    target_total = _model_bytes(target, context)

    # The draft goes on the card whole or not at all. Splitting a model that is
    # already small across host and device costs a PCIe round trip per token on
    # the very path whose cheapness is the entire premise of drafting.
    if draft_total > budget:
        return _no_plan(
            "the draft model does not fit alongside anything, so drafting would "
            "cost more than it saves"
        )

    remaining = budget - draft_total
    if target_total <= remaining:
        return {
            "targetGpuLayers": 999,
            "draftGpuLayers": 999,
            "fits": True,
            "reason": "both models fit on the card",
            "targetBytes": target_total,
            "draftBytes": draft_total,
            "budgetBytes": budget,
        }

    # Partial offload of the target: fit whole layers, cheapest first.
    kv_per_layer = _kv_bytes_per_layer(target, context)
    fitted = 0
    used = target.get("overheadBytes", 0)
    if used > remaining:
        used = 0  # the output tensors only move once every block has
    for size in target["layerBytes"]:
        step = size + kv_per_layer
        if used + step > remaining:
            break
        used += step
        fitted += 1

    return {
        "targetGpuLayers": fitted,
        "draftGpuLayers": 999,
        "fits": False,
        "reason": (
            f"the draft fits whole; {fitted} of {target['layerCount']} target "
            "layers fit alongside it"
        ),
        "targetBytes": target_total,
        "draftBytes": draft_total,
        "budgetBytes": budget,
    }


def _model_bytes(plan: dict[str, Any], context: int) -> int:
    kv = plan.get("kvBytesPerToken") or 0
    return int(plan.get("totalBytes", 0)) + int(kv) * max(0, context)


def _kv_bytes_per_layer(plan: dict[str, Any], context: int) -> int:
    layers = plan.get("layerCount") or 0
    kv = plan.get("kvBytesPerToken") or 0
    if not layers or not kv:
        return 0
    return int(kv * max(0, context) / layers)


def _no_plan(reason: str) -> dict[str, Any]:
    return {
        "targetGpuLayers": None,
        "draftGpuLayers": None,
        "fits": False,
        "reason": reason,
    }

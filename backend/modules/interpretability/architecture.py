"""What the loaded model actually *is*, normalized enough to draw.

Two sources, same concepts under different names:

* **Ollama** — `/api/show` returns the GGUF key/value metadata llama.cpp wrote at
  conversion time (`gemma2.block_count`, `llama.attention.head_count_kv`, …). The
  keys are namespaced by architecture, so they're matched by suffix rather than
  by a table we'd have to chase per family.
* **Hugging Face** — a repo's `config.json` (`num_hidden_layers`,
  `num_key_value_heads`, …). Richer than GGUF for MoE and normalization details,
  and the only option for OpenAI-dialect servers (LM Studio, vLLM), which expose
  no architecture endpoint at all.

Everything lands in one `ModelArchitecture` so the diagram doesn't care which it
came from — but `source` rides along, because "we read this off the running
weights" and "we read this off a repo you pointed us at" are different claims.

**Fields we can't confirm stay `None` and are simply not drawn.** A diagram that
invents a plausible-looking number is worse than one with a gap: the whole point of
this module is that you can trust what it shows you.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from backend.modules.interpretability.models import (
    AttentionSpec,
    FfnSpec,
    ModelArchitecture,
    MoeSpec,
)

logger = logging.getLogger(__name__)

# Whether the FFN is gated (SwiGLU/GeGLU: two up-projections multiplied, not one)
# is a real structural difference in the diagram, so it's answered from explicit
# lists rather than inferred — `hidden_act: silu` alone doesn't tell you, since a
# non-gated FFN can use the same activation.
#
# Three-state on purpose: True for known-gated, False for known-dense, and **None
# for a family in neither list**. "We don't know" and "we know it isn't" are
# different claims, and only one of them justifies drawing a single-projection FFN.
_GATED_FAMILIES = {
    "llama",
    "gemma",
    "gemma2",
    "gemma3",
    "gemma4",
    "mistral",
    "mixtral",
    "qwen2",
    "qwen2_moe",
    "qwen3",
    "qwen3_moe",
    "phi3",
    "deepseek_v2",
    "deepseek_v3",
    "olmo2",
    "starcoder2",
}

# Classic non-gated FFNs: one up-projection through an activation, then down.
_DENSE_FFN_FAMILIES = {
    "gpt2",
    "gpt_neox",
    "gptj",
    "bloom",
    "opt",
    "falcon",
    "mpt",
    "bert",
    "roberta",
}


def _is_gated(family: str | None) -> bool | None:
    """True / False / None — see the note on `_GATED_FAMILIES`.

    A multimodal repo names its language tower with a suffix (`gemma3_text`), which
    is the same architecture as `gemma3` for FFN purposes — so the bare name is
    tried too rather than falling through to "unknown".
    """
    if not family:
        return None
    name = family.lower()
    for candidate in (name, name.removesuffix("_text")):
        if candidate in _GATED_FAMILIES:
            return True
        if candidate in _DENSE_FFN_FAMILIES:
            return False
    return None


def _first_suffix(info: dict[str, Any], suffix: str) -> Any:
    """A GGUF value by key suffix — `block_count` matches `gemma2.block_count`.

    Suffix matching rather than `{arch}.{key}` lookup because the architecture
    prefix in the metadata does not always equal `general.architecture`, and a
    lookup table of per-family prefixes is exactly the maintenance we don't want.
    """
    for key, value in info.items():
        if str(key).endswith("." + suffix):
            return value
    return None


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _attention_kind(heads: int | None, kv_heads: int | None) -> str:
    """Multi-head, grouped-query, or multi-query — read off the head counts.

    The distinction is the single biggest driver of KV-cache size, so it earns a
    place in the diagram: 32 query heads over 8 KV heads means a 4× smaller cache
    than full MHA, at the same layer count.
    """
    if heads is None or kv_heads is None:
        return "unknown"
    if kv_heads == 1:
        return "mqa"
    if kv_heads == heads:
        return "mha"
    return "gqa" if kv_heads < heads else "unknown"


def _build(
    *,
    source: str,
    source_detail: str,
    model: str,
    family: str | None,
    layers: int | None,
    hidden: int | None,
    heads: int | None,
    kv_heads: int | None,
    head_dim: int | None,
    ffn_dim: int | None,
    activation: str | None,
    vocab: int | None,
    context: int | None,
    rope_theta: float | None,
    sliding_window: int | None,
    norm_type: str | None,
    tied_embeddings: bool | None,
    param_count: int | None,
    experts: int | None,
    experts_per_token: int | None,
    expert_ffn_dim: int | None,
    shared_experts: int | None,
    notes: list[str],
) -> ModelArchitecture:
    # head_dim is usually hidden/heads, and the diagram wants it — but "usually" is
    # not "always" (Gemma 3 sets 256 where the division gives 240), so a derived
    # value is marked as such and rendered as an estimate rather than a fact.
    head_dim_derived = False
    if head_dim is None and hidden and heads and hidden % heads == 0:
        head_dim = hidden // heads
        head_dim_derived = True

    kind = _attention_kind(heads, kv_heads)
    attention = (
        AttentionSpec(
            heads=heads,
            kvHeads=kv_heads,
            headDim=head_dim,
            headDimDerived=head_dim_derived,
            kind=kind,
            groupRatio=(
                heads // kv_heads if heads and kv_heads and kv_heads > 0 else None
            ),
            slidingWindow=sliding_window,
            ropeTheta=rope_theta,
        )
        if heads or kv_heads
        else None
    )

    gated = _is_gated(family)
    ffn = (
        FfnSpec(
            intermediateSize=ffn_dim,
            activation=activation,
            expansionRatio=(round(ffn_dim / hidden, 2) if ffn_dim and hidden else None),
            gated=gated,
        )
        if ffn_dim or activation
        else None
    )

    moe = (
        MoeSpec(
            experts=experts,
            expertsPerToken=experts_per_token or 0,
            expertIntermediateSize=expert_ffn_dim,
            sharedExperts=shared_experts,
            activeFraction=(
                round(experts_per_token / experts, 3)
                if experts and experts_per_token
                else None
            ),
        )
        if experts
        else None
    )

    return ModelArchitecture(
        source=source,
        sourceDetail=source_detail,
        model=model,
        family=family,
        parameterCount=param_count,
        layers=layers,
        hiddenSize=hidden,
        vocabSize=vocab,
        contextLength=context,
        tiedEmbeddings=tied_embeddings,
        normType=norm_type,
        attention=attention,
        ffn=ffn,
        moe=moe,
        notes=notes,
    )


def from_ollama_show(
    model: str, endpoint: str, data: dict[str, Any]
) -> ModelArchitecture:
    """Normalize an Ollama `/api/show` payload. Read off the actual loaded weights,
    so this is the highest-confidence source available."""
    info: dict[str, Any] = data.get("model_info") or {}
    details: dict[str, Any] = data.get("details") or {}
    family = (
        str(info.get("general.architecture") or details.get("family") or "") or None
    )

    notes: list[str] = []
    quant = details.get("quantization_level")
    if quant:
        # Worth stating plainly: quantization changes the numerics, not the shape,
        # so the diagram is still accurate but the weights are not full precision.
        notes.append(f"Quantized ({quant}) — structure unchanged, precision reduced.")

    return _build(
        source="ollama",
        source_detail=endpoint,
        model=model,
        family=family,
        layers=_int(_first_suffix(info, "block_count")),
        hidden=_int(_first_suffix(info, "embedding_length")),
        heads=_int(_first_suffix(info, "attention.head_count")),
        kv_heads=_int(_first_suffix(info, "attention.head_count_kv")),
        head_dim=_int(_first_suffix(info, "attention.key_length")),
        ffn_dim=_int(_first_suffix(info, "feed_forward_length")),
        activation=None,  # GGUF doesn't record the activation function.
        vocab=_int(_first_suffix(info, "vocab_size")),
        context=_int(_first_suffix(info, "context_length")),
        rope_theta=_float(_first_suffix(info, "rope.freq_base")),
        sliding_window=_int(_first_suffix(info, "attention.sliding_window")),
        norm_type=(
            "rmsnorm"
            if _first_suffix(info, "attention.layer_norm_rms_epsilon") is not None
            else None
        ),
        tied_embeddings=None,
        param_count=_int(info.get("general.parameter_count")),
        experts=_int(_first_suffix(info, "expert_count")),
        experts_per_token=_int(_first_suffix(info, "expert_used_count")),
        expert_ffn_dim=_int(_first_suffix(info, "expert_feed_forward_length")),
        shared_experts=_int(_first_suffix(info, "expert_shared_count")),
        notes=notes,
    )


def from_hf_config(model: str, repo: str, cfg: dict[str, Any]) -> ModelArchitecture:
    """Normalize a Hugging Face `config.json`.

    Note the confidence caveat this carries: the config describes the *repo*, not
    necessarily the weights your server loaded. For a quantized local build they
    agree on structure, which is what the diagram shows — but it's why `source`
    is surfaced rather than hidden.
    """
    # A text-generation config is sometimes nested under text_config (multimodal
    # repos like Gemma 3 VLMs put the language tower there).
    text_cfg = (
        cfg.get("text_config") if isinstance(cfg.get("text_config"), dict) else None
    )
    c: dict[str, Any] = {**cfg, **(text_cfg or {})}

    family = str(cfg.get("model_type") or c.get("model_type") or "") or None
    notes: list[str] = []
    if text_cfg:
        notes.append("Multimodal repo — showing the language tower (text_config).")

    sliding = _int(c.get("sliding_window"))
    if sliding and c.get("sliding_window_pattern"):
        notes.append(
            f"Attention alternates: sliding window every "
            f"{c.get('sliding_window_pattern')} layers, full attention otherwise."
        )
    elif sliding and (c.get("layer_types") or c.get("use_sliding_window")):
        notes.append("Some layers use sliding-window attention rather than full.")

    # MoE spellings differ by family; check each rather than assuming one.
    experts = (
        _int(c.get("num_local_experts"))
        or _int(c.get("n_routed_experts"))
        or _int(c.get("num_experts"))
    )
    quant = cfg.get("quantization_config")
    if isinstance(quant, dict) and quant.get("quant_method"):
        notes.append(f"Repo is {quant['quant_method']}-quantized.")

    return _build(
        source="huggingface",
        source_detail=repo,
        model=model,
        family=family,
        layers=_int(c.get("num_hidden_layers")),
        hidden=_int(c.get("hidden_size")),
        heads=_int(c.get("num_attention_heads")),
        # Absent num_key_value_heads means no GQA — every query head has its own
        # KV head, i.e. plain MHA. Defaulting to heads is the documented semantic.
        kv_heads=_int(c.get("num_key_value_heads"))
        or _int(c.get("num_attention_heads")),
        head_dim=_int(c.get("head_dim")),
        ffn_dim=_int(c.get("intermediate_size")),
        activation=(
            str(c.get("hidden_act") or c.get("hidden_activation") or "") or None
        ),
        vocab=_int(c.get("vocab_size")),
        context=_int(c.get("max_position_embeddings")),
        rope_theta=_float(c.get("rope_theta")),
        sliding_window=sliding,
        norm_type=("rmsnorm" if c.get("rms_norm_eps") is not None else None),
        tied_embeddings=(
            bool(c["tie_word_embeddings"]) if "tie_word_embeddings" in c else None
        ),
        param_count=None,  # config.json carries no parameter count.
        experts=experts,
        experts_per_token=_int(c.get("num_experts_per_tok")),
        expert_ffn_dim=_int(c.get("moe_intermediate_size")),
        shared_experts=_int(c.get("n_shared_experts")),
        notes=notes,
    )


def declared_repo(data: dict[str, Any]) -> str | None:
    """The HF repo a GGUF names as its own base model.

    `general.base_model.0.repo_url` is written at conversion time by whoever built
    the GGUF, so it points at the weights this file was actually derived from. That
    provenance is what makes gap-filling from it defensible: we aren't guessing a
    same-family repo, we're following the file's own declaration.
    """
    info = data.get("model_info") or {}
    url = info.get("general.base_model.0.repo_url")
    if not isinstance(url, str) or "huggingface.co/" not in url:
        return None
    repo = url.split("huggingface.co/", 1)[1].strip("/")
    # owner/name only — reject a deeper path (tree/blob URLs).
    return repo if repo.count("/") == 1 else None


# Fields safe to fill from a secondary source, as (spec attribute path, label).
# Deliberately a whitelist: structural counts that cannot differ between a GGUF and
# the repo it was converted from. Quantization, sizes-in-bytes and anything the
# conversion could legitimately change are NOT here.
_FILLABLE = (
    ("vocabSize", "vocab size"),
    ("contextLength", "context length"),
    ("tiedEmbeddings", "tied embeddings"),
    ("normType", "norm type"),
    ("parameterCount", "parameter count"),
)
_FILLABLE_ATTENTION = (
    ("kvHeads", "KV heads"),
    ("headDim", "head dim"),
    ("ropeTheta", "RoPE theta"),
    ("slidingWindow", "sliding window"),
)
_FILLABLE_FFN = (
    ("intermediateSize", "FFN size"),
    ("activation", "activation"),
    ("gated", "FFN gating"),
)


def fill_gaps(primary: ModelArchitecture, secondary: ModelArchitecture) -> list[str]:
    """Fill only the fields `primary` left unset, from `secondary`. Mutates
    `primary` and returns human labels for what was filled.

    **Never overrides a stated value.** The two sources can genuinely disagree —
    Gemma 4's GGUF reports `attention.key_length` 512 where the repo config says
    `head_dim` 256 — and silently preferring one would produce a diagram that mixes
    contradictory data without admitting it. A gap is safe to fill; a conflict is
    not ours to resolve.
    """
    filled: list[str] = []

    for field, label in _FILLABLE:
        if getattr(primary, field) is None and getattr(secondary, field) is not None:
            setattr(primary, field, getattr(secondary, field))
            filled.append(label)

    if primary.attention and secondary.attention:
        for field, label in _FILLABLE_ATTENTION:
            if (
                getattr(primary.attention, field) is None
                and getattr(secondary.attention, field) is not None
            ):
                setattr(primary.attention, field, getattr(secondary.attention, field))
                filled.append(label)
        # kvHeads may have just arrived, so the derived facts need recomputing.
        heads, kv = primary.attention.heads, primary.attention.kvHeads
        if heads and kv:
            primary.attention.kind = _attention_kind(heads, kv)
            primary.attention.groupRatio = heads // kv if kv > 0 else None
    elif primary.attention is None and secondary.attention is not None:
        primary.attention = secondary.attention
        filled.append("attention")

    if primary.ffn and secondary.ffn:
        for field, label in _FILLABLE_FFN:
            if (
                getattr(primary.ffn, field) is None
                and getattr(secondary.ffn, field) is not None
            ):
                setattr(primary.ffn, field, getattr(secondary.ffn, field))
                filled.append(label)
    elif primary.ffn is None and secondary.ffn is not None:
        primary.ffn = secondary.ffn
        filled.append("FFN")

    if primary.moe is None and secondary.moe is not None:
        primary.moe = secondary.moe
        filled.append("MoE routing")

    return filled


async def fetch_hf_config(repo: str) -> dict[str, Any] | None:
    """A repo's `config.json`, using the Hugging Face connector's token when one is
    connected (Gemma's repos are gated). Returns None if it can't be had."""
    try:
        from huggingface_hub import hf_hub_download

        from backend.modules.interpretability.tokenizer import hf_token

        path = hf_hub_download(
            repo_id=repo, filename="config.json", token=await hf_token()
        )
        with open(path, encoding="utf-8") as handle:
            loaded = json.load(handle)
        return loaded if isinstance(loaded, dict) else None
    except Exception as exc:
        logger.info("interpretability: no config.json for %s (%s)", repo, exc)
        return None

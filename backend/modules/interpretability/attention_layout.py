"""What every layer's token mixer is, read off a GGUF header.

`architecture.py` describes a model with one attention spec — one head count, one
head width, one sliding window. That was already known to be false for models that
alternate (see the `varies` note in docs/modules/interpretability.mdx), and the
models that matter now go much further:

- **Gemma 4** windows 5 of every 6 layers (4 of 5 on E2B), gives its global layers
  twice the head width (512 vs 256) and a quarter of the KV heads, drops the V
  projection on them (K=V), and on E2B/E4B makes the last N layers *reuse* an
  earlier layer's KV cache.
- **Qwen3.5 / Qwen3-Next** are three Gated-DeltaNet (linear attention) layers to
  every full-attention one, and the full ones gate their output.
- **granite-h**, **LFM2**, Jamba: attention only every few blocks; the rest are
  Mamba blocks or short convolutions.

So this module answers per layer, and says how it knows. The precedence is:

1. **Metadata that is per layer** — `attention.sliding_window_pattern` as an array,
   `attention.recurrent_layers`, a per-layer `attention.head_count_kv` (0 meaning
   "no attention here"), `attention.shared_kv_layers`. `basis = "metadata"`.
2. **The family's rule** — what llama.cpp's loader for that architecture does when
   the file does not say (Gemma 3 is "of every 6, all but the last windowed";
   Llama 4's chunk size is a constant in the loader). `basis = "rule"`.
3. **The tensor directory** — `attn_*` vs `ssm_*` vs `shortconv`, missing `attn_v`,
   `attn_sinks`, the K projection's width. `basis = "tensors"`. Also the
   cross-check: where the rule or metadata and the tensors disagree, the layout
   carries a `conflicts` entry rather than quietly picking one.

Nothing here reads tensor data — only the header, the same as `gguf.py`.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from backend.modules.interpretability.models import (
    AttentionLayout,
    KvCachePoint,
    LayerAttention,
)

#: Kinds that attend over a KV cache, as opposed to carrying a recurrent state.
ATTENDING = frozenset({"global", "sliding", "chunked", "mla"})

_FP16 = 2

#: Context lengths the KV curve is evaluated at (plus the model's own maximum).
KV_CONTEXTS = (1024, 4096, 8192, 32768, 131072)


class TensorLike(Protocol):
    name: str
    shape: tuple[int, ...]


@dataclass(frozen=True)
class FamilyRule:
    """What llama.cpp's loader for a family does when the file does not say.

    `swa_period`: of every `swa_period` layers, all but the last are windowed
    (`llama_hparams::set_swa_pattern`). `full_interval`: every Nth layer is full
    attention and the rest linear (Qwen3-Next's `full_attention_interval`).
    """

    loader: str
    swa_period: int | None = None
    swa_kind: str = "sliding"
    #: A window the loader hard-codes rather than reading (Llama 4's 8192 chunk).
    fixed_window: int | None = None
    #: Global layers skip rope (Llama 4's NoPE layers).
    nope_on_global: bool = False
    #: Every Nth layer skips rope regardless (SmolLM3's `n_no_rope_layer_step`).
    nope_step: int | None = None
    full_interval: int | None = None


FAMILY_RULES: dict[str, FamilyRule] = {
    "gemma2": FamilyRule("src/models/gemma2-iswa.cpp", swa_period=2),
    "gemma3": FamilyRule("src/models/gemma3.cpp", swa_period=6),
    "cohere2": FamilyRule("src/models/cohere2-iswa.cpp", swa_period=4),
    "gpt-oss": FamilyRule("src/models/openai-moe.cpp", swa_period=2),
    "llama4": FamilyRule(
        "src/models/llama4.cpp",
        swa_period=4,
        swa_kind="chunked",
        fixed_window=8192,
        nope_on_global=True,
    ),
    "smollm3": FamilyRule("src/models/smollm3.cpp", nope_step=4),
    "qwen3next": FamilyRule("src/models/qwen3next.cpp", full_interval=4),
    "qwen35": FamilyRule("src/models/qwen35.cpp", full_interval=4),
    "qwen35moe": FamilyRule("src/models/qwen35moe.cpp", full_interval=4),
}


def swa_pattern(n_layer: int, period: int) -> list[bool]:
    """`True` for a windowed layer: llama.cpp's `set_swa_pattern(period)`.

    Of every `period` layers, all but the last are windowed; a period of 0 windows
    every layer. Pinned against the WebML engine's `layerWindow` by
    `backend/tests/fixtures/swa_patterns.json`, which both test suites read.
    """
    return [period == 0 or (i % period) < (period - 1) for i in range(n_layer)]


# ── metadata access ──────────────────────────────────────────────────────────


def _get(meta: dict[str, Any], suffix: str) -> Any:
    """A GGUF value by key suffix (`attention.key_length` matches
    `gemma4.attention.key_length`) — the same rule `architecture.py` uses, so a
    new family works without a table entry."""
    for key, value in meta.items():
        if key.endswith("." + suffix):
            return value
    return None


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None or isinstance(value, (list, tuple)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    if value is None or isinstance(value, (list, tuple, bool)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _per_layer_int(value: Any, n: int) -> list[int | None]:
    """A scalar repeated, or an array as given (padded/cut to `n`)."""
    if isinstance(value, (list, tuple)):
        out = [_int(v) for v in value[:n]]
        return out + [None] * (n - len(out))
    return [_int(value)] * n


def _per_layer_bool(value: Any, n: int) -> list[bool] | None:
    if not isinstance(value, (list, tuple)) or len(value) < n:
        return None
    return [bool(v) for v in value[:n]]


# ── tensor evidence ──────────────────────────────────────────────────────────

_BLOCK = re.compile(r"^blk\.(\d+)\.(.+?)(?:\.(?:weight|bias))?$")


def _roles_by_layer(
    tensors: Iterable[TensorLike], n: int
) -> list[dict[str, tuple[int, ...]]]:
    roles: list[dict[str, tuple[int, ...]]] = [{} for _ in range(n)]
    for t in tensors:
        m = _BLOCK.match(t.name)
        if not m:
            continue
        layer = int(m.group(1))
        if 0 <= layer < n:
            roles[layer][m.group(2)] = tuple(t.shape)
    return roles


def _tensor_kind(roles: dict[str, tuple[int, ...]]) -> str | None:
    """What the tensors of one block say its mixer is, or None if there are none.

    Order matters: Qwen3.5's DeltaNet layers carry `ssm_*` tensors *and* a fused
    `attn_qkv`, and are linear attention, not a Mamba block.
    """
    if not roles:
        return None
    names = roles.keys()
    has = lambda prefix: any(r.startswith(prefix) for r in names)  # noqa: E731
    if has("attn_kv_a_mqa") or has("attn_kv_b"):
        return "mla"
    if has("time_mix"):
        return "linear"
    if has("ssm_") and "attn_qkv" in names and "attn_k" not in names:
        return "linear"
    if has("ssm_"):
        return "ssm"
    if has("shortconv"):
        return "conv"
    if "attn_q" in names or "attn_k" in names or "attn_qkv" in names:
        return "attention"
    return "none"


def _out_dim(shape: tuple[int, ...] | None) -> int | None:
    """A projection's output width. GGUF stores `ne` innermost-first, so a 2-D
    weight is `(in, out)` — `attn_q` on Gemma 4 E2B is `(1536, 2048)`."""
    if not shape or len(shape) < 2:
        return None
    return int(shape[1])


# ── the layout ───────────────────────────────────────────────────────────────


def layer_attention(
    meta: dict[str, Any],
    tensors: Sequence[TensorLike],
    *,
    path: str = "",
) -> AttentionLayout:
    """The per-layer attention layout of the model `meta`/`tensors` describe."""
    family = str(meta.get("general.architecture") or "") or None
    n = _int(_get(meta, "block_count"))
    if not n:
        return AttentionLayout(
            source="gguf", path=path, family=family, error="GGUF states no block_count"
        )

    rule = FAMILY_RULES.get(family or "")
    roles = _roles_by_layer(tensors, n)
    tkinds = [_tensor_kind(r) for r in roles]
    conflicts: list[str] = []
    notes: list[str] = []

    heads = _per_layer_int(_get(meta, "attention.head_count"), n)
    kv_meta = _get(meta, "attention.head_count_kv")
    kv_heads = _per_layer_int(kv_meta, n)
    kv_per_layer = isinstance(kv_meta, (list, tuple))
    window = _int(_get(meta, "attention.sliding_window"))

    # Multi-token-prediction layers are appended after the main stack and are
    # full attention whatever the interval says (llama.cpp counts them
    # non-recurrent).
    n_mtp = _int(_get(meta, "nextn_predict_layers")) or 0
    mtp = [i >= n - n_mtp for i in range(n)]

    # ── 1. which layers are windowed / recurrent, and how we know ────────────
    declared: list[str | None] = [None] * n
    basis: list[str] = ["tensors"] * n

    swa_value = _get(meta, "attention.sliding_window_pattern")
    swa_array = _per_layer_bool(swa_value, n)
    recurrent_array = _per_layer_bool(_get(meta, "attention.recurrent_layers"), n)
    full_interval = _int(_get(meta, "full_attention_interval"))
    windowed_kind = rule.swa_kind if rule else "sliding"

    if swa_array is not None:
        for i, windowed in enumerate(swa_array):
            declared[i], basis[i] = (
                (windowed_kind if windowed else "global"),
                "metadata",
            )
    elif rule and rule.swa_period:
        period = _int(swa_value) or rule.swa_period
        pattern = swa_pattern(n, period)
        for i, windowed in enumerate(pattern):
            declared[i], basis[i] = (windowed_kind if windowed else "global"), "rule"
        notes.append(
            f"{family} does not store its window pattern per layer; of every {period} "
            f"layers all but the last are {windowed_kind}, per llama.cpp's "
            f"{rule.loader}."
        )
    elif window and not (rule and rule.full_interval):
        notes.append(
            f"The metadata states a {window}-token sliding window but not which layers "
            "use it, and llama.cpp has no rule for this family on record here, so "
            "every attention layer is drawn global."
        )

    if recurrent_array is not None:
        for i, recurrent in enumerate(recurrent_array):
            if not mtp[i]:
                declared[i] = "linear" if recurrent else "global"
                basis[i] = "metadata"
    elif rule and rule.full_interval:
        interval = full_interval or rule.full_interval
        for i in range(n):
            if not mtp[i]:
                declared[i] = "global" if (i + 1) % interval == 0 else "linear"
                basis[i] = "metadata" if full_interval else "rule"
    for i in range(n):
        if mtp[i]:
            declared[i], basis[i] = "global", "metadata"

    # A per-layer KV-head array with zeros is the hybrid convention (granite-h,
    # LFM2, Jamba): no attention in that block. What it *is* comes from tensors.
    if kv_per_layer:
        for i in range(n):
            if kv_heads[i] == 0:
                declared[i], basis[i] = None, "metadata"

    # ── 2. reconcile with the tensors ─────────────────────────────────────────
    kv_lora = _int(_get(meta, "attention.kv_lora_rank"))
    kinds: list[str] = []
    disagree: list[int] = []
    for i in range(n):
        tk = tkinds[i]
        dk = declared[i]
        if tk == "mla" or (kv_lora and tk == "attention"):
            kinds.append("mla")
            basis[i] = "metadata" if kv_lora else "tensors"
        elif dk is None:
            if kv_per_layer and kv_heads[i] == 0:
                # Declared non-attention; the tensors name the mixer, so they are
                # the basis of the kind shown.
                kinds.append(tk if tk not in (None, "attention") else "none")
                basis[i] = "tensors"
                if tk == "attention":
                    disagree.append(i)
            else:
                kinds.append("global" if tk in ("attention", None) else tk)
                basis[i] = "metadata" if tk == "attention" else "tensors"
        elif dk in ATTENDING:
            if tk in ("linear", "ssm", "conv"):
                disagree.append(i)
                kinds.append(tk)
                basis[i] = "tensors"
            else:
                kinds.append(dk)
        else:  # declared linear
            if tk == "attention":
                disagree.append(i)
                kinds.append("global")
                basis[i] = "tensors"
            else:
                kinds.append(dk)
    if disagree:
        conflicts.append(
            f"Layers {_ranges(disagree)}: the metadata/rule and the tensor directory "
            "disagree about the mixer; drawn as the tensors show."
        )

    # ── 3. per-layer facts ────────────────────────────────────────────────────
    key_len = _int(_get(meta, "attention.key_length"))
    val_len = _int(_get(meta, "attention.value_length"))
    key_swa = _int(_get(meta, "attention.key_length_swa"))
    val_swa = _int(_get(meta, "attention.value_length_swa"))
    rope_base = _float(_get(meta, "rope.freq_base"))
    rope_base_swa = _float(_get(meta, "rope.freq_base_swa"))
    rope_dims = _int(_get(meta, "rope.dimension_count"))
    rope_dims_swa = _int(_get(meta, "rope.dimension_count_swa"))
    hidden = _int(_get(meta, "embedding_length"))
    conv_len = _int(_get(meta, "shortconv.l_cache")) or _int(
        _get(meta, "ssm.conv_kernel")
    )
    shared = _int(_get(meta, "attention.shared_kv_layers")) or 0
    first_shared = n - n_mtp - shared if shared > 0 else None

    layers: list[LayerAttention] = []
    dim_mismatch: list[int] = []
    for i in range(n):
        kind = kinds[i]
        r = roles[i]
        layer = LayerAttention(layer=i, kind=kind, basis=basis[i], mtp=mtp[i])
        if kind in ATTENDING:
            windowed = kind in ("sliding", "chunked")
            layer.heads = heads[i]
            layer.kvHeads = kv_heads[i] if kv_heads[i] else heads[i]
            layer.headDimK = (key_swa if windowed and key_swa else key_len) or (
                hidden // heads[i] if hidden and heads[i] else None
            )
            layer.headDimV = (
                val_swa if windowed and val_swa else val_len
            ) or layer.headDimK
            layer.ropeBase = rope_base_swa if windowed and rope_base_swa else rope_base
            layer.ropeDims = rope_dims_swa if windowed and rope_dims_swa else rope_dims
            if kind == "sliding":
                layer.window = window
            elif kind == "chunked":
                layer.window = (rule.fixed_window if rule else None) or window
            if rule and rule.nope_on_global and kind == "global":
                layer.nope = True
            if rule and rule.nope_step and (i + 1) % rule.nope_step == 0:
                layer.nope = True
            # Tensor cross-checks.
            k_out = _out_dim(r.get("attn_k"))
            if k_out and layer.kvHeads and layer.headDimK:
                if k_out != layer.kvHeads * layer.headDimK:
                    dim_mismatch.append(i)
            q_out = _out_dim(r.get("attn_q"))
            if q_out and layer.heads and layer.headDimK:
                layer.outputGate = q_out == 2 * layer.heads * layer.headDimK
            layer.kEqV = "attn_k" in r and "attn_v" not in r and "attn_qkv" not in r
            layer.sinks = "attn_sinks" in r
            if first_shared is not None and first_shared <= i < n - n_mtp:
                same = [j for j in range(first_shared) if kinds[j] == kind]
                layer.kvSourceLayer = same[-1] if same else None
        layers.append(layer)

    if dim_mismatch:
        conflicts.append(
            f"Layers {_ranges(dim_mismatch)}: the K projection's width is not KV heads × "
            "the stated head width."
        )
    if first_shared is not None:
        notes.append(
            f"Layers {first_shared}–{n - n_mtp - 1} compute no K/V of their own: each "
            "attends over the cache of the last earlier layer of its kind "
            f"(attention.shared_kv_layers = {shared})."
        )
    if any(layer.kEqV for layer in layers):
        notes.append(
            f"Layers {_ranges([la.layer for la in layers if la.kEqV])} have no V "
            "projection: the value is the key (K=V)."
        )
    if n_mtp:
        notes.append(
            f"The last {n_mtp} block(s) are multi-token-prediction heads, used for "
            "speculative drafting rather than in the main pass."
        )

    _fill_kv_bytes(layers, kv_lora=kv_lora, rope_dims=rope_dims)
    _fill_reach(layers, conv_len=conv_len)

    counts = Counter(layer.kind for layer in layers if not layer.mtp)
    ctx_max = _int(_get(meta, "context_length"))
    return AttentionLayout(
        source="gguf",
        path=path,
        family=family,
        layers=layers,
        counts=dict(counts),
        pattern=describe_pattern([la.kind for la in layers if not la.mtp]),
        kvCache=kv_curve(layers, ctx_max),
        conflicts=conflicts,
        notes=notes,
    )


def _fill_kv_bytes(
    layers: list[LayerAttention], *, kv_lora: int | None, rope_dims: int | None
) -> None:
    """fp16 KV bytes per token per layer. A shared-KV layer adds nothing; a
    recurrent layer keeps a fixed-size state, not a per-token cache."""
    for layer in layers:
        if layer.kind not in ATTENDING or layer.kvSourceLayer is not None or layer.mtp:
            continue
        if layer.kind == "mla":
            # llama.cpp caches the compressed latent plus the rope part of the key.
            if kv_lora:
                layer.kvBytesPerToken = (kv_lora + (rope_dims or 0)) * _FP16
            continue
        layer.kvBytesPerToken = _own_kv_bytes(layer)


def _own_kv_bytes(layer: LayerAttention) -> int:
    if not layer.kvHeads or not layer.headDimK:
        return 0
    v = 0 if layer.kEqV else (layer.headDimV or layer.headDimK)
    return layer.kvHeads * (layer.headDimK + v) * _FP16


def _fill_reach(layers: list[LayerAttention], *, conv_len: int | None) -> None:
    """How far back information can have come from by the end of each layer — an
    upper bound. A global layer (or a recurrent state) reaches the whole context;
    a windowed one extends what came before by its window."""
    reach: int | None = 0
    for layer in layers:
        if layer.mtp:
            layer.reach = reach
            continue
        if layer.kind in ("global", "mla", "linear", "ssm"):
            reach = None
        elif layer.kind in ("sliding", "chunked") and reach is not None:
            reach += max((layer.window or 1) - 1, 0)
        elif layer.kind == "conv" and reach is not None:
            reach += max((conv_len or 1) - 1, 0)
        layer.reach = reach


def kv_curve(layers: list[LayerAttention], ctx_max: int | None) -> list[KvCachePoint]:
    """The fp16 KV cache at several context lengths, as built and as it would be
    with every attention layer global and unshared."""
    contexts = sorted(
        {c for c in (*KV_CONTEXTS, ctx_max or 0) if c and (not ctx_max or c <= ctx_max)}
    )
    points: list[KvCachePoint] = []
    for ctx in contexts:
        actual = 0
        if_global = 0
        for layer in layers:
            if layer.kind not in ATTENDING or layer.mtp:
                continue
            span = min(ctx, layer.window) if layer.window else ctx
            actual += layer.kvBytesPerToken * span
            own = layer.kvBytesPerToken if layer.kind == "mla" else _own_kv_bytes(layer)
            if_global += own * ctx
        points.append(KvCachePoint(context=ctx, bytes=actual, bytesIfGlobal=if_global))
    return points


def describe_pattern(kinds: Sequence[str]) -> str:
    """A one-line reading of the stack: "5 sliding : 1 global, ×8", "all 16
    global", or plain counts when nothing repeats."""
    n = len(kinds)
    if not n:
        return ""
    if len(set(kinds)) == 1:
        return f"all {n} {kinds[0]}"
    for period in range(2, n // 2 + 1):
        if all(kinds[i] == kinds[i % period] for i in range(n)):
            runs: list[str] = []
            run_kind, run_len = kinds[0], 0
            for k in kinds[:period]:
                if k == run_kind:
                    run_len += 1
                else:
                    runs.append(f"{run_len} {run_kind}")
                    run_kind, run_len = k, 1
            runs.append(f"{run_len} {run_kind}")
            whole, rest = divmod(n, period)
            tail = f" + {rest}" if rest else ""
            return f"{' : '.join(runs)}, ×{whole}{tail}"
    counts = Counter(kinds)
    return ", ".join(f"{c} {k}" for k, c in counts.most_common())


def _ranges(indices: Sequence[int]) -> str:
    """[1,2,3,7,9,10] → "1–3, 7, 9–10"."""
    out: list[str] = []
    start = prev = None
    for i in sorted(indices):
        if start is None:
            start = prev = i
        elif i == prev + 1:
            prev = i
        else:
            out.append(f"{start}–{prev}" if prev != start else f"{start}")
            start = prev = i
    if start is not None:
        out.append(f"{start}–{prev}" if prev != start else f"{start}")
    return ", ".join(out)

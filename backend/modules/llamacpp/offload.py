"""Where the bytes go at a given `--n-gpu-layers`, measured from the GGUF itself.

**Why this exists.** "GPU layers" was a number you typed blind. The pane could tell
you a card has 12 GB and that a file is 5 GB, and nothing at all about the only
question being asked: *how many of these layers fit?* Every input needed to answer it
was already on disk — the tensor directory carries a size and a block index per
tensor — so the answer was a sum away and was simply never taken.

**Measured, not modelled.** The per-layer figures here are the real byte sizes of the
real tensors in the file the user selected, including the mixed quantization a
K-quant build actually uses. The one modelled quantity is the KV cache, and it is
reported *per token* so the caller multiplies by whatever context it is offering —
the context size is a live control, and baking one into the answer would make the
number silently wrong the moment it moved.

**What it deliberately does not claim.** A real allocation also holds compute
buffers, the CUDA context and whatever else the driver keeps, and those depend on
the build and the batch size rather than on the weights. So this reports weights and
KV cache, says so, and leaves headroom to the caller instead of inventing a fudge
factor that would be wrong on some machine and trusted on all of them.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from backend.modules.interpretability import gguf

logger = logging.getLogger(__name__)

#: llama.cpp keeps the KV cache at f16 unless told otherwise: 2 bytes an element,
#: and one element each for K and V.
_KV_BYTES_PER_ELEMENT = 2
_KV_TENSORS_PER_LAYER = 2


def _int(metadata: dict[str, Any], key: str) -> int | None:
    value = metadata.get(key)
    return value if isinstance(value, int) else None


def _kv_bytes_per_token(metadata: dict[str, Any], arch: str, layers: int) -> int | None:
    """Bytes of KV cache one token of context costs, across every layer.

    `head_count_kv` is the grouped-query figure and is the whole point of computing
    this from metadata rather than from `embedding_length`: a GQA model with 8 KV
    heads against 32 query heads has a **four times smaller** cache, which is often
    the difference between fitting and not.

    None whenever a needed field is missing — a KV cache guessed from a missing head
    count would be an invented number sitting next to measured ones.
    """
    if not arch or not layers:
        return None
    kv_heads = _int(metadata, f"{arch}.attention.head_count_kv")
    if kv_heads is None:
        kv_heads = _int(metadata, f"{arch}.attention.head_count")
    # `key_length` is authoritative where it exists; otherwise the usual
    # hidden / heads. Both can be absent, and then there is no answer to give.
    head_dim = _int(metadata, f"{arch}.attention.key_length")
    if head_dim is None:
        hidden = _int(metadata, f"{arch}.embedding_length")
        heads = _int(metadata, f"{arch}.attention.head_count")
        head_dim = hidden // heads if hidden and heads else None
    if not kv_heads or not head_dim:
        return None
    return _KV_TENSORS_PER_LAYER * layers * kv_heads * head_dim * _KV_BYTES_PER_ELEMENT


def layer_plan(path: str | Path) -> dict[str, Any]:
    """Per-layer byte sizes for one GGUF, plus what a token of context costs.

    Returns `layerBytes` indexed by block, and `overheadBytes` for everything outside
    the stack (token embeddings, the final norm, the output head). Those are kept
    apart because llama.cpp treats them differently: the blocks are what
    `--n-gpu-layers` moves, while the output tensors only follow when the count
    exceeds the block count — which is exactly what `-ngl 99` is for.
    """
    try:
        header = gguf.read_header(path)
    except (OSError, gguf.GgufError, ValueError) as exc:
        logger.info("llamacpp: no offload plan for %s (%s)", path, exc)
        return {"error": str(exc)}

    max_layer = -1
    for tensor in header.tensors:
        if tensor.layer is not None:
            max_layer = max(max_layer, tensor.layer)
    layers = max_layer + 1

    layer_bytes = [0] * layers
    overhead = 0
    complete = True
    for tensor in header.tensors:
        if tensor.n_bytes is None:
            # An unrecognized quantization. Every total below becomes a floor, and
            # `complete` is what says so — silently skipping it would report a model
            # as smaller than it is, which is the one error that matters here.
            complete = False
            continue
        if tensor.layer is None:
            overhead += tensor.n_bytes
        else:
            layer_bytes[tensor.layer] += tensor.n_bytes

    arch = str(header.metadata.get("general.architecture") or "")
    return {
        "path": str(header.path),
        "layerCount": layers,
        "layerBytes": layer_bytes,
        "overheadBytes": overhead,
        "totalBytes": sum(layer_bytes) + overhead,
        "kvBytesPerToken": _kv_bytes_per_token(header.metadata, arch, layers),
        "contextLength": _int(header.metadata, f"{arch}.context_length")
        if arch
        else None,
        "complete": complete,
        "error": "",
    }

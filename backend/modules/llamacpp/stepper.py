"""Reading a trace the way a debugger reads a program: one step at a time.

A trace's records are already an instruction stream. The tracer appends them in
the order ggml *computed* them, each tagged with its pass and its block, so
"step into" is the next record, "step over" is the next block and "step out" is
the end of the pass. Building that program needs nothing the browser does not
already have — it is the record list — so it is built there (`stepper/program.ts`),
with the one node classifier that exists (`node-kind.ts`).

What the browser *cannot* do is look inside a record, and that is this module:

- **`record_array`** — a record as the 4-D array it was, honouring its strides.
  The flat prefix `get_record` ships is fine for a value strip and wrong for
  anything with more than one axis: an attention record is
  `[n_kv, n_tokens, n_head]`, and the first 8192 values of it are head 0's first
  few rows, not a picture of anything.
- **`matrix`** — one 2-D slice (a head, or the token × feature plane) at a size a
  pane can draw, plus per-row norms taken at *full* resolution.
- **`residual_delta`** — what one block wrote into the residual stream.
- **`experts`** — mixture-of-experts routing: which experts each token went to.

## Strides are recorded in the *source* dtype

`nb` is copied off the live ggml tensor, so it is in bytes of the type ggml held.
An `fp16` record was **downcast from f32 by the tracer**, which means its bytes
are half as wide as its strides say. Element strides are therefore `nb / 4` for
an `fp16` record and `nb / itemsize` for a `full` one — dividing by the stored
width would double every stride and read garbage that still looks like numbers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from backend.modules.llamacpp import traces

#: Cells a matrix response may carry. 512 tokens is the trace cap, so a whole
#: attention head (`512 x 512`) fits exactly; a residual plane (4096 features x
#: 512 tokens) does not and is pooled along the feature axis.
MAX_MATRIX_CELLS = 512 * 512

#: Source-dtype widths — see "Strides are recorded in the source dtype" above.
_ITEMSIZE = {"f32": 4, "F32": 4, "f16": 2, "F16": 2, "i32": 4, "I32": 4}


class StepperError(Exception):
    """A request this trace cannot answer, stated rather than approximated."""


def _source_width(record: traces.TraceRecord) -> int:
    if record.fidelity == "fp16":
        return 4  # downcast from f32 at capture; nb still counts f32 bytes
    width = _ITEMSIZE.get(record.dtype)
    if width is None:
        raise StepperError(f"no reader for dtype {record.dtype!r}")
    return width


def record_array(trace: traces.Trace, record: traces.TraceRecord) -> np.ndarray:
    """The record as `[ne3, ne2, ne1, ne0]` (numpy order: slowest axis first).

    Raises on a `summary` record: it has no bytes by construction, and an array
    of zeros standing in for it is exactly what the trace format forbids.
    """
    if record.fidelity == "summary" or record.length <= 0:
        raise StepperError(
            f"{record.name} was stored as a summary — statistics, not values"
        )
    with trace.blob.open("rb") as handle:
        handle.seek(record.offset)
        payload = handle.read(record.length)
    flat = traces.decode_array(payload, record.dtype)
    ne = [max(1, int(n)) for n in (list(record.ne) + [1, 1, 1, 1])[:4]]
    width = _source_width(record)
    strides = [int(b) // width for b in (list(record.nb) + [0, 0, 0, 0])[:4]]
    reach = sum((n - 1) * s for n, s in zip(ne, strides)) + 1
    if strides[0] <= 0 or reach > flat.size:
        # A stride set that points past the payload is a record we do not
        # understand; assuming contiguity is only safe when the sizes agree.
        total = ne[0] * ne[1] * ne[2] * ne[3]
        if total > flat.size:
            raise StepperError(
                f"{record.name}: strides {record.nb} do not fit its {flat.size} values"
            )
        return flat[:total].reshape(ne[3], ne[2], ne[1], ne[0])
    item = flat.itemsize
    view = np.lib.stride_tricks.as_strided(
        flat,
        shape=(ne[3], ne[2], ne[1], ne[0]),
        strides=(
            strides[3] * item,
            strides[2] * item,
            strides[1] * item,
            strides[0] * item,
        ),
        writeable=False,
    )
    return np.array(view)  # own the memory; the strided view aliases the payload


def prompt_length(trace: traces.Trace) -> int:
    tokens = trace.manifest.get("promptTokens")
    if isinstance(tokens, int) and tokens > 0:
        return tokens
    path = trace.directory / "tokens.json"
    try:
        import json

        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    return sum(1 for r in rows if not r.get("generated"))


def kv_in_use(trace: traces.Trace, pass_index: int, ne0: int) -> int:
    """How many KV positions an attention record's columns actually cover.

    `kq_soft_max` is as wide as the **allocated** KV cache (256 on a six-token
    prompt), and the masked columns are exact zeros. Drawing all of them makes a
    six-token attention pattern a thin stripe in a black square — correct and
    unreadable. Pass 0 attends over the prompt; pass n over the prompt plus the
    n tokens generated before it.
    """
    used = prompt_length(trace) + max(0, pass_index)
    return max(1, min(ne0, used)) if used > 0 else ne0


def _pool_columns(matrix: np.ndarray, target: int) -> np.ndarray:
    """Max-|x| pooling along columns, keeping the sign of the winner.

    Mean pooling a residual plane averages its outlier features into nothing,
    and the outliers are the story (a handful of features in every modern model
    run hundreds of times hotter than the rest)."""
    rows, cols = matrix.shape
    if cols <= target:
        return matrix
    edges = np.linspace(0, cols, target + 1).astype(int)
    out = np.empty((rows, target), dtype=np.float32)
    for i in range(target):
        block = matrix[:, edges[i] : max(edges[i] + 1, edges[i + 1])]
        idx = np.abs(block).argmax(axis=1)
        out[:, i] = block[np.arange(rows), idx]
    return out


@dataclass
class Matrix:
    rows: int
    cols: int
    values: list[float]
    row_norms: list[float]
    heads: int
    head: int
    source_cols: int
    pooled: bool
    kv_cropped: bool
    row_axis: str
    col_axis: str
    row_offset: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "rows": self.rows,
            "cols": self.cols,
            "values": self.values,
            "rowNorms": self.row_norms,
            "heads": self.heads,
            "head": self.head,
            "sourceCols": self.source_cols,
            "pooled": self.pooled,
            "kvCropped": self.kv_cropped,
            "rowAxis": self.row_axis,
            "colAxis": self.col_axis,
            "rowOffset": self.row_offset,
        }


def is_attention_scores(name: str) -> bool:
    return name.startswith("kq_soft_max") or name.startswith("kq-")


def matrix(
    trace: traces.Trace, record: traces.TraceRecord, head: int = 0, max_cols: int = 256
) -> Matrix:
    """One 2-D plane of a record, sized for a pane.

    Rows are `ne1` (tokens, for every activation llama.cpp names) and columns are
    `ne0` (features, or KV positions for attention). `head` picks `ne2`. Row
    norms are computed **before** pooling, so the bar beside a row is the true L2
    of that token's vector and not of its thumbnail.
    """
    array = record_array(trace, record)
    heads = int(array.shape[1])
    if not 0 <= head < heads:
        raise StepperError(f"{record.name} has {heads} heads; there is no head {head}")
    plane = array[0, head].astype(np.float32)  # [ne1, ne0]
    attention = is_attention_scores(record.name)
    kv_cropped = False
    if attention:
        used = kv_in_use(trace, record.pass_index, plane.shape[1])
        kv_cropped = used < plane.shape[1]
        plane = plane[:, :used]
    source_cols = int(plane.shape[1])
    norms = np.sqrt((plane.astype(np.float64) ** 2).sum(axis=1)).astype(np.float32)
    cap_cols = max(1, min(max_cols, MAX_MATRIX_CELLS // max(1, plane.shape[0])))
    # Attention is never pooled: every KV column is a token you can point at, and
    # a max over two of them would credit the wrong one.
    pooled_plane = plane if attention else _pool_columns(plane, cap_cols)
    if pooled_plane.size > MAX_MATRIX_CELLS:
        raise StepperError(
            f"{record.name} is {plane.shape[0]}x{plane.shape[1]} — too large to draw"
        )
    # Rows cover the *tail* of the sequence when llama.cpp pruned this node to
    # fewer positions (the last block's output on a prompt pass is one column).
    n_tokens = prompt_length(trace) if record.pass_index == 0 else 1
    row_offset = max(0, n_tokens - int(plane.shape[0]))
    return Matrix(
        rows=int(pooled_plane.shape[0]),
        cols=int(pooled_plane.shape[1]),
        values=[float(v) for v in pooled_plane.reshape(-1)],
        row_norms=[float(v) for v in norms],
        heads=heads,
        head=head,
        source_cols=source_cols,
        pooled=pooled_plane.shape[1] != source_cols,
        kv_cropped=kv_cropped,
        row_axis="token",
        col_axis="kv position" if attention else "feature",
        row_offset=row_offset,
    )


def head_summaries(
    trace: traces.Trace, record: traces.TraceRecord
) -> list[dict[str, float]]:
    """Per-head shape of an attention record: where each head looks.

    `entropy` (in nats, averaged over query rows) separates a head that attends
    to one token from one that smears; `selfWeight` is the mean weight on the
    diagonal and `firstWeight` the mean on position 0 — the attention sink most
    decoder models develop. Enough to pick the interesting head from a grid of
    thumbnails without drawing 32 matrices.
    """
    if not is_attention_scores(record.name):
        raise StepperError(f"{record.name} is not an attention score record")
    array = record_array(trace, record)[0].astype(np.float64)  # [head, row, kv]
    used = kv_in_use(trace, record.pass_index, array.shape[2])
    array = array[:, :, :used]
    n_rows = array.shape[1]
    # Query row r sits at absolute position (used - n_rows + r).
    base = used - n_rows
    out: list[dict[str, float]] = []
    for h in range(array.shape[0]):
        probs = np.clip(array[h], 0.0, None)
        sums = probs.sum(axis=1, keepdims=True)
        probs = np.divide(probs, sums, out=np.zeros_like(probs), where=sums > 0)
        with np.errstate(divide="ignore", invalid="ignore"):
            ent = -(np.where(probs > 0, probs * np.log(probs), 0.0)).sum(axis=1)
        diag = [probs[r, base + r] for r in range(n_rows) if 0 <= base + r < used]
        out.append(
            {
                "head": float(h),
                "entropy": float(ent.mean()) if ent.size else 0.0,
                "selfWeight": float(np.mean(diag)) if diag else 0.0,
                "firstWeight": float(probs[:, 0].mean()) if used else 0.0,
            }
        )
    return out


def _find(
    trace: traces.Trace, pass_index: int, prefix: str, layer: int | None
) -> traces.TraceRecord | None:
    for record in trace.records:
        if record.pass_index != pass_index or record.layer != layer:
            continue
        if record.name.startswith(prefix):
            return record
    return None


def residual_delta(
    trace: traces.Trace, layer: int, pass_index: int = 0
) -> dict[str, Any]:
    """What block `layer` wrote into the residual stream, per token.

    `l_out-L` minus the stream entering the block (`l_out-(L-1)`, or `inp_embd`
    for block 0). `relative` is ‖Δ‖/‖before‖ — "this block rewrote 40% of the
    token" reads the same on any model — and `cosine` is how far the direction
    turned. Only the positions **both** tensors computed are compared: the last
    block is pruned to the final token on a prompt pass, and aligning from the
    left would subtract the wrong tokens from each other.
    """
    after_rec = _find(trace, pass_index, "l_out", layer)
    before_rec = (
        _find(trace, pass_index, "inp_embd", None)
        if layer == 0
        else _find(trace, pass_index, "l_out", layer - 1)
    )
    if layer == 0 and after_rec is not None and before_rec is None:
        # Seen on real traces: `inp_embd` is in the capture set and still absent,
        # because the embedding lookup runs before the callback sees the graph.
        # Block 0's input is then genuinely unknown — not zero.
        raise StepperError(
            "block 0's input (`inp_embd`) was not recorded — on this llama.cpp "
            "build the embedding lookup happens outside the traced graph, so "
            "there is nothing to subtract"
        )
    if after_rec is None or before_rec is None:
        raise StepperError(
            f"layer {layer} needs both its own `l_out` and the stream entering it; "
            "this trace's capture set did not keep them"
        )
    after = record_array(trace, after_rec)[0, 0].astype(np.float64)  # [tokens, embd]
    before = record_array(trace, before_rec)[0, 0].astype(np.float64)
    if after.shape[1] != before.shape[1]:
        raise StepperError("the two residual tensors have different widths")
    width = min(after.shape[0], before.shape[0])
    a, b = after[-width:], before[-width:]
    delta = a - b
    d_norm = np.linalg.norm(delta, axis=1)
    b_norm = np.linalg.norm(b, axis=1)
    a_norm = np.linalg.norm(a, axis=1)
    denom = a_norm * b_norm
    cosine = np.divide(
        (a * b).sum(axis=1), denom, out=np.zeros_like(denom), where=denom > 0
    )
    relative = np.divide(d_norm, b_norm, out=np.zeros_like(d_norm), where=b_norm > 0)
    n_tokens = prompt_length(trace) if pass_index == 0 else 1
    return {
        "layer": layer,
        "passIndex": pass_index,
        "positions": list(
            range(max(0, n_tokens - width), max(0, n_tokens - width) + width)
        ),
        "deltaNorm": [float(v) for v in d_norm],
        "beforeNorm": [float(v) for v in b_norm],
        "afterNorm": [float(v) for v in a_norm],
        "relative": [float(v) for v in relative],
        "cosine": [float(v) for v in cosine],
    }


def experts(trace: traces.Trace, pass_index: int = 0) -> dict[str, Any]:
    """Mixture-of-experts routing for one pass, from the captured router nodes.

    `ffn_moe_topk-L` (i32, `[n_used, n_tokens]`) is which experts each token was
    sent to, `ffn_moe_weights-L` (`[1, n_used, n_tokens]`) how much each counted,
    and `ffn_moe_probs-L` (`[n_expert, n_tokens]`) the router's whole
    distribution — the only one of the three that knows how many experts exist.
    A dense model has none of them, and that is reported as `moe: false`, not as
    an empty atlas.
    """
    layers: dict[int, dict[str, Any]] = {}
    for record in trace.records:
        if record.pass_index != pass_index or record.layer is None:
            continue
        name = record.name
        slot: str | None = None
        if name.startswith("ffn_moe_topk"):
            slot = "topk"
        elif name.startswith("ffn_moe_weights") and not name.startswith(
            ("ffn_moe_weights_sum", "ffn_moe_weights_norm", "ffn_moe_weights_scaled")
        ):
            slot = "weights"
        elif name.startswith("ffn_moe_probs"):
            slot = "probs"
        if slot is None or record.length <= 0:
            continue
        layers.setdefault(record.layer, {})[slot] = record_array(trace, record)

    if not any("topk" in v for v in layers.values()):
        return {"moe": False, "passIndex": pass_index, "layers": []}

    n_expert = 0
    for parts in layers.values():
        if "probs" in parts:
            n_expert = max(n_expert, int(parts["probs"].shape[-1]))
    rows: list[dict[str, Any]] = []
    for layer in sorted(layers):
        parts = layers[layer]
        topk = parts.get("topk")
        if topk is None:
            continue
        chosen = topk.reshape(-1, topk.shape[-1]).astype(np.int64)  # [tokens, n_used]
        n_expert = max(n_expert, int(chosen.max()) + 1 if chosen.size else 0)
        weights = parts.get("weights")
        w = (
            weights.reshape(chosen.shape).astype(np.float64)
            if weights is not None and weights.size == chosen.size
            else None
        )
        rows.append(
            {
                "layer": layer,
                "selections": chosen.tolist(),
                "weights": None if w is None else [[float(x) for x in r] for r in w],
            }
        )
    for row in rows:
        counts = np.zeros(max(1, n_expert), dtype=np.int64)
        for token in row["selections"]:
            for e in token:
                if 0 <= e < counts.size:
                    counts[e] += 1
        row["counts"] = counts.tolist()
    return {"moe": True, "passIndex": pass_index, "nExpert": n_expert, "layers": rows}

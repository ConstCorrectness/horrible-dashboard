"""Read a captured activation as *words* — the logit lens, and the shape a
Jacobian lens drops into.

The trace store answers "what were the numbers in `ffn_out-15`". This module
answers the question Neuronpedia's Jacobian Lens asks: **what is the model
disposed to say from this activation?** The readout is

    lens_logits = unembed(norm(J_L @ h))

where `h` is the residual stream at (layer `L`, position `p`) — which
`tracer.py` already captures as `l_out-L` — and `J_L` is a per-layer transport
matrix. Anthropic's J-lens fits `J_L = E[dh_final/dh_L]` over a corpus, which
needs autograd this backend deliberately does not have. Set `J_L = I` and the
same machinery is the classic **logit lens**, which needs no artifact at all and
works on every GGUF on this machine. That is what ships here; `LensSpec` names
the transport so a fitted `J` is a file, not a rewrite.

Three things make this honest rather than plausible:

- **The self-check is the correctness argument.** Per-architecture details are
  exactly the kind that produce confident wrong numbers instead of errors: Gemma
  folds `1 +` into its norm weights at convert time, Gemma 2 softcaps final
  logits, most models tie `output.weight` to `token_embd.weight`. So at the last
  layer the identity lens must reproduce the trace's *own* captured
  `result_output`, and `result_norm` independently checks the norm alone. A grid
  reports `verified` in three states — `true`, `false`, and `unavailable` (the
  trace captured no `result_output` to check against) — and a `false` is
  rendered as a banner, never as numbers. This is the `.cgz` round trip and the
  ggml ABI self-check a third time: agreement with something independently
  known, or nothing.
- **A missing dequantizer refuses.** `traces.decode` refuses a dtype it never
  wrote; this refuses a quantization it cannot read, by name, rather than
  returning a plausible matrix.
- **The unembedding matrix is never materialized.** For gemma-3-12b it is
  262144 x 3840 — 2 GB in fp16. Weight chunks stream past a stacked matrix of
  every cell in the grid, so the whole grid costs **one** pass over the weights
  rather than one per cell.

This is the one place in the codebase that seeks past a GGUF's `data_offset`.
`interpretability/gguf.py` stays header-only on purpose (a structure question
must not cost a 20 GB read); reading weight *values* is a different problem with
a different budget, and it lives here.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import numpy as np

from backend import paths
from backend.modules.interpretability import gguf
from backend.modules.llamacpp import traces

logger = logging.getLogger(__name__)

#: Vocabulary rows dequantized per step. Large enough that the matmul dominates
#: the Python loop, small enough that a 262k-row matrix never lands in memory.
CHUNK_ROWS = 4096

#: Default top-k per grid cell. The grid shows one word per cell; k is what the
#: cell *detail* and the rank superscript are computed from, so it is small.
DEFAULT_K = 5

#: Ceiling for the dequantized-unembedding cache, in bytes. Above this the
#: weights are re-read from the GGUF every time rather than spending tens of
#: gigabytes of the user's disk on a speed-up they did not ask for.
DEFAULT_CACHE_BUDGET_GB = 4.0

#: How closely the identity lens must reproduce the trace's own `result_output`
#: for a grid to call itself verified. Generous on purpose: the trace stores fp16
#: by default, and the comparison is between two float paths that agree on the
#: answer, not two bit-identical computations.
VERIFY_ATOL = 0.35

#: The row index the embedding occupies. `l_out-0` is the first block's output,
#: so the input embedding is the layer before it and needs a number that cannot
#: collide with a real block.
EMBEDDING_LAYER = -1


class LensError(Exception):
    """The lens cannot be computed. Always caught at the route boundary."""


# --- lens specifications ----------------------------------------------------


def lens_root() -> Path:
    return paths.data_dir() / "llamacpp" / "lens"


@dataclass(frozen=True)
class LensSpec:
    """Which transport to apply before unembedding.

    `identity` is synthetic — there is no directory and no download, and it is
    labelled "identity (logit lens)" everywhere so it is never mistaken for a
    fitted J-space readout. A `jacobian` lens is a directory of one `J_l` per
    layer plus a `lens.json` recording where it came from.
    """

    id: str
    kind: str  # "identity" | "jacobian"
    label: str
    provenance: str = ""
    directory: Path | None = None
    layers: list[int] = field(default_factory=list)
    d_model: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "provenance": self.provenance,
            "layers": list(self.layers),
            "dModel": self.d_model,
        }


IDENTITY = LensSpec(
    id="identity",
    kind="identity",
    label="identity (logit lens)",
    provenance=(
        "No fitted transport — the residual stream is normed and unembedded "
        "directly. This is the classic logit lens, not a J-space readout."
    ),
)


def available_lenses(model_sha: str) -> list[LensSpec]:
    """Every lens that can be applied to this model. `identity` is always first."""
    found = [IDENTITY]
    root = lens_root() / _safe_component(model_sha) if model_sha else None
    if root is not None and root.is_dir():
        for directory in sorted(root.iterdir()):
            spec = _load_lens_dir(directory)
            if spec is not None:
                found.append(spec)
    return found


def resolve_lens(lens_id: str, model_sha: str) -> LensSpec:
    for spec in available_lenses(model_sha):
        if spec.id == lens_id:
            return spec
    raise LensError(f"no lens {lens_id!r} for this model")


def _load_lens_dir(directory: Path) -> LensSpec | None:
    manifest = directory / "lens.json"
    if not manifest.is_file():
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.info("llamacpp: unreadable lens manifest %s (%s)", manifest, exc)
        return None
    return LensSpec(
        id=directory.name,
        kind=str(data.get("kind") or "jacobian"),
        label=str(data.get("label") or directory.name),
        provenance=str(data.get("provenance") or ""),
        directory=directory,
        layers=[int(v) for v in data.get("layers") or []],
        d_model=int(data.get("dModel") or 0),
    )


def _safe_component(value: str) -> str:
    cleaned = "".join(c for c in value if c.isalnum() or c in "._-")
    if not cleaned or cleaned.startswith("."):
        raise LensError(f"invalid path component {value!r}")
    return cleaned


def transport(spec: LensSpec, layer: int, h: np.ndarray) -> np.ndarray:
    """Apply the lens's transport to one residual vector."""
    if spec.kind == "identity" or spec.directory is None:
        return h
    path = spec.directory / f"J_{layer}.npy"
    if not path.is_file():
        raise LensError(f"lens {spec.id!r} has no matrix for layer {layer}")
    matrix = np.load(path, mmap_mode="r")
    if matrix.shape[1] != h.shape[0]:
        raise LensError(
            f"lens {spec.id!r} layer {layer} is {matrix.shape}, "
            f"but the residual is {h.shape[0]} wide"
        )
    return np.asarray(matrix, dtype=np.float32) @ h


# --- the model's output head ------------------------------------------------


@dataclass
class Unembedding:
    """Everything needed to turn a residual vector into vocabulary logits."""

    path: Path
    #: `output.weight`, or `token_embd.weight` when the model ties them.
    tensor: gguf.TensorInfo
    #: True when the output head is the embedding table read backwards.
    tied: bool
    data_offset: int
    n_embd: int
    n_vocab: int
    norm_weight: np.ndarray | None
    norm_eps: float
    #: Gemma 2's `tanh(x/c) * c` on the final logits. None when the model has none.
    logit_softcap: float | None
    architecture: str
    #: `tokenizer.ggml.model` — "gpt2", "llama", … It decides how a raw vocabulary
    #: entry is rendered, and re-reading the header for it would re-parse a
    #: 256k-entry string array every time a cell is drawn.
    tokenizer_model: str
    vocab: list[str]
    token_types: list[int]

    @property
    def quant(self) -> str:
        return self.tensor.type_name

    def to_dict(self) -> dict[str, Any]:
        return {
            "tensor": self.tensor.name,
            "tied": self.tied,
            "quant": self.quant,
            "nEmbd": self.n_embd,
            "nVocab": self.n_vocab,
            "normEps": self.norm_eps,
            "logitSoftcap": self.logit_softcap,
            "architecture": self.architecture,
            "tokenizerModel": self.tokenizer_model,
        }


def _metadata_suffix(metadata: dict[str, Any], suffix: str) -> Any:
    """The value of the one key ending `suffix`.

    GGUF namespaces every architectural constant under the architecture's own
    name (`gemma3.attention.layer_norm_rms_epsilon`), so a lookup table of
    families would be a table we have to keep chasing. `tokenizer.py` reaches
    for `.context_length` the same way and for the same reason.
    """
    for key, value in metadata.items():
        if str(key).endswith(suffix):
            return value
    return None


def load_unembedding(model_path: str | Path) -> Unembedding:
    """Locate and describe the output head of a GGUF. Header reads only."""
    path = Path(model_path)
    try:
        header = gguf.read_header(path)
    except (gguf.GgufError, OSError) as exc:
        # A missing or unreadable file is the same answer as a malformed one: we
        # cannot describe this model's output head. `read_header` lets OSError
        # through, so catching only GgufError would 500 on a stale path.
        raise LensError(f"cannot read {path.name}: {exc}") from exc

    by_name = {t.name: t for t in header.tensors}
    tensor = by_name.get("output.weight")
    tied = False
    if tensor is None:
        tensor = by_name.get("token_embd.weight")
        tied = True
    if tensor is None:
        raise LensError(
            f"{path.name} has neither output.weight nor token_embd.weight — "
            "there is no output head to unembed against"
        )
    if len(tensor.shape) != 2:
        raise LensError(f"{tensor.name} is {len(tensor.shape)}-dimensional, expected 2")

    # ggml stores the fastest-varying dimension first, so an output head is
    # [n_embd, n_vocab] and one vocabulary entry is n_embd *contiguous* values.
    # That is what makes chunk-by-rows streaming possible at all.
    n_embd, n_vocab = int(tensor.shape[0]), int(tensor.shape[1])

    norm = by_name.get("output_norm.weight")
    norm_weight: np.ndarray | None = None
    if norm is not None:
        norm_weight = _read_tensor(path, header, norm).reshape(-1)
        if norm_weight.shape[0] != n_embd:
            raise LensError(
                f"output_norm.weight is {norm_weight.shape[0]} wide, "
                f"but the output head expects {n_embd}"
            )

    eps = _metadata_suffix(header.metadata, ".attention.layer_norm_rms_epsilon")
    if eps is None:
        eps = _metadata_suffix(header.metadata, ".attention.layer_norm_epsilon")
    softcap = _metadata_suffix(header.metadata, ".final_logit_softcapping")

    return Unembedding(
        path=path,
        tensor=tensor,
        tied=tied,
        data_offset=header.data_offset,
        n_embd=n_embd,
        n_vocab=n_vocab,
        norm_weight=norm_weight,
        norm_eps=float(eps) if isinstance(eps, (int, float)) else 1e-5,
        logit_softcap=float(softcap) if isinstance(softcap, (int, float)) else None,
        architecture=str(header.metadata.get("general.architecture") or ""),
        tokenizer_model=str(header.metadata.get("tokenizer.ggml.model") or ""),
        vocab=[str(v) for v in header.metadata.get("tokenizer.ggml.tokens") or []],
        token_types=[
            int(v) for v in header.metadata.get("tokenizer.ggml.token_type") or []
        ],
    )


def _read_tensor(
    path: Path, header: gguf.GgufFile, tensor: gguf.TensorInfo
) -> np.ndarray:
    """One whole tensor as float32. Only for small ones — norms, not heads."""
    if tensor.n_bytes is None:
        raise LensError(
            f"{tensor.name} is {tensor.type_name}, whose block size we do not know"
        )
    with open(path, "rb") as handle:
        handle.seek(header.data_offset + tensor.offset)
        raw = handle.read(tensor.n_bytes)
    if len(raw) != tensor.n_bytes:
        raise LensError(f"{tensor.name} is truncated in {path.name}")
    return _dequantize(raw, tensor.type_id, tensor.type_name, tensor.elements)


def _dequantize(raw: bytes, type_id: int, type_name: str, elements: int) -> np.ndarray:
    """Bytes to float32, via llama.cpp's own numpy dequantizers.

    A quantization we cannot read is named and refused. The alternative —
    guessing a block layout — produces a matrix that is wrong in a way no
    downstream check would notice, which is the failure mode this whole module
    is arranged to prevent.
    """
    from gguf import GGMLQuantizationType
    from gguf.quants import dequantize

    try:
        qtype = GGMLQuantizationType(type_id)
    except ValueError as exc:
        raise LensError(f"unknown ggml type {type_id} ({type_name})") from exc
    try:
        values = dequantize(np.frombuffer(raw, dtype=np.uint8), qtype)
    except NotImplementedError as exc:
        raise LensError(
            f"cannot read the output head — it is {qtype.name}, and there is no "
            "dequantizer for that. Trace a model stored in a quantization we can read."
        ) from exc
    return np.asarray(values, dtype=np.float32).reshape(-1)[:elements]


# --- streaming the output head ----------------------------------------------


def _cache_budget_bytes() -> int:
    value = traces._setting("llamacpp.lensCacheGb", DEFAULT_CACHE_BUDGET_GB)
    try:
        return int(max(float(value), 0.0) * 1024**3)
    except (TypeError, ValueError):
        return int(DEFAULT_CACHE_BUDGET_GB * 1024**3)


def cache_path(model_sha: str) -> Path:
    return lens_root() / _safe_component(model_sha) / "unembed.f16.npy"


def _row_bytes(un: Unembedding) -> int:
    """Bytes per vocabulary row of the output head."""
    if un.tensor.n_bytes is None:
        raise LensError(
            f"the output head is {un.quant}, whose block size we do not know — "
            "its byte length cannot be computed, so it cannot be read"
        )
    row_bytes, remainder = divmod(un.tensor.n_bytes, un.n_vocab)
    if remainder:
        raise LensError(
            f"{un.tensor.name} is {un.tensor.n_bytes} bytes over {un.n_vocab} rows, "
            "which is not a whole number of bytes per row"
        )
    return row_bytes


def _cached_head(cached: Path | None, un: Unembedding) -> np.ndarray | None:
    """The memory-mapped fp16 output head for this model, if we have one.

    A cache whose shape does not match these weights is deleted rather than
    used: `modelSha` digests the GGUF's first megabyte, so a collision is
    conceivable, and a silently wrong output head produces a grid that reads
    perfectly and means nothing.

    Deleting it needs the mapping released first. On Windows an open memmap
    keeps the file undeletable, so unlinking while `matrix` is still alive
    raises — leaving the stale cache in place to be rejected again on every
    future grid.
    """
    if cached is None or not cached.is_file():
        return None
    try:
        matrix = np.load(cached, mmap_mode="r")
    except (OSError, ValueError) as exc:
        logger.info("llamacpp: unreadable lens cache %s (%s)", cached, exc)
        return None
    if matrix.shape == (un.n_vocab, un.n_embd) and matrix.dtype == np.float16:
        return matrix

    logger.info(
        "llamacpp: discarding a lens cache that is %s %s, not %s float16",
        matrix.shape,
        matrix.dtype,
        (un.n_vocab, un.n_embd),
    )
    mapping = getattr(matrix, "_mmap", None)
    del matrix
    if mapping is not None:
        mapping.close()
    try:
        cached.unlink(missing_ok=True)
    except OSError as exc:
        # Still locked by something else: reading the real weights is correct
        # either way, so this is a slow grid rather than a failed one.
        logger.info("llamacpp: could not remove the stale lens cache (%s)", exc)
    return None


def _weight_chunks(un: Unembedding, model_sha: str) -> Iterator[tuple[int, np.ndarray]]:
    """Yield `(first_vocab_row, block)` covering the whole output head.

    Dequantizing a 1 GB head takes seconds, so the fp16 result is cached when it
    fits the budget and memory-mapped on every later grid. The cache is keyed by
    `modelSha`, which is the same key the trace itself carries — two
    quantizations of one model are two caches, as they must be.
    """
    cached = cache_path(model_sha) if model_sha else None
    matrix = _cached_head(cached, un)
    if matrix is not None:
        for start in range(0, un.n_vocab, CHUNK_ROWS):
            block = np.asarray(matrix[start : start + CHUNK_ROWS], dtype=np.float32)
            yield start, block
        return

    row_bytes = _row_bytes(un)
    fp16_bytes = un.n_vocab * un.n_embd * 2
    writing = cached is not None and fp16_bytes <= _cache_budget_bytes()
    buffer = np.empty((un.n_vocab, un.n_embd), dtype=np.float16) if writing else None

    with open(un.path, "rb") as handle:
        handle.seek(un.data_offset + un.tensor.offset)
        for start in range(0, un.n_vocab, CHUNK_ROWS):
            rows = min(CHUNK_ROWS, un.n_vocab - start)
            raw = handle.read(rows * row_bytes)
            if len(raw) != rows * row_bytes:
                raise LensError(f"{un.tensor.name} is truncated in {un.path.name}")
            block = _dequantize(
                raw, un.tensor.type_id, un.quant, rows * un.n_embd
            ).reshape(rows, un.n_embd)
            if buffer is not None:
                buffer[start : start + rows] = block.astype(np.float16)
            yield start, block

    if buffer is not None and cached is not None:
        try:
            cached.parent.mkdir(parents=True, exist_ok=True)
            tmp = cached.with_name(cached.name + ".part")
            # Written through an open handle, not a path: `np.save` appends
            # `.npy` to a path that lacks it, so saving to `unembed.f16.part`
            # silently produces `unembed.f16.part.npy` and the rename that
            # follows finds nothing — a cache that is never installed and a
            # gigabyte of litter per grid.
            with tmp.open("wb") as handle:
                np.save(handle, buffer)
            tmp.replace(cached)
        except OSError as exc:
            # A cache we could not write costs a re-read, never a failed grid.
            logger.info("llamacpp: could not write the lens cache (%s)", exc)


# --- the arithmetic ---------------------------------------------------------


def rms_norm(h: np.ndarray, weight: np.ndarray | None, eps: float) -> np.ndarray:
    """The model's final norm, applied to an intermediate residual.

    A residual halfway up the stack has not been through the output norm, so
    unembedding it raw compares vectors of the wrong scale against the head and
    yields a ranking dominated by whichever rows happen to be large. Gemma's
    `1 +` is folded into the stored weight at conversion time, so there is no
    family branch here — and if that ever stops being true, `verify()` says so
    rather than the grid quietly changing its answers.
    """
    scale = 1.0 / math.sqrt(float(np.mean(np.square(h.astype(np.float64)))) + eps)
    out = h.astype(np.float32) * np.float32(scale)
    return out if weight is None else out * weight.astype(np.float32)


def softcap(logits: np.ndarray, cap: float | None) -> np.ndarray:
    if not cap:
        return logits
    return np.tanh(logits / np.float32(cap)) * np.float32(cap)


def _top_k_columns(
    un: Unembedding, model_sha: str, columns: np.ndarray, k: int
) -> tuple[np.ndarray, np.ndarray]:
    """Top-k vocabulary ids and logits for every column of `columns`.

    `columns` is `[n_embd, n_cells]` — the whole grid stacked. Weight chunks
    stream past it once, so a 48x20 grid costs one pass over the output head
    rather than 960. Merging a running top-k per chunk is what lets that pass
    keep only `k` values per cell instead of a `[n_vocab, n_cells]` matrix.
    """
    n_cells = columns.shape[1]
    keep = min(k, un.n_vocab)
    best_vals = np.full((n_cells, keep), -np.inf, dtype=np.float32)
    best_ids = np.zeros((n_cells, keep), dtype=np.int64)

    for start, block in _weight_chunks(un, model_sha):
        # [rows, n_embd] @ [n_embd, n_cells] -> [rows, n_cells], then transposed
        # so every cell's candidates are contiguous for the merge.
        scores = (block @ columns).T
        scores = softcap(scores, un.logit_softcap)
        rows = scores.shape[1]
        take = min(keep, rows)
        idx = np.argpartition(-scores, take - 1, axis=1)[:, :take]
        vals = np.take_along_axis(scores, idx, axis=1)
        merged_vals = np.concatenate([best_vals, vals], axis=1)
        merged_ids = np.concatenate([best_ids, idx + start], axis=1)
        order = np.argsort(-merged_vals, axis=1, kind="stable")[:, :keep]
        best_vals = np.take_along_axis(merged_vals, order, axis=1)
        best_ids = np.take_along_axis(merged_ids, order, axis=1)

    return best_ids, best_vals


# --- reading the trace ------------------------------------------------------


@dataclass
class Residuals:
    """The residual stream of one pass, layer by layer.

    **The rows are ragged, and that is not a bug in the trace.** llama.cpp prunes
    its graph to what the pass actually needs, so on a prompt pass the last
    block's `l_out` is one column wide — only the final position's residual is
    required to produce logits — while every earlier block is the full width. A
    narrow tensor covers the *tail* of the sequence, which is why
    `column_of` exists and why reading `matrix[:, position]` directly would
    quietly read the wrong token before it started raising.
    """

    #: layer index -> [n_embd, k], where k <= n_tokens and covers the last k
    #: positions.
    by_layer: dict[int, np.ndarray]
    n_tokens: int
    n_embd: int
    #: The captured logits, when the trace has them — the self-check's evidence.
    result_output: np.ndarray | None
    #: The captured post-norm residual, which checks `rms_norm` on its own.
    result_norm: np.ndarray | None


def _record_matrix(
    trace: traces.Trace, record: traces.TraceRecord
) -> np.ndarray | None:
    if record.length <= 0:
        return None  # a summary record carries statistics instead of data
    with trace.blob.open("rb") as handle:
        handle.seek(record.offset)
        payload = handle.read(record.length)
    values = traces.decode_array(payload, record.dtype)
    rows = int(record.ne[0]) if record.ne else values.size
    if rows <= 0:
        return None
    cols = values.size // rows
    return values[: rows * cols].reshape(cols, rows).T  # ggml is column-major


def read_residuals(trace: traces.Trace, pass_index: int = 0) -> Residuals:
    """Every residual-stream tensor of one forward pass, keyed by layer."""
    by_layer: dict[int, np.ndarray] = {}
    result_output: np.ndarray | None = None
    result_norm: np.ndarray | None = None

    for record in trace.records:
        if record.pass_index != pass_index:
            continue
        name = record.name
        if name.startswith("inp_embd"):
            matrix = _record_matrix(trace, record)
            if matrix is not None:
                by_layer[EMBEDDING_LAYER] = matrix
        elif name.startswith("l_out") and record.layer is not None:
            matrix = _record_matrix(trace, record)
            if matrix is not None:
                by_layer[record.layer] = matrix
        elif name.startswith("result_output"):
            result_output = _record_matrix(trace, record)
        elif name.startswith("result_norm"):
            result_norm = _record_matrix(trace, record)

    if not by_layer:
        raise LensError(
            "this trace captured no residual stream — a lens needs `inp_embd` "
            "and `l_out`, which a capture set that excluded them will not have"
        )
    sample = next(iter(by_layer.values()))
    return Residuals(
        by_layer=by_layer,
        n_embd=int(sample.shape[0]),
        n_tokens=max(int(m.shape[1]) for m in by_layer.values()),
        result_output=result_output,
        result_norm=result_norm,
    )


def column_of(matrix: np.ndarray, n_tokens: int, position: int) -> int | None:
    """Which column of a pruned tensor holds `position`, or None if it has none.

    "This cell was never computed" is a real answer and a third state, the same
    family as a `summary` record having no bytes. Falling back to the nearest
    column would put the last token's readout under every earlier one and make
    the top of the grid look uncannily prescient.
    """
    width = int(matrix.shape[1])
    index = position - (n_tokens - width)
    return index if 0 <= index < width else None


# --- rendering a token ------------------------------------------------------


# GPT-2 byte-level BPE maps every byte to a printable codepoint, so a raw vocab
# entry reads as "Ġthe" rather than " the". Reversing it is exact and cheap;
# leaving it alone would put mojibake in every grid cell.
def _byte_decoder() -> dict[str, int]:
    printable = (
        list(range(ord("!"), ord("~") + 1))
        + list(range(ord("\xa1"), ord("\xac") + 1))
        + list(range(ord("\xae"), ord("\xff") + 1))
    )
    mapped = list(printable)
    extra = 0
    for byte in range(256):
        if byte not in printable:
            printable.append(byte)
            mapped.append(256 + extra)
            extra += 1
    return {chr(code): byte for byte, code in zip(printable, mapped)}


_BYTE_DECODER = _byte_decoder()


def render_piece(piece: str, tokenizer_model: str) -> str:
    """A vocabulary entry as the text it actually stands for."""
    if tokenizer_model in ("gpt2", "bpe"):
        try:
            raw = bytes(_BYTE_DECODER[c] for c in piece)
        except KeyError:
            return piece
        return raw.decode("utf-8", errors="replace")
    if tokenizer_model in ("llama", "spm", "t5"):
        return piece.replace("▁", " ")
    return piece


# --- the grid ---------------------------------------------------------------


@dataclass
class LensGrid:
    layers: list[int]
    positions: list[int]
    #: `cells[row][col]` — top-k ids/text/logits for each (layer, position), or
    #: None where llama.cpp did not compute that position at that layer.
    cells: list[list[dict[str, Any] | None]]
    lens: LensSpec
    unembedding: dict[str, Any]
    verified: str  # "true" | "false" | "unavailable"
    verify_note: str
    verify_detail: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "layers": self.layers,
            "positions": self.positions,
            "cells": self.cells,
            "lens": self.lens.to_dict(),
            "unembedding": self.unembedding,
            "verified": self.verified,
            "verifyNote": self.verify_note,
            "verifyDetail": self.verify_detail,
        }


def compute_grid(
    trace: traces.Trace,
    *,
    lens_id: str = "identity",
    k: int = DEFAULT_K,
    layers: list[int] | None = None,
    positions: list[int] | None = None,
    pass_index: int = 0,
) -> LensGrid:
    """The layer x position readout for one traced pass."""
    model_path = str(trace.manifest.get("modelPath") or "")
    model_sha = str(trace.manifest.get("modelSha") or "")
    if not model_path:
        raise LensError("this trace records no model path")
    un = load_unembedding(model_path)
    spec = resolve_lens(lens_id, model_sha)
    residuals = read_residuals(trace, pass_index)

    if residuals.n_embd != un.n_embd:
        raise LensError(
            f"the trace's residual stream is {residuals.n_embd} wide but "
            f"{un.tensor.name} expects {un.n_embd} — these are different models"
        )

    rows = sorted(residuals.by_layer)
    if layers:
        wanted = set(layers)
        rows = [r for r in rows if r in wanted]
    cols = list(range(residuals.n_tokens))
    if positions:
        wanted_p = set(positions)
        cols = [c for c in cols if c in wanted_p]
    if not rows or not cols:
        raise LensError("the requested layers or positions are not in this trace")

    # Stack every cell that exists into one [n_embd, n] matrix so the output head
    # is streamed once for the whole grid. The self-check rides along as one
    # extra column rather than a second pass over the weights — and it always
    # uses the *identity* transport, because what it checks is the norm and the
    # output head, not whichever lens the user picked.
    #
    # "Every cell that exists" is load-bearing: the rows are ragged (see
    # `Residuals`), so a grid of len(rows) x len(cols) columns would be partly
    # made up.
    check = _verify_column(un, residuals)
    vectors: list[np.ndarray] = []
    at: dict[tuple[int, int], int] = {}
    for layer in rows:
        matrix = residuals.by_layer[layer]
        for position in cols:
            column = column_of(matrix, residuals.n_tokens, position)
            if column is None:
                continue
            vector = transport(spec, layer, matrix[:, column])
            at[(layer, position)] = len(vectors)
            vectors.append(rms_norm(vector, un.norm_weight, un.norm_eps))
    if check:
        check_index = len(vectors)
        vectors.append(check[3])
    if not vectors:
        raise LensError("none of the requested cells were computed in this pass")
    stacked = np.stack(vectors, axis=1)

    ids, values = _top_k_columns(un, model_sha, stacked, max(k, 8))

    cells: list[list[dict[str, Any] | None]] = []
    for layer in rows:
        row_cells: list[dict[str, Any] | None] = []
        for position in cols:
            index = at.get((layer, position))
            # None, not zeros: llama.cpp did not compute this position at this
            # layer, and a blank cell is the honest rendering of that.
            row_cells.append(
                None if index is None else _cell(un, ids[index][:k], values[index][:k])
            )
        cells.append(row_cells)

    verified, note, detail = verify(
        un,
        residuals,
        spec,
        check,
        None if not check else ids[check_index],
        None if not check else values[check_index],
    )
    return LensGrid(
        layers=rows,
        positions=cols,
        cells=cells,
        lens=spec,
        unembedding=un.to_dict(),
        verified=verified,
        verify_note=note,
        verify_detail=detail,
    )


def _cell(un: Unembedding, ids: np.ndarray, values: np.ndarray) -> dict[str, Any]:
    logits = values.astype(np.float64)
    shifted = np.exp(logits - logits.max())
    # A softmax over only the top-k is not the model's distribution and is not
    # presented as one: it is the relative weight *among the shown candidates*,
    # which is what a cell's shading means.
    probs = shifted / shifted.sum() if shifted.sum() else np.zeros_like(shifted)
    return {
        "ids": [int(v) for v in ids],
        "texts": [
            render_piece(
                un.vocab[int(v)] if int(v) < len(un.vocab) else "", un.tokenizer_model
            )
            for v in ids
        ],
        "logits": [round(float(v), 4) for v in values],
        "relProbs": [round(float(v), 5) for v in probs],
    }


# --- the self-check ---------------------------------------------------------


def _verify_column(
    un: Unembedding, residuals: Residuals
) -> tuple[int, int, int, np.ndarray] | None:
    """`(layer, position, logits_column, normed_vector)` for the self-check.

    The topmost captured block output at the **last** position: that is the one
    activation whose unembedding the model itself computed and stored, so it is
    the only place a comparison is possible.

    Both tensors are addressed from the end rather than from the start. On a
    prompt pass llama.cpp computes logits for the final token only, so
    `result_output` is one column wide covering position n-1 — taking column 0 of
    it and position 0 of the residual would compare the last token's logits
    against the first token's activation, which disagrees wildly and would report
    every trace as unverified.
    """
    if residuals.result_output is None or residuals.result_output.shape[1] < 1:
        return None
    blocks = [layer for layer in residuals.by_layer if layer >= 0]
    if not blocks:
        return None
    top = max(blocks)
    matrix = residuals.by_layer[top]
    position = residuals.n_tokens - 1
    column = column_of(matrix, residuals.n_tokens, position)
    if column is None:
        return None
    normed = rms_norm(matrix[:, column], un.norm_weight, un.norm_eps)
    return top, position, residuals.result_output.shape[1] - 1, normed


def verify(
    un: Unembedding,
    residuals: Residuals,
    spec: LensSpec,
    check: tuple[int, int, int, np.ndarray] | None,
    ids: np.ndarray | None,
    values: np.ndarray | None,
) -> tuple[str, str, dict[str, Any]]:
    """Does the identity lens reproduce the trace's own logits?

    Only the identity lens can be checked this way — a fitted `J` deliberately
    does *not* reproduce the model's output, that is the whole point of it — so a
    jacobian lens reports `unavailable` while still saying whether the identity
    path agreed, which is what tells you the *machinery* is sound even when the
    lens on screen cannot be validated.
    """
    if check is None or ids is None or values is None:
        return (
            "unavailable",
            "This trace captured no `result_output`, so there is nothing to check "
            "the lens against. Re-run it with the output head in the capture set.",
            {},
        )
    layer, position, logits_column, normed = check
    captured = residuals.result_output
    assert captured is not None  # implied by check being non-None
    detail: dict[str, Any] = {"layer": layer, "position": position}

    if captured.shape[0] != un.n_vocab:
        return (
            "unavailable",
            f"The captured logits are {captured.shape[0]} wide and the output head is "
            f"{un.n_vocab} — they are not the same vocabulary.",
            detail,
        )

    # The norm alone, first: `rms_norm` and the output head are separately
    # wrong-able, and a disagreement that names neither is a worse answer than no
    # answer. `result_norm` is the model's own post-norm residual.
    if residuals.result_norm is not None and residuals.result_norm.shape[1] >= 1:
        # From the end, for the same reason `result_output` is: the final norm is
        # computed for the positions that produce logits, not for all of them.
        theirs_norm = residuals.result_norm[:, -1]
        detail["normMaxAbsDiff"] = round(float(np.max(np.abs(normed - theirs_norm))), 5)

    theirs = captured[:, logits_column]
    top = int(np.argmax(theirs))
    detail["argmaxAgrees"] = bool(int(ids[0]) == top)
    # Compared on our own top candidates rather than the whole vocabulary: those
    # are the values the grid actually shows, and they ride out of the same
    # streaming pass, so the check costs nothing extra.
    detail["maxAbsDiff"] = round(
        float(
            max(
                abs(float(values[j]) - float(theirs[int(ids[j])]))
                for j in range(len(ids))
            )
        ),
        4,
    )
    detail["topToken"] = render_piece(
        un.vocab[top] if top < len(un.vocab) else "", un.tokenizer_model
    )

    ok = bool(detail["argmaxAgrees"]) and detail["maxAbsDiff"] <= VERIFY_ATOL
    if spec.kind != "identity":
        return (
            "unavailable",
            (
                "A fitted lens does not reproduce the model's output by design, so it "
                "cannot be checked against it. The identity lens on this same trace "
                + ("agrees" if ok else "does NOT agree")
                + " with the captured logits."
            ),
            detail,
        )
    if ok:
        return (
            "true",
            "The identity lens reproduces this trace's own captured logits at the "
            "final layer, so the norm, the output head and the vocabulary all line up.",
            detail,
        )
    return (
        "false",
        (
            "The identity lens does NOT reproduce this trace's captured logits "
            f"(largest disagreement {detail['maxAbsDiff']}). Something about this "
            "architecture's output path is not what this code assumes — treat every "
            "cell as unverified."
        ),
        detail,
    )


# --- tracking one token -----------------------------------------------------


def _weight_row(un: Unembedding, model_sha: str, token_id: int) -> np.ndarray:
    """One vocabulary row of the output head.

    A row is a whole number of quantization blocks — blocks never span rows in
    GGUF — so a single token's weights can be seeked to directly instead of
    streaming the whole head to find them.
    """
    matrix = _cached_head(cache_path(model_sha) if model_sha else None, un)
    if matrix is not None:
        return np.asarray(matrix[token_id], dtype=np.float32)
    row_bytes = _row_bytes(un)
    with open(un.path, "rb") as handle:
        handle.seek(un.data_offset + un.tensor.offset + token_id * row_bytes)
        raw = handle.read(row_bytes)
    if len(raw) != row_bytes:
        raise LensError(f"{un.tensor.name} is truncated in {un.path.name}")
    return _dequantize(raw, un.tensor.type_id, un.quant, un.n_embd)


def track_token(
    trace: traces.Trace,
    token_id: int,
    *,
    lens_id: str = "identity",
    pass_index: int = 0,
) -> dict[str, Any]:
    """One vocabulary token's logit and rank at every (layer, position).

    The dual of a node pin: instead of asking what a cell says, ask where a word
    you care about is, everywhere. That is what turns a grid from a picture into
    a story — "Paris was already rank 400 by layer 12" is a finding; "layer 30
    says Paris" is a screenshot.

    A rank needs every logit, but only as a *count* of how many beat this one, so
    the pass keeps one integer per cell rather than a `[n_vocab, n_cells]` matrix.
    """
    model_path = str(trace.manifest.get("modelPath") or "")
    model_sha = str(trace.manifest.get("modelSha") or "")
    if not model_path:
        raise LensError("this trace records no model path")
    un = load_unembedding(model_path)
    spec = resolve_lens(lens_id, model_sha)
    residuals = read_residuals(trace, pass_index)
    if not 0 <= token_id < un.n_vocab:
        raise LensError(f"token {token_id} is outside this model's vocabulary")

    rows = sorted(residuals.by_layer)
    cols = list(range(residuals.n_tokens))
    vectors: list[np.ndarray] = []
    at: dict[tuple[int, int], int] = {}
    for layer in rows:
        matrix = residuals.by_layer[layer]
        for position in cols:
            column = column_of(matrix, residuals.n_tokens, position)
            if column is None:
                continue
            vector = transport(spec, layer, matrix[:, column])
            at[(layer, position)] = len(vectors)
            vectors.append(rms_norm(vector, un.norm_weight, un.norm_eps))
    if not vectors:
        raise LensError("this pass computed no residuals to track against")
    stacked = np.stack(vectors, axis=1)

    target = softcap(_weight_row(un, model_sha, token_id) @ stacked, un.logit_softcap)
    better = np.zeros(stacked.shape[1], dtype=np.int64)
    for _start, block in _weight_chunks(un, model_sha):
        scores = softcap(block @ stacked, un.logit_softcap)
        better += np.sum(scores > target[None, :], axis=0)

    def grid_of(values: Any, cast: Any) -> list[list[Any]]:
        return [
            [
                None
                if at.get((layer, position)) is None
                else cast(values[at[(layer, position)]])
                for position in cols
            ]
            for layer in rows
        ]

    return {
        "tokenId": token_id,
        "text": render_piece(
            un.vocab[token_id] if token_id < len(un.vocab) else "", un.tokenizer_model
        ),
        "layers": rows,
        "positions": cols,
        "logits": grid_of(target, lambda v: round(float(v), 4)),
        # `better` counts strictly-greater rows, so the token's own row never
        # counts itself and rank 1 is the argmax.
        "ranks": grid_of(better, lambda v: int(v) + 1),
        "lens": spec.to_dict(),
    }

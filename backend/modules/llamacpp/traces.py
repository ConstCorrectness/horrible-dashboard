"""The trace store: snapshot activations on disk, scrubbed offline.

A forward pass is not a stream. Attention weights alone are fp32
`[n_kv, n_tokens, n_head]` — ~33 MB *per layer per pass* at 512 tokens and 32
heads — so "watch the activations live" is a websocket carrying a gigabyte a
second to a pane that can render none of it. Instead a run writes a snapshot and
the pane scrubs it afterwards, which also makes a trace a thing you can keep,
compare and delete.

Layout, which is hassault's cube-grid precedent literally:

```
$HORRIBLE_DATA_DIR/llamacpp/traces/<trace_id>/
  manifest.json   # provenance + the record list
  tokens.json     # the tokens this pass actually ran on
  tensors.bin     # one append-only blob, records in capture order
```

One blob rather than a file per layer: per-layer files give thousands of handles
and *still* can't address a single node inside a layer, whereas blob + `Range`
gives both — the pane fetches exactly the bytes of the record it is showing.

Two honesty rules are enforced here rather than left to the pane:

- **Every record declares a `fidelity`.** `full` is the tensor; `fp16` is a
  downcast of it; `summary` is statistics *instead of* the tensor. A `summary`
  record has no bytes and must never be rendered as though it were data.
- **`modelSha` says what it hashed.** Digesting 20 GB to open a pane would be
  absurd, so it is a digest of the GGUF header region plus the file size, and
  `modelShaScope` names that. It is enough to catch "a different model" and it
  does not pretend to be a whole-file checksum.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import struct
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, BinaryIO
from backend import paths

logger = logging.getLogger(__name__)

#: How much of a GGUF goes into `modelSha`. The header, tensor index and the
#: start of the first tensor all live well inside this, so two different
#: quantizations of the same model never collide.
SHA_PREFIX_BYTES = 1 << 20

#: Default ceiling for the whole trace directory. Small on purpose: traces are
#: cheap to regenerate and a single careless run can be a gigabyte.
DEFAULT_TRACE_BUDGET_GB = 2.0

#: Hard cap on traced tokens regardless of what the caller asks for. Attention
#: capture is quadratic in this, and a pre-flight estimate the user clicked past
#: is not a reason to let a pane fill the disk.
MAX_TRACE_TOKENS = 512

FIDELITIES = ("full", "fp16", "summary")


def traces_root() -> Path:
    return paths.data_dir() / "llamacpp" / "traces"


def _setting(key: str, default: Any) -> Any:
    from backend.modules.settings.routes import get_value

    return get_value(key, default)


def budget_bytes() -> int:
    value = _setting("llamacpp.traceBudgetGb", DEFAULT_TRACE_BUDGET_GB)
    try:
        gb = float(value)
    except (TypeError, ValueError):
        gb = DEFAULT_TRACE_BUDGET_GB
    return int(max(gb, 0.0) * 1024**3)


def model_sha(path: Path) -> str:
    """A digest of the GGUF's first megabyte and its size.

    See the module docstring: this is deliberately not a whole-file hash, and
    every manifest carries `modelShaScope` so nothing downstream can mistake it
    for one.
    """
    digest = hashlib.sha256()
    size = path.stat().st_size
    digest.update(str(size).encode())
    with path.open("rb") as handle:
        digest.update(handle.read(SHA_PREFIX_BYTES))
    return digest.hexdigest()


# --- records ---------------------------------------------------------------


@dataclass
class TraceRecord:
    """One captured node.

    `offset`/`length` address `tensors.bin`; a `summary` record has
    `length == 0` and carries its statistics inline instead.
    """

    index: int
    name: str
    op: str
    dtype: str
    ne: list[int]
    nb: list[int]
    #: Decoder block this node belongs to, parsed from the node name. `None`
    #: when the name carries no block — embeddings and the output head do not.
    layer: int | None
    #: Which forward pass produced it: 0 is the prompt, 1..n each generated token.
    pass_index: int
    fidelity: str
    offset: int
    length: int
    summary: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["passIndex"] = data.pop("pass_index")
        return data

    @staticmethod
    def from_dict(data: dict[str, Any]) -> TraceRecord:
        return TraceRecord(
            index=int(data["index"]),
            name=str(data["name"]),
            op=str(data.get("op") or ""),
            dtype=str(data.get("dtype") or ""),
            ne=[int(v) for v in data.get("ne") or []],
            nb=[int(v) for v in data.get("nb") or []],
            layer=data.get("layer"),
            pass_index=int(data.get("passIndex", data.get("pass_index", 0))),
            fidelity=str(data.get("fidelity") or "full"),
            offset=int(data.get("offset") or 0),
            length=int(data.get("length") or 0),
            summary={k: float(v) for k, v in (data.get("summary") or {}).items()},
        )


_LAYER_RE = re.compile(r"[-_](\d+)$")


def layer_of(name: str) -> int | None:
    """The decoder block in a ggml node name (`ffn_out-15` → 15).

    llama.cpp names its graph nodes `<role>-<block>`; nodes outside a block
    (`inp_embd`, `result_norm`) have no suffix, and returning 0 for those would
    quietly file the embedding table inside layer 0.
    """
    match = _LAYER_RE.search(name)
    return int(match.group(1)) if match else None


# --- writing ---------------------------------------------------------------


class TraceWriter:
    """Append-only writer for one trace directory.

    Used by the tracer subprocess, and directly by the round-trip test — the
    reader and writer agreeing is the whole correctness argument for the format,
    exactly as it is for hassault's `.cgz` writer.
    """

    def __init__(self, directory: Path, meta: dict[str, Any]) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.meta = dict(meta)
        self.records: list[TraceRecord] = []
        self._blob: BinaryIO = (directory / "tensors.bin").open("wb")
        self._offset = 0

    def append(
        self,
        *,
        name: str,
        op: str,
        dtype: str,
        ne: list[int],
        nb: list[int],
        pass_index: int,
        fidelity: str,
        payload: bytes = b"",
        summary: dict[str, float] | None = None,
    ) -> TraceRecord:
        if fidelity not in FIDELITIES:
            raise ValueError(f"unknown fidelity {fidelity!r}")
        if fidelity == "summary" and payload:
            raise ValueError("a summary record stores statistics, not bytes")
        record = TraceRecord(
            index=len(self.records),
            name=name,
            op=op,
            dtype=dtype,
            ne=list(ne),
            nb=list(nb),
            layer=layer_of(name),
            pass_index=pass_index,
            fidelity=fidelity,
            offset=self._offset,
            length=len(payload),
            summary=dict(summary or {}),
        )
        if payload:
            self._blob.write(payload)
            self._offset += len(payload)
        self.records.append(record)
        return record

    def close(self, tokens: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        self._blob.close()
        manifest = dict(self.meta)
        manifest.update(
            {
                "records": [r.to_dict() for r in self.records],
                "recordCount": len(self.records),
                "blobBytes": self._offset,
                "modelShaScope": "gguf-header-1mib+size",
            }
        )
        manifest.setdefault("createdAt", time.time())
        (self.directory / "manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        (self.directory / "tokens.json").write_text(
            json.dumps(tokens or [], indent=2), encoding="utf-8"
        )
        return manifest


# --- summaries -------------------------------------------------------------


def summarize(values: list[float]) -> dict[str, float]:
    """Statistics that stand in for a tensor we chose not to store.

    Deliberately cheap and deliberately labelled: these are what a `summary`
    record carries *instead of* data, and the pane renders them as such.
    """
    if not values:
        return {}
    count = len(values)
    total = 0.0
    total_sq = 0.0
    lo = values[0]
    hi = values[0]
    zeros = 0
    for value in values:
        total += value
        total_sq += value * value
        lo = min(lo, value)
        hi = max(hi, value)
        if value == 0.0:
            zeros += 1
    mean = total / count
    return {
        "count": float(count),
        "min": lo,
        "max": hi,
        "mean": mean,
        "rms": (total_sq / count) ** 0.5,
        "absMax": max(abs(lo), abs(hi)),
        "zeroFraction": zeros / count,
    }


def decode(payload: bytes, dtype: str) -> list[float]:
    """Bytes from `tensors.bin` back to numbers.

    Little-endian, matching the manifest's `byteOrder`. Only the two dtypes this
    module ever *writes* are decodable: a quantized weight is recorded as
    metadata, never as bytes, so there is no dequantizer here to go subtly wrong.
    """
    if dtype in ("f32", "F32"):
        count = len(payload) // 4
        return list(struct.unpack(f"<{count}f", payload[: count * 4]))
    if dtype in ("f16", "F16"):
        count = len(payload) // 2
        return list(struct.unpack(f"<{count}e", payload[: count * 2]))
    raise ValueError(f"cannot decode dtype {dtype!r} — it was never written as bytes")


def encode_f16(values: list[float]) -> bytes:
    return struct.pack(f"<{len(values)}e", *values)


# --- reading ---------------------------------------------------------------


@dataclass
class Trace:
    trace_id: str
    directory: Path
    manifest: dict[str, Any]

    @property
    def records(self) -> list[TraceRecord]:
        return [TraceRecord.from_dict(r) for r in self.manifest.get("records") or []]

    @property
    def blob(self) -> Path:
        return self.directory / "tensors.bin"

    def bytes_on_disk(self) -> int:
        return sum(p.stat().st_size for p in self.directory.rglob("*") if p.is_file())

    def summary_dict(self) -> dict[str, Any]:
        """The listing shape: everything but the record list, which is large."""
        data = {k: v for k, v in self.manifest.items() if k != "records"}
        data["traceId"] = self.trace_id
        data["diskBytes"] = self.bytes_on_disk()
        return data


def load(trace_id: str) -> Trace | None:
    directory = traces_root() / _safe_id(trace_id)
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.info("llamacpp: unreadable trace manifest %s (%s)", directory, exc)
        return None
    return Trace(trace_id=directory.name, directory=directory, manifest=manifest)


def _safe_id(trace_id: str) -> str:
    """A trace id is ours, but it arrives from the wire — treat it as hostile.

    The same reasoning as `catalog.is_managed`: a `..` is how a delete route
    becomes an arbitrary-directory-delete route.
    """
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "", trace_id)
    if not cleaned or cleaned.startswith("."):
        raise ValueError(f"invalid trace id {trace_id!r}")
    return cleaned


def list_traces() -> list[Trace]:
    root = traces_root()
    if not root.is_dir():
        return []
    found: list[Trace] = []
    for directory in root.iterdir():
        if not directory.is_dir():
            continue
        trace = load(directory.name)
        if trace is not None:
            found.append(trace)
    found.sort(key=lambda t: float(t.manifest.get("createdAt") or 0), reverse=True)
    return found


def delete_trace(trace_id: str) -> bool:
    directory = traces_root() / _safe_id(trace_id)
    if not directory.is_dir():
        return False
    shutil.rmtree(directory, ignore_errors=True)
    return True


def prune(budget: int | None = None) -> list[str]:
    """Drop oldest traces until the directory fits the budget. Returns the ids."""
    limit = budget_bytes() if budget is None else budget
    if limit <= 0:
        return []
    traces = list_traces()
    total = sum(t.bytes_on_disk() for t in traces)
    removed: list[str] = []
    for trace in reversed(traces):  # oldest last in the listing order
        if total <= limit:
            break
        total -= trace.bytes_on_disk()
        if delete_trace(trace.trace_id):
            removed.append(trace.trace_id)
    return removed


def usage() -> dict[str, Any]:
    traces = list_traces()
    return {
        "usedBytes": sum(t.bytes_on_disk() for t in traces),
        "budgetBytes": budget_bytes(),
        "root": str(traces_root()),
    }


# --- provenance ------------------------------------------------------------


def matches_run(manifest: dict[str, Any], llama_build: str, sha: str) -> bool:
    """Whether a trace may be overlaid on a chat turn.

    A trace and a chat turn are different runs until proven otherwise: chat goes
    through a downloaded `llama-server` build, tracing through whatever libllama
    the wheel bundled. Overlaying them when either the build or the model
    differs shows a forward pass that did not produce the answer above it.
    """
    if not llama_build or not sha:
        return False
    return (
        str(manifest.get("llamaBuild") or "") == llama_build
        and str(manifest.get("modelSha") or "") == sha
    )


# --- pre-flight estimate ---------------------------------------------------


@dataclass
class Estimate:
    bytes_total: int
    seconds: float
    note: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "bytes": self.bytes_total,
            "seconds": round(self.seconds, 1),
            "note": self.note,
        }


def estimate(
    *,
    n_layer: int,
    n_embd: int,
    n_head: int,
    prompt_tokens: int,
    gen_tokens: int = 0,
    layers: int | None = None,
    attention: bool = False,
    fidelity: str = "fp16",
) -> Estimate:
    """What a trace will cost, *before* it starts.

    A progress bar that has already started is too late: attention capture is
    quadratic in tokens, and the difference between a 200 MB run and a 12 GB one
    is one checkbox. This is an estimate and is labelled as one everywhere it is
    shown — the real cost lands in the manifest's `blobBytes`.

    The model is coarse on purpose: per traced block, roughly six residual-width
    activations per pass (`attn_norm`, `kqv_out`, `ffn_inp`, `ffn_out`, `l_out`
    and one spare), plus — when attention is on — a `[n_kv, n_tokens, n_head]`
    score matrix.
    """
    width = 2 if fidelity == "fp16" else 4
    if fidelity == "summary":
        width = 0
    traced_layers = n_layer if layers is None else max(0, min(layers, n_layer))
    prompt_tokens = max(0, min(prompt_tokens, MAX_TRACE_TOKENS))
    gen_tokens = max(0, gen_tokens)

    per_pass_tokens = [prompt_tokens] + [1] * gen_tokens
    total = 0
    for pass_index, tokens in enumerate(per_pass_tokens):
        if tokens <= 0:
            continue
        total += traced_layers * 6 * n_embd * tokens * width
        if attention:
            # The score matrix is over the whole KV cache, which has grown by
            # every token emitted so far — the generated passes are not cheap
            # just because they run one token.
            n_kv = prompt_tokens + pass_index
            total += traced_layers * n_head * tokens * n_kv * 4

    # ~1500 graph nodes per pass and a GIL-bound Python callback firing twice per
    # node: the slowdown is the callback, not the arithmetic.
    seconds = 0.9 * len(per_pass_tokens) + total / (120 * 1024**2)
    note = (
        "Estimate. Attention capture dominates and grows with the square of the "
        "token count; the manifest records what the run actually wrote."
        if attention
        else "Estimate; the manifest records what the run actually wrote."
    )
    return Estimate(bytes_total=int(total), seconds=seconds, note=note)

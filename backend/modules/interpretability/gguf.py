"""Read a GGUF file's header directly — the tensors, not a summary of them.

Everything this module knew about a model until now arrived as JSON through
Ollama's `/api/show`: a handful of scalar dimensions, already interpreted by
someone else. That is enough to *describe* a model and not enough to *show* one.
The tensor directory is what makes the difference — real names (`blk.17.attn_q.weight`),
real shapes, real quantization types, real byte sizes — and it lives in the file.

**Header only.** The directory sits entirely before the tensor data, so answering
"what is this model made of" costs a few hundred KB of reads against a file that is
frequently 20 GB. Nothing here ever seeks past `data_offset`; a function that wanted
weight *values* would need to dequantize, which is a different problem with a
different budget, and its absence is why no statistic here is derived from data.

Ollama hides model files, so `resolve_ollama_model` walks its manifest store to find
the blob. That indirection is deliberate on Ollama's part and undocumented, which is
why every step of it fails soft: a layout change upstream must degrade this pane to
"no GGUF found", never crash it.

Format reference: the GGUF spec (ggml-org/ggml, docs/gguf.md). Structure is
    magic "GGUF" | version u32 | tensor_count u64 | kv_count u64
    kv_count × ( key:string, type:u32, value )
    tensor_count × ( name:string, n_dims:u32, dims:u64[n_dims], type:u32, offset:u64 )
    padding to general.alignment
    tensor data
"""

from __future__ import annotations

import json
import logging
import os
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

logger = logging.getLogger(__name__)

GGUF_MAGIC = b"GGUF"

# A malformed or truncated file must fail fast rather than making us allocate from
# a length field we read out of it. These are far above any real model (the largest
# published GGUFs carry ~1.5k tensors and a few thousand KV pairs) and far below
# anything that would exhaust memory.
_MAX_TENSORS = 100_000
_MAX_KV = 100_000
_MAX_STRING = 64 * 1024 * 1024
_MAX_DIMS = 8
_MAX_ARRAY = 8_000_000  # tokenizer vocab arrays are legitimately ~256k entries

# GGUF metadata value types.
_T_UINT8, _T_INT8, _T_UINT16, _T_INT16 = 0, 1, 2, 3
_T_UINT32, _T_INT32, _T_FLOAT32, _T_BOOL = 4, 5, 6, 7
_T_STRING, _T_ARRAY, _T_UINT64, _T_INT64, _T_FLOAT64 = 8, 9, 10, 11, 12

_SCALARS: dict[int, tuple[str, int]] = {
    _T_UINT8: ("<B", 1),
    _T_INT8: ("<b", 1),
    _T_UINT16: ("<H", 2),
    _T_INT16: ("<h", 2),
    _T_UINT32: ("<I", 4),
    _T_INT32: ("<i", 4),
    _T_FLOAT32: ("<f", 4),
    _T_BOOL: ("<?", 1),
    _T_UINT64: ("<Q", 8),
    _T_INT64: ("<q", 8),
    _T_FLOAT64: ("<d", 8),
}

# ggml tensor types → (name, elements per block, bytes per block).
#
# The pair is what turns a shape into a byte count, and it is the only reason this
# module can report a per-layer size at all: a quantized tensor's footprint is not
# derivable from its shape alone. Unknown ids are reported by number with a null
# size rather than guessed — a wrong byte count would silently misattribute where a
# model's weight actually sits, which is the one thing the table exists to answer.
_GGML_TYPES: dict[int, tuple[str, int, int]] = {
    0: ("F32", 1, 4),
    1: ("F16", 1, 2),
    2: ("Q4_0", 32, 18),
    3: ("Q4_1", 32, 20),
    6: ("Q5_0", 32, 22),
    7: ("Q5_1", 32, 24),
    8: ("Q8_0", 32, 34),
    9: ("Q8_1", 32, 36),
    10: ("Q2_K", 256, 84),
    11: ("Q3_K", 256, 110),
    12: ("Q4_K", 256, 144),
    13: ("Q5_K", 256, 176),
    14: ("Q6_K", 256, 210),
    15: ("Q8_K", 256, 292),
    16: ("IQ2_XXS", 256, 66),
    17: ("IQ2_XS", 256, 74),
    18: ("IQ3_XXS", 256, 98),
    19: ("IQ1_S", 256, 50),
    20: ("IQ4_NL", 32, 18),
    21: ("IQ3_S", 256, 110),
    22: ("IQ2_S", 256, 82),
    23: ("IQ4_XS", 256, 136),
    24: ("I8", 1, 1),
    25: ("I16", 1, 2),
    26: ("I32", 1, 4),
    27: ("I64", 1, 8),
    28: ("F64", 1, 8),
    29: ("IQ1_M", 256, 56),
    30: ("BF16", 1, 2),
    34: ("TQ1_0", 256, 54),
    35: ("TQ2_0", 256, 66),
    39: ("MXFP4", 32, 17),
}


class GgufError(Exception):
    """The file is not a GGUF we can read. Always caught at the route boundary."""


@dataclass(frozen=True)
class TensorInfo:
    """One tensor's entry in the directory — everything but its data."""

    name: str
    shape: tuple[int, ...]
    type_id: int
    type_name: str
    offset: int
    elements: int
    # None when `type_id` is not in `_GGML_TYPES`. See the note on that table:
    # an unknown quantization gets no invented size.
    n_bytes: int | None
    # Transformer block index parsed from the name (`blk.17.…`), or None for the
    # tensors that sit outside the stack — embeddings, final norm, output head.
    layer: int | None
    # Coarse role, used to group the explorer's tree. Derived from the name suffix.
    component: str


@dataclass
class GgufFile:
    """A parsed GGUF header."""

    path: str
    file_size: int
    version: int
    alignment: int
    data_offset: int
    metadata: dict[str, Any] = field(default_factory=dict)
    tensors: list[TensorInfo] = field(default_factory=list)


# ── primitive readers ────────────────────────────────────────────────────────


def _read_exact(handle: BinaryIO, size: int) -> bytes:
    chunk = handle.read(size)
    if len(chunk) != size:
        raise GgufError(f"Truncated: wanted {size} bytes, got {len(chunk)}")
    return chunk


def _read_u32(handle: BinaryIO) -> int:
    return int(struct.unpack("<I", _read_exact(handle, 4))[0])


def _read_u64(handle: BinaryIO) -> int:
    return int(struct.unpack("<Q", _read_exact(handle, 8))[0])


def _read_string(handle: BinaryIO) -> str:
    length = _read_u64(handle)
    if length > _MAX_STRING:
        raise GgufError(f"Implausible string length {length}")
    # `replace` rather than strict: a single bad byte in one tokenizer token must
    # not cost us the whole tensor directory that follows it.
    return _read_exact(handle, length).decode("utf-8", errors="replace")


def _read_value(handle: BinaryIO, type_id: int) -> Any:
    scalar = _SCALARS.get(type_id)
    if scalar is not None:
        fmt, size = scalar
        return struct.unpack(fmt, _read_exact(handle, size))[0]
    if type_id == _T_STRING:
        return _read_string(handle)
    if type_id == _T_ARRAY:
        item_type = _read_u32(handle)
        count = _read_u64(handle)
        if count > _MAX_ARRAY:
            raise GgufError(f"Implausible array length {count}")
        return [_read_value(handle, item_type) for _ in range(count)]
    raise GgufError(f"Unknown metadata value type {type_id}")


# ── name classification ──────────────────────────────────────────────────────

_BLOCK_RE = re.compile(r"(?:^|\.)blk\.(\d+)\.")

# Matched against the tensor's name, first hit wins, so order matters: `ffn_gate_inp`
# is the MoE *router* and must be tested before the plain `ffn_` prefixes, or every
# router would be filed as an ordinary feed-forward projection.
#
# A norm inside a block is reported as part of the sub-block it belongs to
# (`attn_output_norm` is attention), because the alternative leaves every attention
# group in the tree missing its own norms.
_BLOCK_RULES: tuple[tuple[str, str], ...] = (
    ("ffn_gate_inp", "moe"),
    ("_exps", "moe"),
    ("_shexp", "moe"),
    ("attn_", "attention"),
    # Gemma-family sandwich norms. Named unambiguously for the sub-block they
    # close, so attributing them is reading, not guessing — and it keeps the
    # attention group from silently under-reporting its own parameters.
    ("post_attention_norm", "attention"),
    ("post_ffw_norm", "ffn"),
    ("post_ffn_norm", "ffn"),
    ("ffn_", "ffn"),
)

# Only consulted for tensors OUTSIDE the block stack. These names are not unique:
# BERT-family blocks carry a per-layer `layer_output_norm`, which matches
# `output_norm` and would otherwise be filed as the model's final output head —
# putting two tensors per layer in a group that should hold one norm for the whole
# model, and quietly inflating what the tree says the head costs.
_GLOBAL_RULES: tuple[tuple[str, str], ...] = (
    ("token_embd", "embedding"),
    ("token_types", "embedding"),
    ("position_embd", "embedding"),
    ("rope_", "position"),
    ("output_norm", "output"),
    ("output.", "output"),
    ("cls.", "output"),
)


def _classify(name: str) -> tuple[int | None, str]:
    """`blk.17.attn_q.weight` → (17, "attention")."""
    match = _BLOCK_RE.search(name)
    layer = int(match.group(1)) if match else None
    lowered = name.lower()
    for needle, component in _BLOCK_RULES:
        if needle in lowered:
            return layer, component
    if layer is None:
        for needle, component in _GLOBAL_RULES:
            if needle in lowered:
                return layer, component
    # An in-block tensor matching none of the sub-block rules is left as a bare
    # norm rather than assigned to whichever sub-block it probably follows: the
    # tree can show it, and a wrong attribution would be invisible.
    if "norm" in lowered:
        return layer, "norm"
    return layer, "other"


def _n_bytes(type_id: int, elements: int) -> int | None:
    entry = _GGML_TYPES.get(type_id)
    if entry is None:
        return None
    _, block_elems, block_bytes = entry
    if block_elems <= 0 or elements % block_elems:
        # A shape that isn't a whole number of blocks means we've misread either
        # the type or the dims. Report nothing rather than a rounded fiction.
        return None
    return (elements // block_elems) * block_bytes


# ── the parser ───────────────────────────────────────────────────────────────


def read_header(path: str | os.PathLike[str]) -> GgufFile:
    """Parse a GGUF file's metadata and tensor directory. Raises `GgufError`."""
    file_path = Path(path)
    size = file_path.stat().st_size
    with open(file_path, "rb") as handle:
        magic = _read_exact(handle, 4)
        if magic != GGUF_MAGIC:
            raise GgufError(f"Not a GGUF file (magic {magic!r})")
        version = _read_u32(handle)
        if version < 2 or version > 3:
            # v1 used u32 lengths throughout and is long dead; a future v4 would
            # need its own reader rather than being fed to this one.
            raise GgufError(f"Unsupported GGUF version {version}")
        tensor_count = _read_u64(handle)
        kv_count = _read_u64(handle)
        if tensor_count > _MAX_TENSORS or kv_count > _MAX_KV:
            raise GgufError(
                f"Implausible header ({tensor_count} tensors, {kv_count} metadata keys)"
            )

        metadata: dict[str, Any] = {}
        for _ in range(kv_count):
            key = _read_string(handle)
            metadata[key] = _read_value(handle, _read_u32(handle))

        tensors: list[TensorInfo] = []
        for _ in range(tensor_count):
            name = _read_string(handle)
            n_dims = _read_u32(handle)
            if n_dims > _MAX_DIMS:
                raise GgufError(f"Tensor {name!r} claims {n_dims} dimensions")
            shape = tuple(_read_u64(handle) for _ in range(n_dims))
            type_id = _read_u32(handle)
            offset = _read_u64(handle)
            elements = 1
            for dim in shape:
                elements *= dim
            layer, component = _classify(name)
            type_entry = _GGML_TYPES.get(type_id)
            tensors.append(
                TensorInfo(
                    name=name,
                    shape=shape,
                    type_id=type_id,
                    type_name=type_entry[0] if type_entry else f"type:{type_id}",
                    offset=offset,
                    elements=elements,
                    n_bytes=_n_bytes(type_id, elements),
                    layer=layer,
                    component=component,
                )
            )

        alignment = metadata.get("general.alignment")
        align = int(alignment) if isinstance(alignment, int) and alignment > 0 else 32
        here = handle.tell()
        # Tensor data starts at the next `alignment` boundary after the directory.
        data_offset = here + (-here % align)

    return GgufFile(
        path=str(file_path),
        file_size=size,
        version=version,
        alignment=align,
        data_offset=data_offset,
        metadata=metadata,
        tensors=tensors,
    )


# ── locating the file Ollama loaded ──────────────────────────────────────────


def ollama_root() -> Path:
    """Ollama's model store. `OLLAMA_MODELS` wins, else the per-OS default."""
    override = os.environ.get("OLLAMA_MODELS")
    if override:
        return Path(override)
    return Path.home() / ".ollama" / "models"


def _manifest_path(root: Path, model: str) -> Path:
    """Map an Ollama model reference onto its manifest file.

    `gemma3:4b` → `manifests/registry.ollama.ai/library/gemma3/4b`. The defaults for
    the omitted registry and namespace are Ollama's, and a reference that already
    carries them is passed through unchanged.
    """
    ref, _, tag = model.partition(":")
    parts = [p for p in ref.split("/") if p]
    if not parts:
        raise GgufError(f"Unparseable model reference {model!r}")
    if len(parts) == 1:
        registry, namespace, name = "registry.ollama.ai", "library", parts[0]
    elif len(parts) == 2:
        registry, namespace, name = "registry.ollama.ai", parts[0], parts[1]
    else:
        registry, namespace, name = parts[0], parts[1], "/".join(parts[2:])
    # A reference is user-supplied and lands in a filesystem path; `..` in any
    # segment would walk out of the model store.
    for segment in (registry, namespace, name, tag or "latest"):
        if segment in ("", ".", "..") or "\\" in segment:
            raise GgufError(f"Unsafe model reference {model!r}")
    return root / "manifests" / registry / namespace / name / (tag or "latest")


def resolve_ollama_model(model: str) -> str | None:
    """Absolute path to the GGUF blob backing an Ollama model, or None.

    Ollama stores models as an OCI-style manifest plus content-addressed blobs; the
    weights are the layer whose `mediaType` is `…image.model`. None of that is a
    stable public interface, so every failure here is a soft None — the pane says
    "no GGUF found" and keeps working off `/api/show`.
    """
    if not model:
        return None
    root = ollama_root()
    try:
        manifest_path = _manifest_path(root, model)
        with open(manifest_path, encoding="utf-8") as handle:
            manifest = json.load(handle)
        layers = manifest.get("layers")
        if not isinstance(layers, list):
            return None
        for layer in layers:
            if not isinstance(layer, dict):
                continue
            if layer.get("mediaType") != "application/vnd.ollama.image.model":
                continue
            digest = str(layer.get("digest") or "")
            # `sha256:abc…` on the wire, `sha256-abc…` on disk.
            if not re.fullmatch(r"sha256[:-][0-9a-f]{64}", digest):
                continue
            blob = root / "blobs" / digest.replace(":", "-")
            return str(blob) if blob.is_file() else None
        return None
    except (OSError, ValueError, GgufError) as exc:
        logger.info("interpretability: no Ollama blob for %s (%s)", model, exc)
        return None


def lmstudio_index() -> Path:
    """LM Studio's model index. The *models* directory is user-configurable (this
    machine keeps it on another drive), but the index that maps a model id onto a
    file always lives here."""
    return Path.home() / ".lmstudio" / ".internal" / "model-index-cache.json"


def resolve_lmstudio_model(model: str) -> str | None:
    """Absolute path to the GGUF behind an LM Studio model id, or None.

    LM Studio's REST API reports a model's id, publisher, arch and quantization but
    **not** where it lives, so the path has to come from its own on-disk index,
    where `indexedModelIdentifier` is exactly the id the API serves.

    `entryPoint` is the field that matters: a multimodal repo ships an `mmproj-*.gguf`
    beside the weights, and taking the first file in the directory gets you the
    vision projector — a real GGUF that parses cleanly and describes the wrong thing.
    """
    if not model:
        return None
    try:
        with open(lmstudio_index(), encoding="utf-8") as handle:
            index = json.load(handle)
        entries = index.get("models")
        if not isinstance(entries, list):
            return None
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if entry.get("indexedModelIdentifier") != model:
                continue
            entry_point = entry.get("entryPoint")
            if isinstance(entry_point, dict):
                path = entry_point.get("absPath")
                if isinstance(path, str) and Path(path).is_file():
                    return path
            # No entry point: take the first non-projector GGUF listed for it.
            for item in entry.get("allFiles") or []:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("filename") or "")
                path = item.get("absPath")
                if (
                    name.endswith(".gguf")
                    and not name.startswith("mmproj-")
                    and isinstance(path, str)
                    and Path(path).is_file()
                ):
                    return path
        return None
    except (OSError, ValueError) as exc:
        logger.info(
            "interpretability: no LM Studio index entry for %s (%s)", model, exc
        )
        return None


def resolve_model_path(model: str, dialect: str, override: str = "") -> str | None:
    """Where the weights for the running model live, or None.

    Ordered, and the order is the point. An explicit `interpretability.ggufPath`
    always wins: auto-discovery reads two undocumented on-disk layouts that their
    owners are free to change, so there has to be a way to say "it is this file"
    that no upstream release can break. Everything below it is a convenience.
    """
    if override.strip():
        path = Path(override.strip()).expanduser()
        return str(path) if path.is_file() else None
    # Our own llama-server: we chose the file, so there is nothing to discover.
    # This is the only provider where the path is *known* rather than inferred from
    # somebody else's undocumented on-disk layout.
    #
    # Matched on the alias, not merely on "a server is running": llama-server can be
    # up while the agent talks to Ollama, and answering with the wrong model's
    # tensors is exactly the silent, plausible-looking wrongness this module exists
    # to prevent.
    from backend.modules.llamacpp.server import llama_manager

    if model and llama_manager.alias == model:
        served = llama_manager.model_path
        if served and Path(served).is_file():
            return served
    if dialect == "ollama":
        return resolve_ollama_model(model)
    # Not gated on the dialect being `lmstudio`: llama.cpp and other OpenAI-dialect
    # servers are frequently pointed at a model LM Studio also has indexed, and a
    # hit here is a hit on the same file.
    return resolve_lmstudio_model(model)

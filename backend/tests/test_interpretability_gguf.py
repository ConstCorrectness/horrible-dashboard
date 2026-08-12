"""Tests for the direct GGUF header reader.

The parser has no independent spec to check against here, so the argument is the
same one the hassault `.cgz` writer makes: a writer built from the format
description, round-tripped through the reader, agrees on every field. What that
cannot prove is the byte-size table — a wrong entry there round-trips perfectly and
still misreports where a model's weight sits — so those are asserted against the
block layouts from ggml's own type definitions, arithmetic spelled out.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from backend.modules.interpretability import gguf

# ── a minimal GGUF writer, for the round trip ────────────────────────────────

_T_UINT32, _T_FLOAT32, _T_STRING, _T_ARRAY = 4, 6, 8, 9


def _pstr(text: str) -> bytes:
    raw = text.encode("utf-8")
    return struct.pack("<Q", len(raw)) + raw


def _kv(key: str, type_id: int, payload: bytes) -> bytes:
    return _pstr(key) + struct.pack("<I", type_id) + payload


def write_gguf(
    path: Path,
    *,
    metadata: list[bytes],
    tensors: list[tuple[str, tuple[int, ...], int]],
    version: int = 3,
    alignment: int = 32,
) -> None:
    """Write a structurally valid GGUF with zeroed tensor data."""
    head = gguf.GGUF_MAGIC + struct.pack("<IQQ", version, len(tensors), len(metadata))
    head += b"".join(metadata)

    # Offsets are relative to data_offset and must respect alignment, exactly as a
    # real converter emits them.
    directory = b""
    cursor = 0
    sizes: list[int] = []
    for name, shape, type_id in tensors:
        directory += _pstr(name) + struct.pack("<I", len(shape))
        directory += b"".join(struct.pack("<Q", d) for d in shape)
        directory += struct.pack("<IQ", type_id, cursor)
        elements = 1
        for dim in shape:
            elements *= dim
        n_bytes = gguf._n_bytes(type_id, elements) or 0
        sizes.append(n_bytes)
        cursor += n_bytes + (-n_bytes % alignment)

    body = head + directory
    pad = -len(body) % alignment
    path.write_bytes(body + b"\0" * pad + b"\0" * cursor)


@pytest.fixture
def tiny_model(tmp_path: Path) -> Path:
    """Two blocks of a gated-FFN model, plus the tensors outside the stack."""
    path = tmp_path / "tiny.gguf"
    write_gguf(
        path,
        metadata=[
            _kv("general.architecture", _T_STRING, _pstr("llama")),
            _kv("general.alignment", _T_UINT32, struct.pack("<I", 32)),
            _kv("llama.block_count", _T_UINT32, struct.pack("<I", 2)),
            _kv("llama.embedding_length", _T_UINT32, struct.pack("<I", 64)),
            _kv("llama.rope.freq_base", _T_FLOAT32, struct.pack("<f", 10000.0)),
            _kv(
                "tokenizer.ggml.tokens",
                _T_ARRAY,
                struct.pack("<IQ", _T_STRING, 2) + _pstr("a") + _pstr("b"),
            ),
        ],
        tensors=[
            ("token_embd.weight", (64, 256), 12),  # Q4_K
            ("blk.0.attn_norm.weight", (64,), 0),  # F32
            ("blk.0.attn_q.weight", (64, 64), 12),
            ("blk.0.attn_k.weight", (64, 64), 12),
            ("blk.0.ffn_gate.weight", (64, 128), 14),  # Q6_K
            ("blk.0.ffn_down.weight", (128, 64), 14),
            ("blk.1.attn_q.weight", (64, 64), 12),
            ("blk.1.ffn_gate.weight", (64, 128), 14),
            ("output_norm.weight", (64,), 0),
            ("output.weight", (64, 256), 8),  # Q8_0
        ],
    )
    return path


# ── round trip ───────────────────────────────────────────────────────────────


def test_round_trip_metadata_and_tensors(tiny_model: Path) -> None:
    parsed = gguf.read_header(tiny_model)

    assert parsed.version == 3
    assert parsed.alignment == 32
    assert parsed.metadata["general.architecture"] == "llama"
    assert parsed.metadata["llama.block_count"] == 2
    assert parsed.metadata["llama.rope.freq_base"] == pytest.approx(10000.0)
    assert parsed.metadata["tokenizer.ggml.tokens"] == ["a", "b"]
    assert len(parsed.tensors) == 10

    by_name = {t.name: t for t in parsed.tensors}
    q = by_name["blk.0.attn_q.weight"]
    assert q.shape == (64, 64)
    assert q.type_name == "Q4_K"
    assert q.elements == 4096
    assert q.layer == 0
    assert q.component == "attention"


def test_data_offset_is_aligned_and_within_the_file(tiny_model: Path) -> None:
    parsed = gguf.read_header(tiny_model)
    assert parsed.data_offset % parsed.alignment == 0
    # The directory ends before the data, and the data is inside the file — the two
    # ways a mis-parsed header shows up as a plausible-looking number.
    assert 0 < parsed.data_offset <= parsed.file_size


# ── the byte-size table ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("type_id", "name", "elements", "expected"),
    [
        (0, "F32", 100, 400),
        (1, "F16", 100, 200),
        (30, "BF16", 100, 200),
        # 32-element blocks: d(2) + qs[16]
        (2, "Q4_0", 320, 10 * 18),
        # d(2) + qs[32]
        (8, "Q8_0", 320, 10 * 34),
        # 256-element K-quants: scales+quants, no shape dependence
        (12, "Q4_K", 512, 2 * 144),
        (14, "Q6_K", 512, 2 * 210),
        (11, "Q3_K", 256, 110),
        (13, "Q5_K", 256, 176),
        (10, "Q2_K", 256, 84),
        (23, "IQ4_XS", 256, 136),
    ],
)
def test_block_sizes(type_id: int, name: str, elements: int, expected: int) -> None:
    assert gguf._GGML_TYPES[type_id][0] == name
    assert gguf._n_bytes(type_id, elements) == expected


def test_unknown_type_reports_no_size_rather_than_guessing() -> None:
    """The whole module's premise: a number we cannot compute is not invented."""
    assert gguf._n_bytes(9999, 1024) is None


def test_partial_block_reports_no_size() -> None:
    """A shape that isn't a whole number of blocks means the type or the dims were
    misread; rounding it would silently misattribute the model's weight."""
    assert gguf._n_bytes(12, 100) is None  # Q4_K needs multiples of 256


def test_unknown_type_marks_the_inventory_incomplete(tmp_path: Path) -> None:
    path = tmp_path / "future.gguf"
    write_gguf(
        path,
        metadata=[_kv("general.architecture", _T_STRING, _pstr("llama"))],
        tensors=[("blk.0.attn_q.weight", (64, 64), 250)],
    )
    parsed = gguf.read_header(path)
    assert parsed.tensors[0].type_name == "type:250"
    assert parsed.tensors[0].n_bytes is None


# ── name classification ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "layer", "component"),
    [
        ("token_embd.weight", None, "embedding"),
        ("output_norm.weight", None, "output"),
        ("output.weight", None, "output"),
        ("blk.0.attn_q.weight", 0, "attention"),
        ("blk.31.attn_output.weight", 31, "attention"),
        ("blk.7.attn_norm.weight", 7, "attention"),
        ("blk.7.ffn_gate.weight", 7, "ffn"),
        ("blk.7.ffn_norm.weight", 7, "ffn"),
        ("blk.3.ffn_gate_exps.weight", 3, "moe"),
        ("blk.3.ffn_down_exps.weight", 3, "moe"),
        ("rope_freqs.weight", None, "position"),
    ],
)
def test_classification(name: str, layer: int | None, component: str) -> None:
    assert gguf._classify(name) == (layer, component)


def test_in_block_output_norm_is_not_the_models_output_head() -> None:
    """BERT-family blocks carry a per-layer `layer_output_norm`. Matching the
    global `output_norm` rule against it files two tensors per layer under the
    model's final head — 24 "output" tensors on a 12-layer model, which is what
    caught this against a real nomic-bert GGUF."""
    assert gguf._classify("blk.0.layer_output_norm.weight") == (0, "norm")
    assert gguf._classify("output_norm.weight") == (None, "output")


def test_in_block_attention_norm_stays_with_attention() -> None:
    assert gguf._classify("blk.4.attn_output_norm.bias") == (4, "attention")


def test_gemma_sandwich_norms_are_attributed_to_their_sub_block() -> None:
    """Gemma names its post-norms for the sub-block they close, so this is reading
    rather than guessing — verified against a real gemma4 12B GGUF."""
    assert gguf._classify("blk.0.post_attention_norm.weight") == (0, "attention")
    assert gguf._classify("blk.0.post_ffw_norm.weight") == (0, "ffn")


def test_an_unrecognised_in_block_tensor_is_not_guessed_into_a_sub_block() -> None:
    """Gemma 4 carries a per-layer `layer_output_scale` belonging to neither
    attention nor the FFN. Assigning it to whichever it probably follows would be a
    wrong attribution nothing on screen could reveal."""
    assert gguf._classify("blk.0.layer_output_scale.weight") == (0, "other")


def test_moe_router_is_not_filed_as_a_plain_ffn() -> None:
    """`ffn_gate_inp` is the expert router. It starts with `ffn_`, so rule order is
    load-bearing: matched the other way round, every MoE router in the model would
    be grouped with the dense feed-forward projections."""
    assert gguf._classify("blk.0.ffn_gate_inp.weight") == (0, "moe")


# ── malformed input ──────────────────────────────────────────────────────────


def test_rejects_a_non_gguf_file(tmp_path: Path) -> None:
    path = tmp_path / "not.gguf"
    path.write_bytes(b"ELF\0" + b"\0" * 64)
    with pytest.raises(gguf.GgufError, match="Not a GGUF"):
        gguf.read_header(path)


def test_rejects_an_unsupported_version(tmp_path: Path) -> None:
    path = tmp_path / "v1.gguf"
    path.write_bytes(gguf.GGUF_MAGIC + struct.pack("<IQQ", 1, 0, 0))
    with pytest.raises(gguf.GgufError, match="version 1"):
        gguf.read_header(path)


def test_rejects_an_implausible_header(tmp_path: Path) -> None:
    """A corrupt length field must not become an allocation."""
    path = tmp_path / "huge.gguf"
    path.write_bytes(gguf.GGUF_MAGIC + struct.pack("<IQQ", 3, 2**40, 2**40))
    with pytest.raises(gguf.GgufError, match="Implausible"):
        gguf.read_header(path)


def test_truncated_directory_raises_rather_than_returning_a_partial(
    tiny_model: Path,
) -> None:
    truncated = tiny_model.parent / "cut.gguf"
    truncated.write_bytes(tiny_model.read_bytes()[:120])
    with pytest.raises(gguf.GgufError):
        gguf.read_header(truncated)


# ── locating Ollama's blob ───────────────────────────────────────────────────


def _seed_ollama_store(root: Path, ref: str, digest: str, blob: bytes) -> None:
    registry, namespace, name_tag = "registry.ollama.ai", "library", ref
    name, _, tag = name_tag.partition(":")
    manifest = root / "manifests" / registry / namespace / name / (tag or "latest")
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(
            {
                "layers": [
                    {
                        "mediaType": "application/vnd.ollama.image.params",
                        "digest": "sha256:" + "0" * 64,
                    },
                    {
                        "mediaType": "application/vnd.ollama.image.model",
                        "digest": "sha256:" + digest,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    blobs = root / "blobs"
    blobs.mkdir(parents=True, exist_ok=True)
    (blobs / f"sha256-{digest}").write_bytes(blob)


def test_resolves_the_model_layer_not_the_first_layer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A manifest lists several layers; only one is the weights. Taking layer 0
    would hand back the params file and every read after it would fail."""
    root = tmp_path / "models"
    digest = "a" * 64
    _seed_ollama_store(root, "gemma3:4b", digest, b"GGUF-ish")
    monkeypatch.setenv("OLLAMA_MODELS", str(root))

    resolved = gguf.resolve_ollama_model("gemma3:4b")
    assert resolved == str(root / "blobs" / f"sha256-{digest}")


def test_default_tag_is_latest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "models"
    digest = "b" * 64
    _seed_ollama_store(root, "qwen3", digest, b"x")
    monkeypatch.setenv("OLLAMA_MODELS", str(root))
    assert gguf.resolve_ollama_model("qwen3") is not None


def test_missing_model_is_none_not_an_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ollama's on-disk layout is not a public interface. If it changes, this pane
    degrades to 'no GGUF found' — it never takes a route down with it."""
    monkeypatch.setenv("OLLAMA_MODELS", str(tmp_path / "empty"))
    assert gguf.resolve_ollama_model("nothing:here") is None


@pytest.mark.parametrize("ref", ["../../etc/passwd", "a/../../b", "..", ""])
def test_traversal_in_a_model_reference_is_refused(
    ref: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reference is user-supplied and lands in a filesystem path."""
    monkeypatch.setenv("OLLAMA_MODELS", str(tmp_path))
    assert gguf.resolve_ollama_model(ref) is None


def test_namespaced_reference(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "models"
    digest = "c" * 64
    manifest = root / "manifests" / "registry.ollama.ai" / "hf.co" / "unsloth" / "q4"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(
            {
                "layers": [
                    {
                        "mediaType": "application/vnd.ollama.image.model",
                        "digest": "sha256:" + digest,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (root / "blobs").mkdir(parents=True, exist_ok=True)
    (root / "blobs" / f"sha256-{digest}").write_bytes(b"x")
    monkeypatch.setenv("OLLAMA_MODELS", str(root))

    assert gguf.resolve_ollama_model("hf.co/unsloth:q4") is not None

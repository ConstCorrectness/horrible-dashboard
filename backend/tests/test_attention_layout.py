"""The per-layer attention layout (`attention_layout.py`).

Two halves. Synthetic headers pin each rule and each precedence step on its own.
Real headers — the GGUFs on this machine, skipped when absent — pin the claims
the explorer actually draws, and the correctness argument for each: the count of
windowed layers the metadata states must equal the count the *tensor shapes* show,
since Gemma 4's global layers are a different width.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from backend.modules.interpretability.attention_layout import (
    describe_pattern,
    layer_attention,
    swa_pattern,
)

FIXTURES = Path(__file__).parent / "fixtures"
LMSTUDIO = Path("D:/.lmstudio/models")


@dataclass
class T:
    name: str
    shape: tuple[int, ...]


def attn(
    layer: int,
    *,
    d: int = 64,
    q_heads: int = 4,
    kv: int = 2,
    hidden: int = 256,
    v: bool = True,
) -> list[T]:
    out = [
        T(f"blk.{layer}.attn_q.weight", (hidden, q_heads * d)),
        T(f"blk.{layer}.attn_k.weight", (hidden, kv * d)),
        T(f"blk.{layer}.attn_output.weight", (q_heads * d, hidden)),
    ]
    if v:
        out.append(T(f"blk.{layer}.attn_v.weight", (hidden, kv * d)))
    return out


def meta(family: str, n: int, **extra: object) -> dict[str, object]:
    base = {
        "general.architecture": family,
        f"{family}.block_count": n,
        f"{family}.embedding_length": 256,
        f"{family}.attention.head_count": 4,
        f"{family}.attention.head_count_kv": 2,
        f"{family}.attention.key_length": 64,
        f"{family}.attention.value_length": 64,
        f"{family}.context_length": 8192,
    }
    base.update(
        {
            f"{family}.{k}" if not k.startswith("general.") else k: v
            for k, v in extra.items()
        }
    )
    return base


# ── the shared pattern rule ──────────────────────────────────────────────────


def test_swa_pattern_matches_the_shared_fixture() -> None:
    # The WebML engine's `layerWindow` is tested against the same file.
    cases = json.loads((FIXTURES / "swa_patterns.json").read_text(encoding="utf-8"))[
        "cases"
    ]
    for case in cases:
        assert swa_pattern(case["layers"], case["period"]) == case["windowed"], case


# ── synthetic headers ────────────────────────────────────────────────────────


def test_plain_model_is_all_global_from_metadata() -> None:
    tensors = [t for i in range(4) for t in attn(i)]
    layout = layer_attention(meta("llama", 4), tensors)
    assert [la.kind for la in layout.layers] == ["global"] * 4
    assert {la.basis for la in layout.layers} == {"metadata"}
    assert layout.pattern == "all 4 global"
    assert layout.layers[0].kvBytesPerToken == 2 * (64 + 64) * 2
    assert layout.conflicts == []


def test_gemma3_window_comes_from_the_family_rule_and_says_so() -> None:
    tensors = [t for i in range(12) for t in attn(i)]
    layout = layer_attention(
        meta("gemma3", 12, **{"attention.sliding_window": 1024}), tensors
    )
    kinds = [la.kind for la in layout.layers]
    assert kinds == (["sliding"] * 5 + ["global"]) * 2
    assert {la.basis for la in layout.layers} == {"rule"}
    assert layout.layers[0].window == 1024 and layout.layers[5].window is None
    assert any("gemma3.cpp" in note for note in layout.notes)
    assert layout.pattern == "5 sliding : 1 global, ×2"


def test_explicit_array_beats_the_rule() -> None:
    pattern = [True, False, True, False]
    tensors = [t for i in range(4) for t in attn(i)]
    layout = layer_attention(
        meta(
            "gemma3",
            4,
            **{
                "attention.sliding_window": 512,
                "attention.sliding_window_pattern": pattern,
            },
        ),
        tensors,
    )
    assert [la.kind for la in layout.layers] == [
        "sliding",
        "global",
        "sliding",
        "global",
    ]
    assert {la.basis for la in layout.layers} == {"metadata"}


def test_unknown_family_with_a_window_is_drawn_global_with_a_note() -> None:
    tensors = [t for i in range(2) for t in attn(i)]
    layout = layer_attention(
        meta("mistral", 2, **{"attention.sliding_window": 4096}), tensors
    )
    assert [la.kind for la in layout.layers] == ["global", "global"]
    assert any("not which layers" in note for note in layout.notes)


def test_llama4_chunked_layers_and_nope_globals() -> None:
    tensors = [t for i in range(8) for t in attn(i)]
    layout = layer_attention(meta("llama4", 8), tensors)
    kinds = [la.kind for la in layout.layers]
    assert kinds == ["chunked", "chunked", "chunked", "global"] * 2
    assert layout.layers[0].window == 8192
    assert [la.nope for la in layout.layers] == [False, False, False, True] * 2


def test_qwen35_linear_layers_and_gated_full_layers() -> None:
    tensors: list[T] = []
    for i in range(8):
        if (i + 1) % 4 == 0:
            # Q is projected doubled: half queries, half the output gate.
            tensors += attn(i)
            tensors[-4] = T(f"blk.{i}.attn_q.weight", (256, 2 * 4 * 64))
        else:
            tensors += [
                T(f"blk.{i}.attn_qkv.weight", (256, 512)),
                T(f"blk.{i}.attn_gate.weight", (256, 256)),
                T(f"blk.{i}.ssm_conv1d.weight", (4, 512)),
            ]
    layout = layer_attention(meta("qwen35", 8, full_attention_interval=4), tensors)
    assert [la.kind for la in layout.layers] == ["linear"] * 3 + ["global"] + [
        "linear"
    ] * 3 + ["global"]
    assert layout.layers[3].outputGate
    assert layout.layers[0].kvBytesPerToken == 0
    assert layout.pattern == "3 linear : 1 global, ×2"


def test_hybrid_zero_kv_heads_take_their_kind_from_tensors() -> None:
    tensors: list[T] = []
    for i in range(4):
        if i == 2:
            tensors += attn(i)
        else:
            tensors += [
                T(f"blk.{i}.ssm_in.weight", (256, 512)),
                T(f"blk.{i}.ssm_out.weight", (512, 256)),
            ]
    layout = layer_attention(
        meta("granitehybrid", 4, **{"attention.head_count_kv": [0, 0, 2, 0]}), tensors
    )
    assert [la.kind for la in layout.layers] == ["ssm", "ssm", "global", "ssm"]
    assert layout.layers[0].basis == "tensors"


def test_tensors_overrule_a_rule_and_the_conflict_is_reported() -> None:
    # The rule says layer 0 is attention; the file says it is a Mamba block.
    tensors = [T("blk.0.ssm_in.weight", (256, 512))] + attn(1)
    layout = layer_attention(
        meta("gemma3", 2, **{"attention.sliding_window": 512}), tensors
    )
    assert layout.layers[0].kind == "ssm"
    assert layout.conflicts and "Layers 0" in layout.conflicts[0]


def test_kv_sharing_points_at_the_last_earlier_layer_of_the_same_kind() -> None:
    pattern = [True, True, False, True, False, True]
    tensors = [t for i in range(6) for t in attn(i)]
    layout = layer_attention(
        meta(
            "gemma4",
            6,
            **{
                "attention.sliding_window": 512,
                "attention.sliding_window_pattern": pattern,
                "attention.shared_kv_layers": 3,
            },
        ),
        tensors,
    )
    sources = [la.kvSourceLayer for la in layout.layers]
    # Layers 3–5 reuse: 3 (sliding) → 1, 4 (global) → 2, 5 (sliding) → 1.
    assert sources == [None, None, None, 1, 2, 1]
    assert [la.kvBytesPerToken == 0 for la in layout.layers] == [False] * 3 + [True] * 3


def test_k_equals_v_and_sinks_come_from_the_tensor_directory() -> None:
    tensors = attn(0, v=False) + [T("blk.0.attn_sinks.weight", (4,))]
    layout = layer_attention(meta("gpt-oss", 1), tensors)
    layer = layout.layers[0]
    assert layer.kEqV and layer.sinks
    assert layer.kvBytesPerToken == 2 * 64 * 2  # the key, counted once


def test_reach_is_bounded_until_a_global_layer() -> None:
    tensors = [t for i in range(6) for t in attn(i)]
    layout = layer_attention(
        meta("gemma3", 6, **{"attention.sliding_window": 100}), tensors
    )
    assert [la.reach for la in layout.layers] == [99, 198, 297, 396, 495, None]


def test_kv_curve_caps_windowed_layers() -> None:
    tensors = [t for i in range(6) for t in attn(i)]
    layout = layer_attention(
        meta("gemma3", 6, **{"attention.sliding_window": 1024}), tensors
    )
    per_token = 2 * (64 + 64) * 2
    at_8k = next(p for p in layout.kvCache if p.context == 8192)
    assert at_8k.bytes == per_token * (5 * 1024 + 8192)
    assert at_8k.bytesIfGlobal == per_token * 6 * 8192


def test_describe_pattern_falls_back_to_counts() -> None:
    assert (
        describe_pattern(["conv", "conv", "global", "conv", "global"])
        == "3 conv, 2 global"
    )


def test_no_block_count_is_an_error_not_an_empty_layout() -> None:
    layout = layer_attention({"general.architecture": "x"}, [])
    assert layout.error


# ── real headers ─────────────────────────────────────────────────────────────


def _real(rel: str) -> Path:
    path = LMSTUDIO / rel
    if not path.is_file():
        pytest.skip(f"{path} not on this machine")
    return path


def _layout(rel: str):
    from backend.modules.interpretability.gguf import read_header

    header = read_header(_real(rel))
    return header, layer_attention(
        header.metadata, header.tensors, path=str(header.path)
    )


def _shape_bands(header, kinds: list[str]) -> tuple[set[int], set[int]]:
    """K-projection widths of the windowed and of the global layers, read from the
    tensor directory alone."""
    widths = {
        int(t.name.split(".")[1]): t.shape[1]
        for t in header.tensors
        if t.name.endswith("attn_k.weight")
    }
    windowed = {
        widths[i] for i, k in enumerate(kinds) if k == "sliding" and i in widths
    }
    globals_ = {widths[i] for i, k in enumerate(kinds) if k == "global" and i in widths}
    return windowed, globals_


def test_real_gemma4_e2b() -> None:
    header, layout = _layout(
        "lmstudio-community/gemma-4-E2B-it-GGUF/gemma-4-E2B-it-Q4_K_M.gguf"
    )
    kinds = [la.kind for la in layout.layers]
    assert layout.counts == {"sliding": 28, "global": 7}
    assert layout.pattern == "4 sliding : 1 global, ×7"
    assert {la.headDimK for la in layout.layers if la.kind == "sliding"} == {256}
    assert {la.headDimK for la in layout.layers if la.kind == "global"} == {512}
    # Layers 15–34 share: the last sliding before 15 is 13, the last global 14.
    assert {la.kvSourceLayer for la in layout.layers[15:] if la.kind == "sliding"} == {
        13
    }
    assert {la.kvSourceLayer for la in layout.layers[15:] if la.kind == "global"} == {
        14
    }
    windowed, globals_ = _shape_bands(header, kinds)
    assert windowed.isdisjoint(globals_), "metadata pattern and tensor widths disagree"
    assert layout.conflicts == []


def test_real_gemma4_26b_moe() -> None:
    header, layout = _layout(
        "lmstudio-community/gemma-4-26B-A4B-it-QAT-GGUF/gemma-4-26B-A4B-it-QAT-Q4_0.gguf"
    )
    assert layout.counts == {"sliding": 25, "global": 5}
    globals_ = [la for la in layout.layers if la.kind == "global"]
    assert all(la.kEqV and la.kvHeads == 2 and la.headDimK == 512 for la in globals_)
    windowed, global_widths = _shape_bands(header, [la.kind for la in layout.layers])
    assert windowed.isdisjoint(global_widths)
    assert layout.conflicts == []


def test_real_qwen35_hybrid() -> None:
    _header, layout = _layout(
        "lmstudio-community/Qwen3.5-9B-GGUF/Qwen3.5-9B-Q4_K_M.gguf"
    )
    assert layout.counts == {"linear": 24, "global": 8}
    assert all(la.outputGate for la in layout.layers if la.kind == "global")
    assert layout.conflicts == []


def test_real_granite_hybrid() -> None:
    _header, layout = _layout(
        "lmstudio-community/granite-4.0-h-tiny-GGUF/granite-4.0-h-tiny-Q4_K_M.gguf"
    )
    assert layout.counts == {"ssm": 36, "global": 4}
    assert [la.layer for la in layout.layers if la.kind == "global"] == [5, 15, 25, 35]


def test_real_llama_is_uniform() -> None:
    _header, layout = _layout(
        "lmstudio-community/Llama-3.2-3B-Instruct-GGUF/Llama-3.2-3B-Instruct-Q4_K_M.gguf"
    )
    assert layout.pattern.startswith("all ") and layout.pattern.endswith(" global")
    assert layout.conflicts == []

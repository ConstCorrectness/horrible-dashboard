"""Architecture normalization: GGUF metadata and HF `config.json` into one shape.

The invariant under test throughout is that **nothing is invented**. A dimension the
metadata doesn't state must stay `None` so the diagram omits it — a plausible-looking
number the source never said is the one failure this module cannot afford.
"""

from __future__ import annotations

from typing import Any

from backend.modules.interpretability import architecture as arch


def _hf(**over: Any) -> dict[str, Any]:
    """A Llama-3-shaped config; override to make the case under test."""
    return {
        "model_type": "llama",
        "num_hidden_layers": 32,
        "hidden_size": 4096,
        "num_attention_heads": 32,
        "num_key_value_heads": 8,
        "intermediate_size": 14336,
        "hidden_act": "silu",
        "vocab_size": 128256,
        "max_position_embeddings": 131072,
        "rope_theta": 500000.0,
        "rms_norm_eps": 1e-5,
        "tie_word_embeddings": False,
        **over,
    }


def _gguf(
    info: dict[str, Any], details: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {"model_info": info, "details": details or {}}


# ── Attention kind ──────────────────────────────────────────────────────────


def test_grouped_query_attention_is_detected_with_its_ratio():
    a = arch.from_hf_config("m", "r", _hf()).attention
    assert a is not None
    assert a.kind == "gqa"
    assert a.heads == 32 and a.kvHeads == 8
    assert a.groupRatio == 4  # 4 query heads per KV head -> 4x smaller KV cache


def test_multi_query_attention_is_a_single_kv_head():
    a = arch.from_hf_config("m", "r", _hf(num_key_value_heads=1)).attention
    assert a and a.kind == "mqa"


def test_equal_head_counts_are_plain_multi_head():
    a = arch.from_hf_config("m", "r", _hf(num_key_value_heads=32)).attention
    assert a and a.kind == "mha"


def test_absent_kv_heads_means_mha_not_unknown():
    """HF semantics: omitting num_key_value_heads means every query head has its own
    KV head. Treating it as unknown would hide a real fact the config does state."""
    cfg = _hf()
    del cfg["num_key_value_heads"]
    a = arch.from_hf_config("m", "r", cfg).attention
    assert a and a.kind == "mha" and a.kvHeads == 32


def test_head_dim_is_derived_when_omitted_but_only_when_exact():
    assert arch.from_hf_config("m", "r", _hf()).attention.headDim == 128  # 4096/32
    # A non-divisible pair means the derivation would be a guess, so it stays None.
    odd = arch.from_hf_config("m", "r", _hf(hidden_size=4097)).attention
    assert odd.headDim is None


def test_a_derived_head_dim_is_flagged_as_derived():
    """hidden/heads is right for most models and WRONG for some: Gemma 3 12B sets
    head_dim 256 where 3840/16 gives 240. The value is still useful, so it's kept —
    but flagged, and the diagram renders it as `~240` rather than as a stated fact."""
    cfg = _hf()
    assert "head_dim" not in cfg
    a = arch.from_hf_config("m", "r", cfg).attention
    assert a.headDim == 128 and a.headDimDerived is True


def test_explicit_head_dim_is_not_flagged():
    a = arch.from_hf_config("m", "r", _hf(head_dim=256)).attention
    assert a.headDim == 256 and a.headDimDerived is False


def test_nested_language_tower_keeps_the_outer_family_and_its_gating():
    """A multimodal repo names its tower `gemma3_text`. Reading that as the family
    both mislabels the model and loses FFN gating, because the suffixed name is in
    no list — so the outer `model_type` wins and the suffix is tolerated."""
    a = arch.from_hf_config(
        "g",
        "google/gemma-3-12b-it",
        {
            "model_type": "gemma3",
            "text_config": {
                "model_type": "gemma3_text",
                "num_hidden_layers": 48,
                "hidden_size": 3840,
                "num_attention_heads": 16,
                "intermediate_size": 15360,
            },
        },
    )
    assert a.family == "gemma3"
    assert a.ffn.gated is True


def test_gemma_generations_are_all_known_gated():
    """Family names are exact-matched, so each generation needs its own entry —
    `gemma4` does not match `gemma`. A missing entry silently downgrades the FFN
    to "unknown" and stops it being drawn as gated."""
    for family in ("gemma", "gemma2", "gemma3", "gemma4"):
        assert arch._is_gated(family) is True, family


def test_gated_lookup_tolerates_a_text_suffix_directly():
    assert arch._is_gated("gemma3_text") is True
    assert arch._is_gated("gpt2_text") is False
    assert arch._is_gated("mystery_text") is None


# ── FFN and MoE ─────────────────────────────────────────────────────────────


def test_ffn_expansion_ratio_and_gating():
    f = arch.from_hf_config("m", "r", _hf()).ffn
    assert f.intermediateSize == 14336
    assert f.expansionRatio == 3.5
    assert f.gated is True  # llama is a known-gated family
    assert f.activation == "silu"


def test_unknown_family_does_not_claim_gating():
    """Gating is a structural claim about the drawing, so an unrecognised family
    leaves it None rather than guessing from the activation name."""
    f = arch.from_hf_config("m", "r", _hf(model_type="some_new_arch")).ffn
    assert f.gated is None


def test_moe_experts_and_active_fraction():
    a = arch.from_hf_config(
        "m", "r", _hf(model_type="mixtral", num_local_experts=8, num_experts_per_tok=2)
    )
    assert a.moe is not None
    assert a.moe.experts == 8 and a.moe.expertsPerToken == 2
    assert a.moe.activeFraction == 0.25  # the point of MoE, stated numerically


def test_moe_alternate_spellings_are_recognised():
    """DeepSeek names the same concept differently; a single-spelling check would
    silently render an MoE model as dense."""
    a = arch.from_hf_config(
        "m",
        "r",
        _hf(
            model_type="deepseek_v3",
            n_routed_experts=256,
            num_experts_per_tok=8,
            n_shared_experts=1,
        ),
    )
    assert a.moe and a.moe.experts == 256 and a.moe.sharedExperts == 1


def test_dense_model_has_no_moe_section():
    assert arch.from_hf_config("m", "r", _hf()).moe is None


# ── Source-specific handling ────────────────────────────────────────────────


def test_gguf_keys_are_matched_by_suffix_across_families():
    """GGUF namespaces every key by architecture. Suffix matching is what keeps a
    new family working without a table entry."""
    a = arch.from_ollama_show(
        "gemma2:9b",
        "http://localhost:11434",
        _gguf(
            {
                "general.architecture": "gemma2",
                "general.parameter_count": 9241705984,
                "gemma2.block_count": 42,
                "gemma2.embedding_length": 3584,
                "gemma2.attention.head_count": 16,
                "gemma2.attention.head_count_kv": 8,
                "gemma2.feed_forward_length": 14336,
                "gemma2.context_length": 8192,
                "gemma2.attention.layer_norm_rms_epsilon": 1e-6,
            }
        ),
    )
    assert a.source == "ollama"
    assert a.layers == 42 and a.hiddenSize == 3584
    assert a.attention.kind == "gqa"
    assert a.parameterCount == 9241705984
    assert a.normType == "rmsnorm"


def test_quantization_is_noted_without_implying_a_shape_change():
    a = arch.from_ollama_show(
        "m", "e", _gguf({"llama.block_count": 32}, {"quantization_level": "Q4_0"})
    )
    assert any("Q4_0" in n for n in a.notes)
    assert any("structure unchanged" in n.lower() for n in a.notes)


def test_multimodal_repo_reads_the_language_tower():
    """A VLM repo nests the text model under text_config; reading the outer config
    would report a mostly-empty architecture."""
    a = arch.from_hf_config(
        "g",
        "google/x",
        {
            "model_type": "gemma3",
            "text_config": {"model_type": "gemma3_text", "num_hidden_layers": 48},
        },
    )
    assert a.layers == 48
    assert any("text_config" in n for n in a.notes)


def test_alternating_sliding_window_is_surfaced():
    a = arch.from_hf_config(
        "g", "r", _hf(sliding_window=1024, sliding_window_pattern=6)
    )
    assert a.attention.slidingWindow == 1024
    assert any("alternates" in n.lower() for n in a.notes)


def test_source_is_reported_so_confidence_is_legible():
    """Reading the loaded weights and reading a repo the user pointed us at are
    different claims; the pane labels them differently."""
    assert arch.from_hf_config("m", "some/repo", _hf()).source == "huggingface"
    assert arch.from_hf_config("m", "some/repo", _hf()).sourceDetail == "some/repo"
    assert arch.from_ollama_show("m", "http://x", _gguf({})).source == "ollama"


# ── The invariant ───────────────────────────────────────────────────────────


def test_empty_metadata_invents_nothing():
    a = arch.from_hf_config("m", "r", {})
    assert a.layers is None and a.hiddenSize is None and a.vocabSize is None
    assert a.attention is None and a.ffn is None and a.moe is None
    assert a.parameterCount is None


def test_partial_metadata_keeps_what_it_has_and_omits_the_rest():
    a = arch.from_hf_config("m", "r", {"num_hidden_layers": 12, "hidden_size": 768})
    assert a.layers == 12 and a.hiddenSize == 768
    assert a.attention is None  # no head counts stated -> no attention section drawn
    assert a.ffn is None


def test_garbage_values_do_not_raise():
    a = arch.from_hf_config(
        "m",
        "r",
        {"num_hidden_layers": "many", "hidden_size": None, "num_attention_heads": []},
    )
    assert a.layers is None and a.hiddenSize is None


def test_hf_config_reports_no_parameter_count_rather_than_estimating():
    """config.json genuinely doesn't carry one, and a computed estimate would be
    wrong often enough to mislead."""
    assert arch.from_hf_config("m", "r", _hf()).parameterCount is None


# ── Gap filling ─────────────────────────────────────────────────────────────


def test_declared_repo_is_read_from_the_gguf_provenance():
    """The GGUF names the weights it was converted from; that's what makes filling
    from it defensible rather than a same-family guess."""
    data = _gguf(
        {"general.base_model.0.repo_url": "https://huggingface.co/google/gemma-4-12B"}
    )
    assert arch.declared_repo(data) == "google/gemma-4-12B"


def test_declared_repo_rejects_anything_that_is_not_owner_slash_name():
    assert arch.declared_repo(_gguf({})) is None
    assert arch.declared_repo(_gguf({"general.base_model.0.repo_url": "not a url"})) is None
    assert (
        arch.declared_repo(
            _gguf({"general.base_model.0.repo_url": "https://huggingface.co/a/b/tree/main"})
        )
        is None
    )


def test_fill_gaps_fills_only_what_is_missing():
    primary = arch.from_ollama_show(
        "gemma4:12b",
        "e",
        _gguf(
            {
                "general.architecture": "gemma4",
                "gemma4.block_count": 48,
                "gemma4.embedding_length": 3840,
                "gemma4.attention.head_count": 16,
                "gemma4.feed_forward_length": 15360,
            }
        ),
    )
    assert primary.attention.kvHeads is None and primary.vocabSize is None

    secondary = arch.from_hf_config(
        "gemma4:12b",
        "google/gemma-4-12B",
        {
            "model_type": "gemma4",
            "num_attention_heads": 16,
            "num_key_value_heads": 8,
            "hidden_size": 3840,
            "vocab_size": 262144,
            "tie_word_embeddings": True,
        },
    )
    filled = arch.fill_gaps(primary, secondary)

    assert primary.attention.kvHeads == 8
    assert primary.vocabSize == 262144
    assert primary.tiedEmbeddings is True
    assert "KV heads" in filled and "vocab size" in filled


def test_fill_gaps_recomputes_attention_kind_once_kv_heads_arrive():
    """kvHeads is what makes GQA detectable; filling it without recomputing would
    leave the diagram saying 'unknown' while holding the data to say 'gqa'."""
    primary = arch.from_ollama_show(
        "m", "e", _gguf({"llama.attention.head_count": 16, "llama.embedding_length": 3840})
    )
    assert primary.attention.kind == "unknown"

    secondary = arch.from_hf_config(
        "m", "r", {"num_attention_heads": 16, "num_key_value_heads": 8}
    )
    arch.fill_gaps(primary, secondary)
    assert primary.attention.kind == "gqa"
    assert primary.attention.groupRatio == 2


def test_fill_gaps_never_overrides_a_stated_value():
    """The safety property the whole feature rests on. Gemma 4's GGUF states
    key_length 512 where its repo config says head_dim 256 — a real conflict. The
    weights win, because silently preferring the repo would mix contradictory data
    into one diagram without admitting it."""
    primary = arch.from_ollama_show(
        "m",
        "e",
        _gguf(
            {
                "gemma4.attention.head_count": 16,
                "gemma4.attention.key_length": 512,
                "gemma4.context_length": 262144,
            }
        ),
    )
    secondary = arch.from_hf_config(
        "m", "r", {"num_attention_heads": 16, "head_dim": 256, "max_position_embeddings": 8192}
    )
    arch.fill_gaps(primary, secondary)

    assert primary.attention.headDim == 512  # not 256
    assert primary.contextLength == 262144  # not 8192


def test_fill_gaps_reports_nothing_when_there_are_no_gaps():
    primary = arch.from_hf_config("m", "r", _hf())
    secondary = arch.from_hf_config("m", "r", _hf(vocab_size=999))
    assert arch.fill_gaps(primary, secondary) == []
    assert primary.vocabSize == 128256  # untouched

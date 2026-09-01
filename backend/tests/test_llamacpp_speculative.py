"""Speculative decoding: flag probing, draft compatibility, and the VRAM split.

The flag probe is tested against fixture `--help` text rather than a real binary,
because the whole point is that the *spelling differs between builds* and no single
machine has both. The compatibility check is tested against synthetic headers for
the same reason: the pairs that matter are ones this machine may not have.
"""

import pytest

from backend.modules.llamacpp import features, specbench, speculative

# Abridged from real `llama-server --help` output, in both spellings.
HELP_NEW = """
usage: llama-server [options]

  -m,    --model FNAME              model path
  -md,   --model-draft FNAME        draft model for speculative decoding
         --spec-draft-n-max N       max drafted tokens (default: 3)
         --spec-draft-n-min N       min drafted tokens
         --spec-draft-p-min P       min probability for greedy drafting
  -ngld, --n-gpu-layers-draft N     draft layers to offload
         --jinja                    use the model's chat template
"""

HELP_OLD = """
usage: llama-server [options]

  -m,    --model FNAME              model path
  -md,   --model-draft FNAME        draft model for speculative decoding
         --draft-max N              max drafted tokens
         --draft-min N              min drafted tokens
         --jinja                    use the model's chat template
"""

HELP_NO_SPEC = """
usage: llama-server [options]

  -m,    --model FNAME              model path
         --jinja                    use the model's chat template
"""

HELP_WITH_RPC = (
    HELP_NEW + "         --rpc SERVERS             comma-separated RPC servers\n"
)


@pytest.fixture(autouse=True)
def _clear():
    features.reset_cache()
    yield
    features.reset_cache()


def _probe(help_text):
    """Build a Features from fixture help text, bypassing the subprocess."""
    return features.Features(
        binary="fake",
        draft_model=features._detect(
            help_text, "draftModel", features.DRAFT_MODEL_FLAGS
        ),
        draft_max=features._detect(help_text, "draftMax", features.DRAFT_MAX_FLAGS),
        draft_min=features._detect(help_text, "draftMin", features.DRAFT_MIN_FLAGS),
        draft_p_min=features._detect(
            help_text, "draftPMin", features.DRAFT_P_MIN_FLAGS
        ),
        draft_ngl=features._detect(
            help_text, "draftGpuLayers", features.DRAFT_NGL_FLAGS
        ),
        rpc=features._detect(help_text, "rpc", features.RPC_FLAGS),
    )


# ---- flag probing -------------------------------------------------------------


def test_new_spelling_is_detected():
    info = _probe(HELP_NEW)
    assert info.speculative
    assert info.draft_max.flag == "--spec-draft-n-max"
    assert info.draft_min.flag == "--spec-draft-n-min"


def test_old_spelling_is_detected():
    """The 2026 rename means both spellings are in the wild; hardcoding either is
    wrong for half of them, and the failure is silent — an unknown flag makes the
    server exit during load, reported only as 'did not start'."""
    info = _probe(HELP_OLD)
    assert info.speculative
    assert info.draft_max.flag == "--draft-max"


def test_a_build_without_drafting_says_so():
    info = _probe(HELP_NO_SPEC)
    assert info.speculative is False
    assert info.draft_model.certain is True  # we asked, and the answer was no


def test_absence_and_could_not_ask_are_different_answers():
    """`hardware/probe.py`'s rule. Reporting 'could not ask' as 'not supported'
    tells a user their build lacks a feature when the truth is nobody looked."""
    unknown = features._all_unknown("", "no llama.cpp build is installed")
    assert unknown.draft_model.supported is False
    assert unknown.draft_model.certain is False
    assert "installed" in unknown.draft_model.reason

    known_absent = _probe(HELP_NO_SPEC)
    assert known_absent.draft_model.supported is False
    assert known_absent.draft_model.certain is True


def test_rpc_support_is_read_from_the_build_not_assumed():
    """`--rpc` is a compile-time feature (-DGGML_RPC=ON), and whether upstream's
    release binaries carry it has changed over time — b10453-cuda, which this
    module downloads, does. That is why it is probed: a constant would have been
    true when written and quietly wrong later."""
    assert _probe(HELP_NEW).rpc.supported is False
    assert _probe(HELP_WITH_RPC).rpc.supported is True
    assert _probe(HELP_WITH_RPC).rpc.flag == "--rpc"


def test_flag_matching_requires_a_delimiter():
    """`--draft-max` is a substring of `--spec-draft-n-max` prose in some builds'
    help text; a bare `in` would report the retired spelling as supported and then
    fail at load."""
    prose = "  --spec-draft-n-max N   replaces the old --draft-max=N option\n"
    detected = features._detect(prose, "draftMax", features.DRAFT_MAX_FLAGS)
    assert detected.flag == "--spec-draft-n-max"


def test_empty_help_is_could_not_ask_not_unsupported():
    info = features._all_unknown("x", "--help produced no output")
    assert info.draft_model.certain is False


# ---- composing the command line ------------------------------------------------


def test_speculative_args_use_the_probed_spelling():
    info = _probe(HELP_OLD)
    args = features.speculative_args("/d.gguf", draft_max=5, features=info)
    assert args[:2] == ["--model-draft", "/d.gguf"]
    assert "--draft-max" in args
    assert "--spec-draft-n-max" not in args


def test_speculative_args_drop_unsupported_knobs():
    """A knob the build does not advertise is dropped, never swapped for the other
    spelling: its absence means the build uses its own default, which works — where
    a wrong flag does not."""
    info = _probe(HELP_OLD)  # has no -ngld and no p-min
    args = features.speculative_args(
        "/d.gguf", draft_gpu_layers=99, draft_p_min=0.4, features=info
    )
    assert "-ngld" not in args
    assert not any(a.endswith("p-min") for a in args)


def test_speculative_args_refuse_a_build_that_cannot_draft():
    """Raising beats returning flags that make the server exit during load — a
    failure that surfaces as 'did not start' and reads as a broken model."""
    info = _probe(HELP_NO_SPEC)
    with pytest.raises(RuntimeError, match="cannot do speculative decoding"):
        features.speculative_args("/d.gguf", features=info)


def test_explicit_zero_draft_layers_survives():
    """`is not None`, never falsiness: 0 means 'keep the draft on the CPU', which
    is a real choice."""
    info = _probe(HELP_NEW)
    args = features.speculative_args("/d.gguf", draft_gpu_layers=0, features=info)
    assert "-ngld" in args
    assert args[args.index("-ngld") + 1] == "0"


# ---- draft compatibility --------------------------------------------------------


def _meta(model="gpt2", pre="llama-bpe", vocab=128256, bos=1, eos=2, salt=""):
    return {
        "general.architecture": "llama",
        "tokenizer.ggml.model": model,
        "tokenizer.ggml.pre": pre,
        "tokenizer.ggml.tokens": [f"{salt}t{i}" for i in range(vocab)],
        "tokenizer.ggml.bos_token_id": bos,
        "tokenizer.ggml.eos_token_id": eos,
    }


def _patch_headers(monkeypatch, mapping):
    def fake(path):
        class H:
            pass

        h = H()
        h.path = path
        h.metadata = mapping[str(path)]
        return h

    monkeypatch.setattr(speculative.gguf, "read_header", fake)


def test_matching_tokenizers_are_compatible(monkeypatch, tmp_path):
    t, d = tmp_path / "t.gguf", tmp_path / "d.gguf"
    t.touch()
    d.touch()
    _patch_headers(monkeypatch, {str(t): _meta(), str(d): _meta()})
    assert speculative.check_compatible(t, d)["compatible"] is True


def test_different_tokenizer_family_is_refused(monkeypatch, tmp_path):
    t, d = tmp_path / "t.gguf", tmp_path / "d.gguf"
    t.touch()
    d.touch()
    _patch_headers(monkeypatch, {str(t): _meta(), str(d): _meta(model="bert")})
    verdict = speculative.check_compatible(t, d)
    assert verdict["compatible"] is False
    assert "tokenizers differ" in verdict["reason"]


def test_different_vocab_size_is_refused(monkeypatch, tmp_path):
    t, d = tmp_path / "t.gguf", tmp_path / "d.gguf"
    t.touch()
    d.touch()
    _patch_headers(monkeypatch, {str(t): _meta(), str(d): _meta(vocab=32000)})
    assert speculative.check_compatible(t, d)["compatible"] is False


def test_same_size_different_tokens_is_refused(monkeypatch, tmp_path):
    """The case size alone cannot catch: two 128k vocabularies holding entirely
    different tokens. This does not fail at load — the acceptance rate collapses
    and generation gets slower while looking like it worked."""
    t, d = tmp_path / "t.gguf", tmp_path / "d.gguf"
    t.touch()
    d.touch()
    _patch_headers(monkeypatch, {str(t): _meta(), str(d): _meta(salt="other-")})
    verdict = speculative.check_compatible(t, d)
    assert verdict["compatible"] is False
    assert "different tokens" in verdict["reason"]


def test_different_eos_is_refused(monkeypatch, tmp_path):
    """A draft emitting a different EOS means the two disagree about where a
    sequence ends."""
    t, d = tmp_path / "t.gguf", tmp_path / "d.gguf"
    t.touch()
    d.touch()
    _patch_headers(monkeypatch, {str(t): _meta(), str(d): _meta(eos=99)})
    assert speculative.check_compatible(t, d)["compatible"] is False


def test_unreadable_header_is_a_refusal_not_a_pass(monkeypatch, tmp_path):
    """The failure mode of getting this wrong is silent, so anything we cannot
    establish is treated as incompatible."""

    def boom(path):
        raise OSError("nope")

    monkeypatch.setattr(speculative.gguf, "read_header", boom)
    verdict = speculative.check_compatible(tmp_path / "a", tmp_path / "b")
    assert verdict["compatible"] is False
    assert "could not read" in verdict["reason"]


def test_a_model_is_not_its_own_draft(monkeypatch, tmp_path):
    t = tmp_path / "t.gguf"
    t.touch()
    _patch_headers(monkeypatch, {str(t): _meta()})
    assert speculative.check_compatible(t, t)["compatible"] is False


# ---- the VRAM split -------------------------------------------------------------


def _plan(layers=32, per_layer=100 * 1024 * 1024, kv=0, complete=True):
    return {
        "layerCount": layers,
        "layerBytes": [per_layer] * layers,
        "overheadBytes": 200 * 1024 * 1024,
        "totalBytes": per_layer * layers + 200 * 1024 * 1024,
        "kvBytesPerToken": kv,
        "complete": complete,
        "error": "",
    }


def _patch_plans(monkeypatch, target, draft):
    calls = {"n": 0}

    def fake(path):
        calls["n"] += 1
        return target if calls["n"] == 1 else draft

    monkeypatch.setattr(speculative, "layer_plan", fake)


def test_spec_plan_offers_no_plan_without_a_vram_figure(monkeypatch):
    _patch_plans(monkeypatch, _plan(), _plan(layers=4))
    out = speculative.spec_plan("t", "d", vram_mb=None, context=4096)
    assert out["targetGpuLayers"] is None
    assert "nothing to divide" in out["reason"]


def test_spec_plan_refuses_an_incompletely_sized_model(monkeypatch):
    """`layer_plan.complete` False means an unrecognised quantization, so every
    total is a floor. Budgeting against a floor is an OOM at load."""
    _patch_plans(monkeypatch, _plan(complete=False), _plan(layers=4))
    out = speculative.spec_plan("t", "d", vram_mb=16000, context=4096)
    assert out["targetGpuLayers"] is None
    assert "quantization" in out["reason"]


def test_spec_plan_puts_both_on_the_card_when_they_fit(monkeypatch):
    _patch_plans(monkeypatch, _plan(layers=4), _plan(layers=2))
    out = speculative.spec_plan("t", "d", vram_mb=16000, context=4096)
    assert out["fits"] is True
    assert out["targetGpuLayers"] == 999
    assert out["draftGpuLayers"] == 999


def test_spec_plan_splits_the_target_when_it_does_not_fit(monkeypatch):
    _patch_plans(monkeypatch, _plan(layers=32), _plan(layers=2))
    out = speculative.spec_plan("t", "d", vram_mb=2000, context=1024)
    assert out["fits"] is False
    assert 0 < out["targetGpuLayers"] < 32
    # The draft goes on whole or not at all: splitting it would cost a PCIe round
    # trip per token on the very path whose cheapness is the premise of drafting.
    assert out["draftGpuLayers"] == 999


def test_spec_plan_refuses_when_the_draft_alone_will_not_fit(monkeypatch):
    _patch_plans(monkeypatch, _plan(layers=32), _plan(layers=64))
    out = speculative.spec_plan("t", "d", vram_mb=1500, context=1024)
    assert out["targetGpuLayers"] is None
    assert "cost more than it saves" in out["reason"]


def test_headroom_is_the_callers(monkeypatch):
    """`layer_plan` reports weights and KV only and declines to invent a
    compute-buffer allowance; this inherits that rather than baking one in."""
    _patch_plans(monkeypatch, _plan(layers=4), _plan(layers=2))
    out = speculative.spec_plan(
        "t", "d", vram_mb=1000, context=1024, headroom_mb=999999
    )
    assert "headroom" in out["reason"]


# ---- the bench ------------------------------------------------------------------


def test_percentiles_not_means():
    """No mean is reported: over a bimodal result it hides that one prompt got
    2.4x and another 0.8x, which is this feature's defining property."""
    r = specbench.PromptResult(label="x", samples=[10.0, 10.0, 10.0, 100.0])
    out = r.to_dict()
    assert "mean" not in out
    # The mean here is 32.5 — nearly a lie about typical throughput.
    assert out["tokensPerSecondP50"] == pytest.approx(10.0)


def test_p10_exposes_a_slow_tail_the_median_hides():
    """p10, not min: one cold outlier is not the tail that matters, but a tenth of
    requests running at a third speed certainly is — and the median says nothing
    about it."""
    r = specbench.PromptResult(label="x", samples=[14.0, 40.0, 41.0, 42.0, 43.0])
    out = r.to_dict()
    assert out["tokensPerSecondP50"] == pytest.approx(41.0)
    assert out["tokensPerSecondP10"] < 30.0


def test_compare_reports_a_slowdown_as_a_result():
    """A feature that does not pay off is a finding, not an error to hide."""
    base = {
        "overallP50": 40.0,
        "prompts": [
            {"label": "prose", "tokensPerSecondP50": 40.0, "acceptanceRate": None}
        ],
    }
    drafted = {
        "overallP50": 28.0,
        "prompts": [
            {"label": "prose", "tokensPerSecondP50": 28.0, "acceptanceRate": 0.2}
        ],
    }
    out = specbench.compare(base, drafted)
    assert out["overallSpeedup"] == pytest.approx(0.7)
    assert "slower" in out["verdict"]
    assert out["perPrompt"][0]["speedup"] == pytest.approx(0.7)


def test_compare_calls_a_small_change_no_change():
    base = {"overallP50": 40.0, "prompts": []}
    drafted = {"overallP50": 42.0, "prompts": []}
    assert "no meaningful change" in specbench.compare(base, drafted)["verdict"]


def test_compare_reports_a_real_speedup():
    base = {"overallP50": 20.0, "prompts": []}
    drafted = {"overallP50": 48.0, "prompts": []}
    assert "faster" in specbench.compare(base, drafted)["verdict"]

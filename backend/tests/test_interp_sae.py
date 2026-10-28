"""SAEs on traces: loading, the arithmetic, the reconstruction self-check, and the
facts and labels the Library attaches to an SAE.

A tiny synthetic JumpReLU SAE stands in for Gemma Scope: its decoder rows are an
orthonormal basis and its negative, so a residual built from them is reconstructed exactly (FVU 0,
`verified`) and random noise is not (`drifted`) — the two verdicts on purpose.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from safetensors.numpy import save_file

from backend.modules.interp import artifacts, catalog, sae
from backend.modules.llamacpp import lens

D, WIDTH, LAYER = 8, 16, 3


def _write_sae(
    folder: Path, arch: str = "jump_relu", drop: str | None = None
) -> dict[str, Any]:
    folder.mkdir(parents=True)
    rng = np.random.default_rng(0)
    basis, _ = np.linalg.qr(rng.normal(size=(D, D)))
    # Each basis direction and its negative: with ReLU-style gating, any residual in
    # the span is one feature per component, so a reconstruction can be exact.
    w_dec = np.concatenate([basis.T, -basis.T], axis=0).astype(np.float32)  # [16, 8]
    params = {
        "w_enc": w_dec.T.copy(),  # [8, 16]
        "w_dec": w_dec,
        "b_enc": np.zeros(WIDTH, np.float32),
        "b_dec": np.zeros(D, np.float32),
        "threshold": np.full(WIDTH, 0.5, np.float32),
    }
    if drop:
        params.pop(drop)
    # C order: `save_file` writes the raw buffer, and the transposed views above are
    # F-ordered — saved as-is they come back as different (wrong) matrices.
    params = {k: np.ascontiguousarray(v) for k, v in params.items()}
    save_file(params, str(folder / "params.safetensors"))
    (folder / "config.json").write_text(
        json.dumps(
            {
                "hf_hook_point_in": f"model.layers.{LAYER}.output",
                "architecture": arch,
                "width": WIDTH,
                "l0": 2,
            }
        )
    )
    return {"id": f"test:{folder.name}", "path": str(folder)}


@dataclass
class FakeTrace:
    directory: Path
    manifest: dict[str, Any]


def _trace(tmp_path: Path, n_tokens: int) -> FakeTrace:
    d = tmp_path / "trace"
    d.mkdir(exist_ok=True)
    (d / "tokens.json").write_text(
        json.dumps([{"index": i, "id": i, "text": f"t{i}"} for i in range(n_tokens)])
    )
    return FakeTrace(d, {"fidelity": "fp16"})


def _residuals(monkeypatch, columns: np.ndarray, n_tokens: int) -> None:
    """Make `lens.read_residuals` return `columns` ([d, k]) at LAYER."""
    monkeypatch.setattr(
        lens,
        "read_residuals",
        lambda trace, pass_index=0: lens.Residuals(
            by_layer={LAYER: columns},
            n_tokens=n_tokens,
            n_embd=int(columns.shape[0]),
            result_output=None,
            result_norm=None,
        ),
    )


@pytest.fixture
def tiny(tmp_path) -> sae.Sae:
    return sae.load(
        _write_sae(tmp_path / "resid_post" / f"layer_{LAYER}_width_16_l0_medium")
    )


def test_load_reads_architecture_site_layer_and_trained_l0(tiny):
    assert (tiny.arch, tiny.site, tiny.layer) == ("jump_relu", "resid_post", LAYER)
    assert (tiny.d_in, tiny.width, tiny.expected_l0) == (D, WIDTH, 2.0)


@pytest.mark.parametrize(
    ("arch", "drop", "message"),
    [
        ("jump_relu", "threshold", "without a threshold"),
        ("gated", None, "unsupported SAE architecture"),
        ("jump_relu", "w_dec", "expected w_enc/w_dec"),
    ],
)
def test_load_refuses_what_it_cannot_run(tmp_path, arch, drop, message):
    art = _write_sae(tmp_path / "resid_post" / "layer_3_x", arch=arch, drop=drop)
    with pytest.raises(sae.SaeError, match=message):
        sae.load(art)


def test_jumprelu_zeroes_below_threshold(tiny):
    x = tiny.w_dec[0:1] * 0.4 + tiny.w_dec[1:2] * 3.0  # feature 0 under its threshold
    acts = tiny.encode(x)
    assert acts[0, 0] == 0.0 and acts[0, 1] == pytest.approx(3.0, abs=1e-5)


def test_an_exact_reconstruction_is_verified_and_bos_is_left_out(
    monkeypatch, tmp_path, tiny
):
    rng = np.random.default_rng(1)
    # Each token: two decoder directions, well above threshold — exactly representable.
    rows = [tiny.w_dec[i] * 2 + tiny.w_dec[(i + 3) % D] * 5 for i in range(6)]
    x = np.stack(rows)
    x[0] = rng.normal(size=D) * 1000  # a BOS the SAE cannot explain: must not count
    _residuals(monkeypatch, x.T.astype(np.float32), n_tokens=6)
    out = sae.encode_trace(_trace(tmp_path, 6), tiny)
    assert out["check"]["state"] == "verified"
    assert out["check"]["fvu"] == pytest.approx(0.0, abs=1e-4)
    assert out["check"]["skipped"] == 1 and out["check"]["tokens"] == 5
    assert out["tokens"][2]["text"] == "t2"
    # BOS's features are not in the trace-wide ranking.
    assert all(f["position"] != 0 for f in out["top"])
    assert out["top"][0]["peak"] == pytest.approx(5.0, abs=1e-4)


def test_noise_is_drifted(monkeypatch, tmp_path, tiny):
    x = np.random.default_rng(2).normal(size=(6, D)).astype(np.float32) * 0.3
    _residuals(monkeypatch, x.T, n_tokens=6)
    assert sae.encode_trace(_trace(tmp_path, 6), tiny)["check"]["state"] == "drifted"


def test_a_feature_that_peaks_after_bos_is_flagged(monkeypatch, tmp_path, tiny):
    x = np.stack([tiny.w_dec[0] * 9] + [tiny.w_dec[1] * 2] * 4)
    x = np.concatenate([np.zeros((1, D)), x])  # BOS, then the first token
    _residuals(monkeypatch, x.T.astype(np.float32), n_tokens=6)
    top = {f["index"]: f for f in sae.encode_trace(_trace(tmp_path, 6), tiny)["top"]}
    first = next(f for f in top.values() if f["position"] == 1)
    assert first["firstToken"] is True
    assert any(not f["firstToken"] for f in top.values())


def test_too_few_tokens_is_not_checked():
    x = np.ones((2, D), np.float32)
    assert sae.reconstruction_check(x, x, skip=1)["state"] == "not_checked"


@pytest.mark.parametrize(
    ("columns", "message"),
    [
        (np.full((D, 4), np.inf, np.float32), "non-finite"),
        (np.ones((D + 2, 4), np.float32), "different model"),
    ],
)
def test_encode_refuses_bad_residuals(monkeypatch, tmp_path, tiny, columns, message):
    _residuals(monkeypatch, columns, n_tokens=4)
    with pytest.raises(sae.SaeError, match=message):
        sae.encode_trace(_trace(tmp_path, 4), tiny)


def test_a_pruned_layer_reports_positions_from_the_tail(monkeypatch, tmp_path, tiny):
    # llama.cpp prunes late layers to the last k positions; positions must follow.
    x = np.stack([tiny.w_dec[2] * 4, tiny.w_dec[5] * 4])
    _residuals(monkeypatch, x.T.astype(np.float32), n_tokens=7)
    out = sae.encode_trace(_trace(tmp_path, 7), tiny)
    assert out["firstPosition"] == 5
    assert [p["position"] for p in out["perToken"]] == [5, 6]
    values = sae.feature_activations(_trace(tmp_path, 7), tiny, 2)
    assert values["firstPosition"] == 5 and values["values"][0] == pytest.approx(
        4.0, abs=1e-4
    )


# ── what the Library attaches to an SAE ──────────────────────────────────────


def test_facts_come_from_the_weights_and_the_hook(tmp_path):
    folder = tmp_path / "resid_post" / "layer_9_width_16k_l0_medium"
    _write_sae(folder)
    facts = artifacts.facts_for(
        "resid_post/layer_9_width_16k_l0_medium",
        {
            "config.json": folder / "config.json",
            "params.safetensors": folder / "params.safetensors",
        },
    )
    # The hook beats the folder name; the encoder's shape beats the config's width.
    assert facts["layer"] == LAYER and facts["source"]["layer"] == "config.json"
    assert (facts["dIn"], facts["width"]) == (D, WIDTH)
    assert facts["source"]["dIn"] == "params.safetensors"


@pytest.mark.parametrize(
    ("choice", "expected"),
    [
        ("resid_post/layer_13_width_16k_l0_medium", "13-gemmascope-2-res-16k"),
        ("resid_post/layer_7_width_262k_l0_medium", "7-gemmascope-2-res-262k"),
        # Same width, different dictionary: no borrowed labels.
        ("resid_post/layer_13_width_16k_l0_small", None),
        ("resid_post/layer_13_width_262k_l0_medium_seed_1", None),
    ],
)
def test_only_medium_l0_dictionaries_get_neuronpedia_labels(choice, expected):
    source = catalog.neuronpedia_source("gemma-scope-2-1b-it-resid", choice)
    assert (source or {}).get("source") == expected
    if expected:
        assert source["model"] == "gemma-3-1b-it"
    assert catalog.neuronpedia_source("qwen3-0.6b-transcoders", choice) is None

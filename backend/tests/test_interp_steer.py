"""Steering: the control-vector buffer the tracer hands llama.cpp, the direction a
feature steers along, and how a steered run is judged against its default.

The buffer test matters most. llama.cpp's control-vector buffer starts at *layer 1*
— layer `il` sits at offset `(il - 1) * n_embd` — so an off-by-one there steers
the wrong block, silently, with text that still looks steered.
"""

from __future__ import annotations

import types

import numpy as np
import pytest

from backend.modules.interp import steer
from backend.modules.interp.sae import Sae
from backend.modules.llamacpp import tracer

N_EMBD, N_LAYER = 4, 6


def _fake_llama(record: dict) -> types.SimpleNamespace:
    def setter(ctx, data, length, n_embd, il_start, il_end):
        record.update(
            data=[data[i] for i in range(length)],
            length=length,
            n_embd=n_embd,
            range=(il_start, il_end),
        )
        return record.get("code", 0)

    return types.SimpleNamespace(
        llama_model_n_embd=lambda model: N_EMBD,
        llama_model_n_layer=lambda model: N_LAYER,
        llama_set_adapter_cvec=setter,
    )


def test_the_buffer_puts_layer_il_at_offset_il_minus_one():
    record: dict = {}
    out = tracer._apply_steer(
        _fake_llama(record),
        model=None,
        context=None,
        steer={"vector": [1.0, 0.0, 0.0, 0.0], "scale": 3.0, "layers": [2, 3]},
    )
    assert record["range"] == (2, 3) and record["length"] == N_EMBD * N_LAYER
    rows = np.array(record["data"]).reshape(N_LAYER, N_EMBD)
    # Row 0 is layer 1; layers 2 and 3 are rows 1 and 2.
    assert rows[1].tolist() == [3.0, 0.0, 0.0, 0.0]
    assert rows[2].tolist() == [3.0, 0.0, 0.0, 0.0]
    assert not rows[[0, 3, 4, 5]].any()
    assert out == {"layers": [2, 3], "scale": 3.0, "norm": 3.0}


@pytest.mark.parametrize(
    ("steer_spec", "message"),
    [
        ({"vector": [1.0, 0.0], "scale": 1, "layers": [2, 2]}, "2 wide"),
        (
            {"vector": [1.0] * N_EMBD, "scale": 1, "layers": [0, 0]},
            "cannot steer block 0",
        ),
        ({"vector": [1.0] * N_EMBD, "scale": 1, "layers": [3, N_LAYER]}, "outside"),
    ],
)
def test_bad_steering_is_refused_before_llama_cpp_sees_it(steer_spec, message):
    record: dict = {}
    with pytest.raises(RuntimeError, match=message):
        tracer._apply_steer(_fake_llama(record), None, None, steer_spec)
    assert not record


def test_a_refused_vector_is_an_error():
    with pytest.raises(RuntimeError, match="refused"):
        tracer._apply_steer(
            _fake_llama({"code": -1}),
            None,
            None,
            {"vector": [1.0] * N_EMBD, "scale": 1, "layers": [1, 1]},
        )


def test_no_steer_is_no_call():
    record: dict = {}
    assert tracer._apply_steer(_fake_llama(record), None, None, None) is None
    assert not record


def _sae(w_dec: np.ndarray) -> Sae:
    width, d = w_dec.shape
    return Sae(
        artifact_id="t",
        arch="relu",
        site="resid_post",
        layer=3,
        w_enc=w_dec.T.copy(),
        b_enc=np.zeros(width, np.float32),
        w_dec=w_dec.astype(np.float32),
        b_dec=np.zeros(d, np.float32),
        threshold=None,
        k=None,
    )


def test_the_direction_is_the_unit_decoder_row():
    model = _sae(np.array([[3.0, 4.0], [0.0, 0.0]], np.float32))
    assert steer.feature_direction(model, 0).tolist() == pytest.approx([0.6, 0.8])
    with pytest.raises(steer.SteerError, match="zero decoder row"):
        steer.feature_direction(model, 1)
    with pytest.raises(steer.SteerError, match="out of range"):
        steer.feature_direction(model, 2)


@pytest.mark.parametrize(
    ("default", "steered", "strength", "state"),
    [
        (0.0, 300.0, 1.0, "arrived"),
        (50.0, 40.0, 1.0, "did_not_arrive"),
        (22.2, 0.0, -1.0, "arrived"),  # the France feature, pushed down: it went
        (0.0, 0.0, -1.0, "nothing_to_suppress"),
        (5.0, 5.0, 0.0, "no_push"),
    ],
)
def test_arrival_follows_the_sign_of_the_push(default, steered, strength, state):
    assert steer.arrival_state(default, steered, strength) == state


def test_next_token_shift_names_movers_among_tokens_that_matter():
    vocab = ["France", "Japan", "Germany", "tail"]
    default = np.log(np.array([0.995, 0.002, 0.002, 1e-9]))
    steered = np.log(np.array([0.47, 0.3, 0.23, 1e-6]))  # tail x1000, still irrelevant
    shift = steer.next_token_shift(default, steered, lambda i: vocab[i], top=2)
    assert shift["defaultTop"]["token"] == "France"
    assert shift["steeredTop"]["token"] == "France"
    assert [g["token"] for g in shift["gained"]] == ["Japan", "Germany"]
    assert shift["lost"][0]["token"] == "France"
    assert all(g["token"] != "tail" for g in shift["gained"])
    assert shift["kl"] > 0


def test_unnamed_tokens_fall_back_to_ids():
    logits = np.zeros(3)
    shift = steer.next_token_shift(logits, logits + np.array([0, 1, 0]), None, top=1)
    assert shift["gained"][0]["token"] == "#1"

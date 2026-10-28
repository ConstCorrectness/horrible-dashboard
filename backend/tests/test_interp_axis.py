"""The Assistant Axis: the fit, its held-out check, the bands, and the cap arithmetic.

The fit's fixture is a synthetic residual stream with a planted persona direction
`u`: assistant answers sit at +1 along it and role answers at a role-specific
negative offset, with noise everywhere else, and only some layers carry the signal.
The axis must recover `u` where it is planted, prefer those layers, and say it
separates; a layer without the signal must not look like it does.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from backend.modules.interp import artifacts, axis, axis_runner

L, D = 6, 32
SIGNAL_LAYERS = {2, 3, 4}


def _plant(seed: int = 0):
    rng = np.random.default_rng(seed)
    u = rng.standard_normal(D)
    u /= np.linalg.norm(u)
    roles = ["pirate", "ghost", "teacher", "dragon"]
    offsets = {
        "assistant": 1.0,
        "pirate": -1.0,
        "ghost": -1.5,
        "teacher": -0.3,
        "dragon": -1.2,
    }

    def answer(name: str, n: int) -> np.ndarray:
        x = rng.standard_normal((n, L, D)) * 0.3
        for layer in SIGNAL_LAYERS:
            x[:, layer] += 4 * offsets[name] * u
        return x

    means, labels, held, held_tokens = [], [], [], []
    for name in ["assistant", "assistant", *roles]:
        for q in range(5):
            tokens = answer(name, 12)
            means.append(tokens.mean(axis=0))
            labels.append(name)
            is_held = q >= 3
            held.append(is_held)
            if is_held:
                held_tokens.append((name, tokens.astype(np.float16)))
    return u, np.stack(means), labels, np.asarray(held), held_tokens


def test_auc_is_mann_whitney_with_ties():
    assert axis.auc(np.array([3.0, 4.0]), np.array([1.0, 2.0])) == 1.0
    assert axis.auc(np.array([1.0]), np.array([1.0])) == 0.5
    assert axis.auc(np.array([2.0, 0.0]), np.array([1.0])) == 0.5
    assert axis.auc(np.array([]), np.array([1.0])) is None


def test_fit_recovers_the_planted_direction_and_checks_it_on_held_out_answers():
    u, means, labels, held, held_tokens = _plant()
    fitted = axis.fit(means, labels, held, held_tokens)
    vectors = fitted["vectors"]
    for layer in SIGNAL_LAYERS:
        assert abs(float(vectors[layer] @ u)) > 0.95
        assert fitted["layers"][layer]["tokenAuc"] == 1.0
    # Where nothing is planted, the axis is noise and the check does not pretend.
    assert fitted["layers"][1]["tokenAuc"] < 0.8
    assert fitted["defaultLayer"] in SIGNAL_LAYERS
    assert fitted["check"]["state"] == "separates"
    # The teacher sits nearer the assistant than the ghost does.
    rp = fitted["roleProjections"]
    d = fitted["defaultLayer"]
    assert (
        rp["ghost"][d]
        < rp["pirate"][d]
        < rp["teacher"][d]
        < fitted["assistantProjection"][d]
    )


def test_the_cap_band_floors_at_the_assistant_p25():
    _u, means, labels, held, held_tokens = _plant()
    fitted = axis.fit(means, labels, held, held_tokens)
    cap = fitted["cap"]
    assert cap["layers"] and all(1 <= layer < L - 1 for layer in cap["layers"])
    for layer in cap["layers"]:
        assert cap["tau"][str(layer)] == fitted["layers"][layer]["assistant"]["p25"]


def test_choose_layer_never_picks_block_zero_or_the_last():
    layers = [{"layer": i, "tokenAuc": 0.5, "dPrime": 0.0} for i in range(5)]
    layers[0]["tokenAuc"] = layers[4]["tokenAuc"] = 1.0
    layers[2]["tokenAuc"] = 0.9
    assert axis.choose_layer(layers) == 2


def test_fit_refuses_without_both_sides():
    _u, means, labels, held, held_tokens = _plant()
    with pytest.raises(axis.AxisError):
        axis.fit(means, ["assistant"] * len(labels), held, held_tokens)


def test_cap_rows_count_what_a_lower_push_already_carries():
    v = np.zeros(4, np.float32)
    v[0] = 1.0
    vectors = {3: v, 5: v}
    tau = {3: 10.0, 5: 10.0}
    # Layer 3 is 4 short; that push is still in the residual at layer 5 (same axis),
    # so layer 5, 2 short on its own, needs nothing more.
    assert axis_runner._cap_rows({3: 6.0, 5: 8.0}, vectors, tau) == {3: 4.0}
    # An orthogonal axis at layer 5 carries none of it.
    w = np.zeros(4, np.float32)
    w[1] = 1.0
    assert axis_runner._cap_rows({3: 6.0, 5: 8.0}, {3: v, 5: w}, tau) == {
        3: 4.0,
        5: 2.0,
    }
    assert axis_runner._cap_rows({3: 11.0, 5: 12.0}, vectors, tau) == {}


def test_drift_summary_reads_against_the_bands():
    bands = {"assistant": {"p25": 5.0}, "role": {"p50": 1.0}}
    s = axis.drift_summary([None, 10.0, 6.0, 4.0, 0.0], bands)
    assert s["min"] == 0.0 and s["minAt"] == 4
    assert s["belowFloor"] == 0.5 and s["inRoleRange"] == 0.25
    assert s["slopePer100"] < 0


def test_parse_roles_takes_names_or_bare_descriptions():
    roles = axis.parse_roles(
        "# comment\npirate: a pirate captain\n- a lonely lighthouse keeper\n\n"
    )
    assert roles == [
        {"name": "pirate", "description": "a pirate captain"},
        {
            "name": "a lonely lighthouse keeper",
            "description": "a lonely lighthouse keeper",
        },
    ]


def test_flatten_messages_for_a_small_models_template():
    flat = axis.flatten_messages(
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
            {"role": "tool", "content": "42"},
            {"role": "user", "content": "thanks"},
        ]
    )
    assert [m["role"] for m in flat] == ["system", "user", "assistant", "user"]
    assert flat[2]["content"] == '[{"id": "1"}]'
    assert flat[3]["content"] == "[tool result]\n42\n\nthanks"


def test_a_built_axis_is_a_library_row_and_deletes_with_it():
    _u, means, labels, held, held_tokens = _plant()
    fitted = axis.fit(means, labels, held, held_tokens)
    out = axis.new_axis_dir("C:/models/tiny-model.gguf")
    out.mkdir(parents=True)
    np.save(out / "axis.npy", fitted.pop("vectors"))
    meta = {
        **fitted,
        "modelPath": "C:/models/tiny-model.gguf",
        "modelName": "tiny-model",
        "nLayer": L,
        "nEmbd": D,
        "roles": ["pirate", "ghost", "teacher", "dragon"],
        "questions": ["q"] * 5,
        "heldOut": 2,
        "maxTokens": 12,
    }
    (out / "axis.json").write_text(json.dumps(meta), encoding="utf-8")
    art = axis.register(out, "sha-123")
    assert art["kind"] == "axis" and art["facts"]["layer"] == fitted["defaultLayer"]
    ax = axis.load(art["id"])
    assert ax.vectors.shape == (L, D)
    assert any(a["id"] == art["id"] for a in axis.list_axes())
    assert ax.summary()["check"]["state"] == "separates"
    assert artifacts.delete_artifact(art["id"])
    assert not out.exists()
    with pytest.raises(axis.AxisError):
        axis.load(art["id"])


def test_message_drift_reads_replies_against_the_first_one():
    bands = {"assistant": {"p50": 100.0}, "role": {"p50": -100.0}}
    values = [None, 90.0, 80.0, 80.0, 10.0, 0.0, 0.0, -100.0, -100.0, -90.0]
    messages = [
        {"role": "user", "start": 0, "end": 2},
        {"role": "assistant", "start": 2, "end": 4},
        {"role": "user", "start": 4, "end": 7},
        {"role": "assistant", "start": 7, "end": 10},
    ]
    d = axis.message_drift(values, messages, bands)
    assert [p["median"] for p in d["perMessage"]] == [90.0, 80.0, 0.0, -100.0]
    # The replies fell 180 of a 200 gap: almost all the way to a role.
    assert d["change"] == -180.0 and d["changeInGaps"] == -0.9
    assert axis.message_drift(values, messages, {})["perMessage"]
    assert "changeInGaps" not in axis.message_drift(values, messages, {})

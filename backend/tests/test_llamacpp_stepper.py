"""The stepper's reads: strided slices, attention planes, residual deltas, MoE routing.

Built on `TraceWriter` fixtures, not a native library — every failure here is one
that *parses*: a stride divided by the wrong width, a KV cache drawn at its
allocated size, a pruned tail aligned from the left. Each produces plausible
numbers, which is why each is pinned.
"""

from __future__ import annotations

import struct

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.modules.llamacpp import stepper, traces
from backend.modules.llamacpp.routes import router
from backend.modules.llamacpp.tracer import DEFAULT_CAPTURE, Tracer

N_EMBD = 8
N_TOK = 4
N_KV_ALLOC = 16  # the allocated cache — wider than the 4 positions in use
N_HEAD = 3


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture()
def client(data_dir) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


def _f32_nb(ne: list[int]) -> list[int]:
    """Contiguous ggml strides in **f32 bytes**, which is what the tracer records
    even for an fp16 record (it downcasts after reading the live tensor)."""
    nb = [4]
    for n in ne[:3]:
        nb.append(nb[-1] * n)
    return nb


def _residual(layer: int) -> np.ndarray:
    """[tokens, embd]: a stream that grows by `layer` each block."""
    base = np.arange(N_TOK * N_EMBD, dtype=np.float32).reshape(N_TOK, N_EMBD) / 10
    return base + layer


def _attention() -> np.ndarray:
    """[head, row, kv_alloc]: causal softmax rows over the first N_TOK columns."""
    att = np.zeros((N_HEAD, N_TOK, N_KV_ALLOC), dtype=np.float32)
    for h in range(N_HEAD):
        for r in range(N_TOK):
            weights = np.arange(1, r + 2, dtype=np.float32) + h
            att[h, r, : r + 1] = weights / weights.sum()
    return att


def write_stepper_trace(trace_id: str = "step", *, moe: bool = False) -> None:
    directory = traces.traces_root() / trace_id
    writer = traces.TraceWriter(
        directory, {"traceId": trace_id, "promptTokens": N_TOK, "modelSha": "abc"}
    )
    for layer in range(3):
        stream = _residual(layer)
        # ggml order: ne0 is the fastest axis, so [embd, tokens] is stream.T's
        # column-major layout — i.e. the row-major bytes of [tokens, embd].
        ne = [N_EMBD, N_TOK, 1, 1]
        writer.append(
            name=f"l_out-{layer}",
            op="ADD",
            dtype="f16",
            ne=ne,
            nb=_f32_nb(ne),
            pass_index=0,
            fidelity="fp16",
            payload=traces.encode_f16(stream.reshape(-1).tolist()),
        )
        att = _attention()
        ne = [N_KV_ALLOC, N_TOK, N_HEAD, 1]
        writer.append(
            name=f"kq_soft_max-{layer}",
            op="SOFT_MAX",
            dtype="f16",
            ne=ne,
            nb=_f32_nb(ne),
            pass_index=0,
            fidelity="fp16",
            payload=traces.encode_f16(att.reshape(-1).tolist()),
        )
        if moe:
            topk = np.array([[layer, 1], [2, 3], [3, 1], [1, 0]], dtype=np.int32)
            ne = [2, N_TOK, 1, 1]
            writer.append(
                name=f"ffn_moe_topk-{layer}",
                op="VIEW",
                dtype="i32",
                ne=ne,
                nb=_f32_nb(ne),
                pass_index=0,
                fidelity="full",
                payload=topk.tobytes(),
            )
            ne = [4, N_TOK, 1, 1]
            probs = np.full((N_TOK, 4), 0.25, dtype=np.float32)
            writer.append(
                name=f"ffn_moe_probs-{layer}",
                op="SOFT_MAX",
                dtype="f32",
                ne=ne,
                nb=_f32_nb(ne),
                pass_index=0,
                fidelity="full",
                payload=probs.tobytes(),
            )
            ne = [1, 2, N_TOK, 1]
            weights = np.array([[0.7, 0.3]] * N_TOK, dtype=np.float32)
            writer.append(
                name=f"ffn_moe_weights-{layer}",
                op="GET_ROWS",
                dtype="f32",
                ne=ne,
                nb=_f32_nb(ne),
                pass_index=0,
                fidelity="full",
                payload=weights.tobytes(),
            )
    # The last block pruned to the final position, as llama.cpp does on a prompt.
    tail = _residual(3)[-1:]
    ne = [N_EMBD, 1, 1, 1]
    writer.append(
        name="l_out-3",
        op="ADD",
        dtype="f32",
        ne=ne,
        nb=_f32_nb(ne),
        pass_index=0,
        fidelity="full",
        payload=tail.tobytes(),
    )
    writer.append(
        name="kq-9",
        op="MUL_MAT",
        dtype="f16",
        ne=[N_KV_ALLOC, N_TOK, N_HEAD, 1],
        nb=[0, 0, 0, 0],
        pass_index=0,
        fidelity="summary",
        summary={"rms": 1.0},
    )
    writer.close(
        [
            {"index": i, "id": i, "text": f"t{i}", "generated": False}
            for i in range(N_TOK)
        ]
    )


def _record(trace: traces.Trace, name: str) -> traces.TraceRecord:
    return next(r for r in trace.records if r.name == name)


def test_fp16_strides_are_read_in_source_width(data_dir) -> None:
    """`nb` counts f32 bytes on an fp16 record. Dividing by the stored width (2)
    would double every stride and read a different, plausible-looking tensor."""
    write_stepper_trace()
    trace = traces.load("step")
    array = stepper.record_array(trace, _record(trace, "l_out-1"))
    assert array.shape == (1, 1, N_TOK, N_EMBD)
    np.testing.assert_allclose(array[0, 0], _residual(1), rtol=1e-3)


def test_non_contiguous_strides_are_honoured(data_dir) -> None:
    """A permuted view: ne says [embd, tokens] but the bytes are token-fastest."""
    directory = traces.traces_root() / "perm"
    writer = traces.TraceWriter(directory, {"traceId": "perm", "promptTokens": N_TOK})
    stored = _residual(0).T.copy()  # bytes laid out as [embd][tokens]
    writer.append(
        name="kqv_out-0",
        op="PERMUTE",
        dtype="f32",
        ne=[N_EMBD, N_TOK, 1, 1],
        nb=[4 * N_TOK, 4, 4 * N_TOK * N_EMBD, 4 * N_TOK * N_EMBD],
        pass_index=0,
        fidelity="full",
        payload=stored.tobytes(),
    )
    writer.close([])
    trace = traces.load("perm")
    array = stepper.record_array(trace, trace.records[0])
    np.testing.assert_allclose(array[0, 0], _residual(0))


def test_attention_is_cropped_to_the_kv_in_use_and_never_pooled(client) -> None:
    write_stepper_trace()
    trace = traces.load("step")
    index = _record(trace, "kq_soft_max-1").index
    body = client.get(
        f"/api/llamacpp/traces/step/record/{index}/matrix?head=2&cols=2"
    ).json()
    assert body["heads"] == N_HEAD and body["head"] == 2
    # 16 allocated columns, 4 in use: the masked zeros are dropped, and the
    # `cols=2` budget does not pool attention (a max over two keys credits one).
    assert (body["rows"], body["cols"]) == (N_TOK, N_TOK)
    assert body["kvCropped"] is True and body["pooled"] is False
    matrix = np.array(body["values"]).reshape(N_TOK, N_TOK)
    np.testing.assert_allclose(matrix, _attention()[2, :, :N_TOK], atol=1e-3)
    assert np.allclose(np.triu(matrix, 1), 0)  # causal


def test_residual_plane_pools_with_full_resolution_row_norms(client) -> None:
    write_stepper_trace()
    trace = traces.load("step")
    index = _record(trace, "l_out-2").index
    body = client.get(f"/api/llamacpp/traces/step/record/{index}/matrix?cols=8").json()
    assert body["pooled"] is False and body["cols"] == N_EMBD
    expected = np.linalg.norm(_residual(2), axis=1)
    np.testing.assert_allclose(body["rowNorms"], expected, rtol=1e-3)
    pooled = stepper.matrix(trace, _record(trace, "l_out-2"), max_cols=2)
    assert pooled.pooled and pooled.cols == 2
    # Norms are of the real vectors, not of the two-column thumbnail.
    np.testing.assert_allclose(pooled.row_norms, expected, rtol=1e-3)


def test_a_summary_record_yields_no_matrix(client) -> None:
    write_stepper_trace()
    trace = traces.load("step")
    index = _record(trace, "kq-9").index
    response = client.get(f"/api/llamacpp/traces/step/record/{index}/matrix")
    assert response.status_code == 422
    assert "summary" in response.json()["detail"]


def test_pruned_tail_is_offset_not_aligned_from_the_left(client) -> None:
    write_stepper_trace()
    trace = traces.load("step")
    tail = stepper.matrix(trace, _record(trace, "l_out-3"))
    assert tail.rows == 1 and tail.row_offset == N_TOK - 1
    body = client.get("/api/llamacpp/traces/step/layer/3/delta").json()
    # Only the final position exists at block 3, and it is compared against the
    # final position of block 2 — not against token 0.
    assert body["positions"] == [N_TOK - 1]
    np.testing.assert_allclose(body["deltaNorm"], [np.sqrt(N_EMBD)], rtol=1e-3)


def test_residual_delta_reports_relative_change_and_turn(client) -> None:
    write_stepper_trace()
    body = client.get("/api/llamacpp/traces/step/layer/2/delta").json()
    assert body["positions"] == list(range(N_TOK))
    before = np.linalg.norm(_residual(1), axis=1)
    np.testing.assert_allclose(body["relative"], np.sqrt(N_EMBD) / before, rtol=1e-3)
    assert all(0.9 < c <= 1.0001 for c in body["cosine"])


def test_head_summaries_find_the_first_token_weight(client) -> None:
    write_stepper_trace()
    trace = traces.load("step")
    index = _record(trace, "kq_soft_max-0").index
    heads = client.get(f"/api/llamacpp/traces/step/record/{index}/heads").json()[
        "heads"
    ]
    assert [h["head"] for h in heads] == [0, 1, 2]
    att = _attention()
    np.testing.assert_allclose(heads[0]["firstWeight"], att[0, :, 0].mean(), rtol=1e-3)
    # Row 0 attends only to itself, so self-weight is at least that row's 1.0 / rows.
    assert heads[0]["selfWeight"] >= 1.0 / N_TOK


def test_experts_read_i32_routing_and_count_per_layer(client) -> None:
    write_stepper_trace("moe", moe=True)
    body = client.get("/api/llamacpp/traces/moe/experts").json()
    assert body["moe"] is True and body["nExpert"] == 4
    layer1 = next(row for row in body["layers"] if row["layer"] == 1)
    assert layer1["selections"] == [[1, 1], [2, 3], [3, 1], [1, 0]]
    assert layer1["weights"][0] == pytest.approx([0.7, 0.3])
    assert layer1["counts"] == [1, 4, 1, 2]


def test_a_dense_model_says_so_rather_than_drawing_an_empty_atlas(client) -> None:
    write_stepper_trace()
    body = client.get("/api/llamacpp/traces/step/experts").json()
    assert body == {"moe": False, "passIndex": 0, "nExpert": 0, "layers": []}


def test_i32_round_trips_through_the_trace_format(data_dir) -> None:
    raw = struct.pack("<3i", 7, 0, 1023)
    assert traces.decode(raw, "i32") == [7.0, 0.0, 1023.0]


def test_moe_router_nodes_are_in_the_default_capture() -> None:
    """Literals verified in the installed llama.dll (b10362). A name that matches
    nothing fails silently — the SSM capture set's lesson."""
    tracer = Tracer({"architecture": "qwen3moe"})
    for name in ("ffn_moe_topk-3", "ffn_moe_probs-3", "ffn_moe_weights-3"):
        assert tracer.wanted(name), name
    assert "ffn_moe_topk" in DEFAULT_CAPTURE

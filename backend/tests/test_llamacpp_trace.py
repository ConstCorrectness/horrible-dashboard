"""The trace path: ABI self-check, trace format round-trip, and the byte routes.

No `llama-cpp-python` here and none needed. The three things that can silently
go wrong — the struct mirror drifting, the writer and reader disagreeing about
the blob, and a `Range` request serving the wrong bytes — are all testable
without a native library, and every one of them is a failure that *parses*.
"""

from __future__ import annotations

import ctypes
import json
import struct

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.modules.llamacpp import ggml_abi, traces
from backend.modules.llamacpp.routes import router


# --- a stub ggml -----------------------------------------------------------


class StubGgml:
    """Just enough ggml to bind against.

    `bind()` is duck-typed precisely so this is possible: the ABI check is pure
    arithmetic over a struct and four small functions, and mocking it here is
    what makes "the mirror drifted" a test rather than a production surprise.
    """

    #: type id → (type_size, blck_size, name)
    TYPES = {0: (4, 1, b"f32"), 1: (2, 1, b"f16"), 8: (18, 32, b"q5_0")}

    def __init__(self, nbytes: int | None = None, n_dims: int | None = None) -> None:
        self._nbytes = nbytes
        self._n_dims = n_dims

    def ggml_type_size(self, type_id: int) -> int:
        return self.TYPES[type_id][0]

    def ggml_blck_size(self, type_id: int) -> int:
        return self.TYPES[type_id][1]

    def ggml_type_name(self, type_id: int) -> bytes:
        return self.TYPES[type_id][2]

    def ggml_nbytes(self, pointer: object) -> int:
        if self._nbytes is not None:
            return self._nbytes
        tensor = ctypes.cast(pointer, ggml_abi.TensorPtr).contents
        return ggml_abi.expected_nbytes(ggml_abi.bind(self), tensor)

    def ggml_n_dims(self, pointer: object) -> int:
        if self._n_dims is not None:
            return self._n_dims
        tensor = ctypes.cast(pointer, ggml_abi.TensorPtr).contents
        return ggml_abi.expected_n_dims(tensor)

    def ggml_nelements(self, pointer: object) -> int:
        tensor = ctypes.cast(pointer, ggml_abi.TensorPtr).contents
        result = 1
        for i in range(ggml_abi.GGML_MAX_DIMS):
            result *= int(tensor.ne[i])
        return result

    def ggml_get_name(self, pointer: object) -> bytes:
        return b"ffn_out-7"

    def ggml_op_desc(self, pointer: object) -> bytes:
        return b"MUL_MAT"

    def ggml_is_quantized(self, type_id: int) -> bool:
        return self.TYPES[type_id][1] > 1

    def ggml_backend_tensor_get(
        self, pointer: object, out: object, offset: int, size: int
    ) -> None:
        raise AssertionError("not used in these tests")


def f32_tensor(ne: tuple[int, int, int, int]) -> ggml_abi.GgmlTensor:
    """A contiguous fp32 tensor, laid out the way ggml lays one out."""
    tensor = ggml_abi.GgmlTensor()
    tensor.type = 0
    nb = [4]
    for i in range(1, ggml_abi.GGML_MAX_DIMS):
        nb.append(nb[i - 1] * ne[i - 1])
    for i in range(ggml_abi.GGML_MAX_DIMS):
        tensor.ne[i] = ne[i]
        tensor.nb[i] = nb[i]
    return tensor


def test_self_check_passes_on_a_consistent_tensor() -> None:
    api = ggml_abi.bind(StubGgml())
    tensor = f32_tensor((128, 6, 1, 1))
    ggml_abi.self_check(api, ctypes.byref(tensor))


def test_self_check_rejects_a_mutated_struct() -> None:
    """A field inserted into `struct ggml_tensor` shifts `ne`/`nb`.

    Simulated the honest way round: the library reports the true size while the
    bytes the mirror reads are the shifted ones, so the two disagree — which is
    exactly what happens on a real drift, and must abort rather than write a
    trace full of tensors whose shapes are one field out.
    """
    api = ggml_abi.bind(StubGgml(nbytes=128 * 6 * 4 + 8))
    tensor = f32_tensor((128, 6, 1, 1))
    with pytest.raises(ggml_abi.AbiMismatch, match="ggml ABI mismatch"):
        ggml_abi.self_check(api, ctypes.byref(tensor))


def test_self_check_rejects_a_dimension_disagreement() -> None:
    api = ggml_abi.bind(StubGgml(n_dims=4))
    tensor = f32_tensor((128, 6, 1, 1))
    with pytest.raises(ggml_abi.AbiMismatch, match="ggml_n_dims"):
        ggml_abi.self_check(api, ctypes.byref(tensor))


def test_quantized_nbytes_uses_the_block_branch() -> None:
    """A quantized type packs `blck_size` elements per block.

    Treating `nb[0]` as a stride — the obvious misreading — overstates a q5_0
    row by 32×, so this branch is not a micro-detail.
    """
    api = ggml_abi.bind(StubGgml())
    tensor = ggml_abi.GgmlTensor()
    tensor.type = 8  # q5_0: 18 bytes per 32 elements
    ne = (256, 4, 1, 1)
    nb = [18, 18 * 256 // 32, 18 * 256 // 32 * 4, 18 * 256 // 32 * 4]
    for i in range(ggml_abi.GGML_MAX_DIMS):
        tensor.ne[i] = ne[i]
        tensor.nb[i] = nb[i]
    assert ggml_abi.expected_nbytes(api, tensor) == 18 * 256 // 32 * 4


def test_missing_symbol_is_a_mismatch_not_an_attribute_error() -> None:
    class Partial(StubGgml):
        ggml_op_desc = None  # type: ignore[assignment]

    with pytest.raises(ggml_abi.AbiMismatch, match="ggml_op_desc"):
        ggml_abi.bind(Partial())


# --- the trace format ------------------------------------------------------


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    return tmp_path


def write_trace(trace_id: str = "t1", *, values=(1.0, -2.0, 3.5, 0.0)) -> dict:
    directory = traces.traces_root() / trace_id
    writer = traces.TraceWriter(
        directory,
        {
            "traceId": trace_id,
            "modelSha": "abc123",
            "llamaBuild": "test-build",
            "byteOrder": "little",
        },
    )
    writer.append(
        name="inp_embd",
        op="GET_ROWS",
        dtype="f32",
        ne=[len(values), 1, 1, 1],
        nb=[4, 4 * len(values), 0, 0],
        pass_index=0,
        fidelity="full",
        payload=struct.pack(f"<{len(values)}f", *values),
    )
    writer.append(
        name="ffn_out-7",
        op="MUL_MAT",
        dtype="f16",
        ne=[len(values), 1, 1, 1],
        nb=[2, 2 * len(values), 0, 0],
        pass_index=1,
        fidelity="fp16",
        payload=traces.encode_f16(list(values)),
    )
    writer.append(
        name="attn_weights-7",
        op="SOFT_MAX",
        dtype="q5_0",
        ne=[4096, 4096, 1, 1],
        nb=[18, 0, 0, 0],
        pass_index=1,
        fidelity="summary",
        summary={"count": 4.0, "absMax": 3.5},
    )
    return writer.close([{"index": 0, "id": 1, "text": "hi", "generated": False}])


def test_manifest_and_blob_round_trip(data_dir) -> None:
    values = (1.0, -2.0, 3.5, 0.0)
    manifest = write_trace(values=values)
    assert manifest["recordCount"] == 3

    trace = traces.load("t1")
    assert trace is not None
    records = trace.records
    assert [r.fidelity for r in records] == ["full", "fp16", "summary"]

    blob = trace.blob.read_bytes()
    first = records[0]
    assert traces.decode(
        blob[first.offset : first.offset + first.length], "f32"
    ) == list(values)
    second = records[1]
    round_tripped = traces.decode(
        blob[second.offset : second.offset + second.length], "f16"
    )
    assert round_tripped == pytest.approx(list(values), abs=1e-3)

    # A summary record occupies no bytes at all: the next record's offset is the
    # previous one's end, so a reader that trusted `offset` blindly would be
    # handed the wrong tensor if this ever changed.
    assert records[2].length == 0
    assert records[2].offset == second.offset + second.length


def test_layer_is_parsed_from_the_node_name_and_may_be_absent() -> None:
    assert traces.layer_of("ffn_out-7") == 7
    assert traces.layer_of("l_out-15") == 15
    # Filing the embedding table under layer 0 would be quietly wrong.
    assert traces.layer_of("inp_embd") is None
    assert traces.layer_of("result_norm") is None


def test_a_summary_record_refuses_bytes(data_dir) -> None:
    writer = traces.TraceWriter(traces.traces_root() / "bad", {"traceId": "bad"})
    with pytest.raises(ValueError, match="statistics, not bytes"):
        writer.append(
            name="x",
            op="ADD",
            dtype="f32",
            ne=[1, 1, 1, 1],
            nb=[4, 0, 0, 0],
            pass_index=0,
            fidelity="summary",
            payload=b"\x00\x00\x00\x00",
        )


def test_decode_refuses_a_quantized_dtype() -> None:
    """There is no dequantizer here, and pretending otherwise is the bug."""
    with pytest.raises(ValueError, match="never written as bytes"):
        traces.decode(b"\x00" * 18, "q5_0")


def test_prune_drops_oldest_first(data_dir) -> None:
    for index, trace_id in enumerate(("old", "new")):
        manifest = write_trace(trace_id)
        path = traces.traces_root() / trace_id / "manifest.json"
        manifest["createdAt"] = 1000.0 + index
        path.write_text(json.dumps(manifest), encoding="utf-8")

    kept = traces.list_traces()
    assert [t.trace_id for t in kept] == ["new", "old"]

    removed = traces.prune(budget=kept[0].bytes_on_disk())
    assert removed == ["old"]
    assert [t.trace_id for t in traces.list_traces()] == ["new"]


def test_overlay_requires_both_build_and_model(data_dir) -> None:
    manifest = write_trace()
    assert traces.matches_run(manifest, "test-build", "abc123")
    # A trace of the same model on a different build is a different run.
    assert not traces.matches_run(manifest, "other-build", "abc123")
    assert not traces.matches_run(manifest, "test-build", "different-model")
    assert not traces.matches_run(manifest, "", "")


def test_estimate_scales_with_attention_and_is_labelled() -> None:
    base = dict(n_layer=32, n_embd=4096, n_head=32, prompt_tokens=256)
    plain = traces.estimate(**base)
    with_attention = traces.estimate(**base, attention=True)
    assert with_attention.bytes_total > plain.bytes_total
    assert "Estimate" in with_attention.note

    # Selecting one layer of 32 is roughly a 32× saving — the knob has to be
    # visible in the number or the pane is showing a constant.
    one_layer = traces.estimate(**base, layers=1)
    assert one_layer.bytes_total * 30 < plain.bytes_total

    # The token cap is a hard cap, not a default: asking past it changes nothing.
    capped = traces.estimate(
        n_layer=32, n_embd=4096, n_head=32, prompt_tokens=traces.MAX_TRACE_TOKENS * 4
    )
    at_cap = traces.estimate(
        n_layer=32, n_embd=4096, n_head=32, prompt_tokens=traces.MAX_TRACE_TOKENS
    )
    assert capped.bytes_total == at_cap.bytes_total


def test_trace_id_cannot_escape_the_trace_directory(data_dir) -> None:
    with pytest.raises(ValueError):
        traces.delete_trace("../../models")
    with pytest.raises(ValueError):
        traces.load("..")


# --- routes ----------------------------------------------------------------


@pytest.fixture()
def client(data_dir) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


def test_listing_reports_unavailability_rather_than_an_empty_list(client) -> None:
    res = client.get("/api/llamacpp/traces")
    assert res.status_code == 200
    body = res.json()
    # llama-cpp-python is an optional extra, so on a normal dev box this is the
    # real answer — and the pane needs the reason, not a bare empty list.
    if not body["available"]:
        assert "uv sync --extra llamacpp" in body["reason"]
    assert body["budgetBytes"] > 0


def test_range_requests_including_the_suffix_form(client) -> None:
    write_trace()
    blob = (traces.traces_root() / "t1" / "tensors.bin").read_bytes()
    url = "/api/llamacpp/traces/t1/tensors"

    full = client.get(url)
    assert full.status_code == 200
    assert full.headers["accept-ranges"] == "bytes"
    assert full.content == blob

    part = client.get(url, headers={"range": "bytes=4-11"})
    assert part.status_code == 206
    assert part.content == blob[4:12]
    assert part.headers["content-range"] == f"bytes 4-11/{len(blob)}"

    # `bytes=-3` is the *last* three bytes. Reading the number as a start offset
    # is the classic misread and silently serves the wrong part of the file.
    suffix = client.get(url, headers={"range": "bytes=-3"})
    assert suffix.status_code == 206
    assert suffix.content == blob[-3:]

    open_ended = client.get(url, headers={"range": "bytes=8-"})
    assert open_ended.status_code == 206
    assert open_ended.content == blob[8:]

    assert client.get(url, headers={"range": "bytes=99999-"}).status_code == 416


def test_record_values_are_decoded_server_side(client) -> None:
    write_trace()
    res = client.get("/api/llamacpp/traces/t1/record/0")
    assert res.status_code == 200
    body = res.json()
    assert body["values"] == [1.0, -2.0, 3.5, 0.0]
    assert body["truncated"] is False
    assert body["summary"]["absMax"] == 3.5

    # An fp16 record decodes to the same numbers within fp16's precision — the
    # pane never sees raw bytes and never has to know the width.
    fp16 = client.get("/api/llamacpp/traces/t1/record/1").json()
    assert fp16["values"] == pytest.approx([1.0, -2.0, 3.5, 0.0], abs=1e-3)


def test_a_summary_record_returns_no_values(client) -> None:
    """Statistics must never be served in the shape of a tensor."""
    write_trace()
    body = client.get("/api/llamacpp/traces/t1/record/2").json()
    assert body["values"] == []
    assert body["summary"]["absMax"] == 3.5
    assert body["record"]["fidelity"] == "summary"


def test_truncation_is_declared(client) -> None:
    write_trace()
    body = client.get("/api/llamacpp/traces/t1/record/0?limit=2").json()
    assert body["values"] == [1.0, -2.0]
    assert body["truncated"] is True


def test_capture_set_is_transformer_only_for_a_transformer() -> None:
    from backend.modules.llamacpp import tracer

    assert tracer.capture_for("llama") == tracer.DEFAULT_CAPTURE
    assert tracer.capture_for("") == tracer.DEFAULT_CAPTURE


def test_capture_set_adds_the_state_space_nodes_for_an_ssm_model() -> None:
    """Without this a Mamba trace records the residual stream and nothing about the
    mechanism, which reads as a broken tracer rather than an unasked-for capture."""
    from backend.modules.llamacpp import tracer

    for arch in ("mamba", "mamba2", "jamba", "falcon-h1", "nemotron_h"):
        patterns = tracer.capture_for(arch)
        assert "ssm_" in patterns, arch
        # Union, never replacement: a hybrid interleaves attention blocks with SSM
        # blocks, so dropping the transformer patterns would blind half of it.
        assert set(tracer.DEFAULT_CAPTURE).issubset(patterns), arch


def test_capture_set_covers_the_rwkv_variants_by_prefix() -> None:
    from backend.modules.llamacpp import tracer

    for arch in ("rwkv6", "rwkv7", "rwkv6qwen2", "arwkv7"):
        assert "time_mix_" in tracer.capture_for(arch), arch


def test_ssm_capture_matches_the_names_this_llama_build_actually_uses() -> None:
    """Names verified against the literals in the installed llama.dll, not guessed:
    `ssm_conv1d` is real and `ssm_scan` is not. A capture pattern that matches
    nothing fails silently, which is the whole failure mode being fixed here."""
    from backend.modules.llamacpp.tracer import Tracer

    tracer = Tracer({"architecture": "mamba2"})
    for name in ("ssm_in-0", "ssm_conv1d-0", "ssm_x-3", "ssm_dt-3", "ssm_out-11"):
        assert tracer.wanted(name), name
    # …and the transformer nodes still match, for a hybrid.
    assert tracer.wanted("ffn_out-2")


def _multipass_trace(trace_id: str = "series") -> None:
    """The same node captured in three passes — what a pin watches. Pass 1 is a
    `summary` record with no stored statistic, i.e. a real gap."""
    directory = traces.traces_root() / trace_id
    writer = traces.TraceWriter(
        directory,
        {
            "traceId": trace_id,
            "modelSha": "abc",
            "llamaBuild": "b",
            "byteOrder": "little",
        },
    )
    for pass_index, values in ((0, (1.0, 1.0, 1.0, 1.0)), (2, (3.0, 3.0, 3.0, 3.0))):
        writer.append(
            name="l_out-7",
            op="MUL",
            dtype="f32",
            ne=[4, 1, 1, 1],
            nb=[4, 16, 0, 0],
            pass_index=pass_index,
            fidelity="full",
            payload=struct.pack("<4f", *values),
        )
    writer.append(
        name="l_out-7",
        op="MUL",
        dtype="f32",
        ne=[4, 1, 1, 1],
        nb=[4, 16, 0, 0],
        pass_index=1,
        fidelity="summary",
        summary={},
    )
    writer.close([])


def test_series_reports_one_point_per_pass_in_pass_order(client) -> None:
    _multipass_trace()
    body = client.get(
        "/api/llamacpp/traces/series/series", params={"name": "l_out-7"}
    ).json()

    assert [p["passIndex"] for p in body["points"]] == [0, 1, 2]
    # rms of four 1.0s is 1.0; of four 3.0s is 3.0.
    assert body["points"][0]["value"] == pytest.approx(1.0)
    assert body["points"][2]["value"] == pytest.approx(3.0)


def test_series_leaves_an_unmeasured_pass_as_a_gap(client) -> None:
    """A `summary` record with no stored statistic has nothing to report. Emitting
    0.0 would draw a plunge to zero that never happened."""
    _multipass_trace()
    body = client.get(
        "/api/llamacpp/traces/series/series", params={"name": "l_out-7"}
    ).json()
    gap = body["points"][1]
    assert gap["value"] is None
    assert gap["fidelity"] == "summary"


def test_series_summarizes_the_whole_record_not_a_prefix(client) -> None:
    """`get_record` caps at `limit` because it ships values to a browser. A series
    compares passes, so a statistic over a prefix would not be a comparison."""
    write_trace()
    capped = client.get("/api/llamacpp/traces/t1/record/0?limit=2").json()
    assert capped["summary"]["absMax"] == 2.0  # only saw 1.0, -2.0

    series = client.get(
        "/api/llamacpp/traces/t1/series", params={"name": "inp_embd"}
    ).json()
    assert series["points"][0]["value"] == pytest.approx(
        client.get("/api/llamacpp/traces/t1/record/0").json()["summary"]["rms"]
    )


def test_series_404s_for_a_node_this_trace_never_captured(client) -> None:
    """A pin can name a node the capture set didn't include. That has to be an
    honest 404 the pane renders as "unresolved", not an empty series that looks
    like a node which was captured and happened to be flat."""
    write_trace()
    assert (
        client.get(
            "/api/llamacpp/traces/t1/series", params={"name": "ssm_in-0"}
        ).status_code
        == 404
    )


def test_missing_trace_and_record_are_404(client) -> None:
    assert client.get("/api/llamacpp/traces/nope").status_code == 404
    write_trace()
    assert client.get("/api/llamacpp/traces/t1/record/99").status_code == 404


def test_delete_removes_the_directory(client) -> None:
    write_trace()
    assert client.delete("/api/llamacpp/traces/t1").json() == {"deleted": True}
    assert traces.list_traces() == []

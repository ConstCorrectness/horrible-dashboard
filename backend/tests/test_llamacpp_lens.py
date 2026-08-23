"""The lens: does reading an activation as words agree with the model itself?

The whole correctness argument for `lens.py` is that at the last layer the
identity lens must reproduce the logits the traced forward pass *already stored*.
So these tests build both halves from scratch — a real GGUF written with
llama.cpp's own writer, and a trace whose `result_output` was computed by the
same arithmetic the model would have used — and then check that the lens agrees.
A version that disagrees is exactly the silent failure the `verified` flag
exists to catch, so `test_verify_reports_false_...` deliberately breaks it.

No native library and no downloaded weights: a four-layer, sixteen-wide model
with a nine-token vocabulary exercises every path that a 12B one does.
"""

from __future__ import annotations

import numpy as np
import pytest

from backend.modules.llamacpp import lens, traces

N_EMBD = 16
N_VOCAB = 9
N_LAYER = 4
EPS = 1e-6

VOCAB = ["<pad>", "the", "Ġcapital", "Ġof", "ĠFrance", "Ġis", "ĠParis", "Ġa", "Ġb"]


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    return tmp_path


def _write_gguf(path, *, tied: bool = False, softcap: float | None = None):
    """A minimal but genuine GGUF, written with the same library llama.cpp uses."""
    from gguf import GGUFWriter

    rng = np.random.default_rng(7)
    head = rng.normal(0, 1, size=(N_VOCAB, N_EMBD)).astype(np.float32)
    norm = rng.normal(1, 0.1, size=(N_EMBD,)).astype(np.float32)

    writer = GGUFWriter(str(path), "llama")
    writer.add_block_count(N_LAYER)
    writer.add_embedding_length(N_EMBD)
    writer.add_layer_norm_rms_eps(EPS)
    if softcap is not None:
        writer.add_final_logit_softcapping(softcap)
    writer.add_tokenizer_model("gpt2")
    writer.add_token_list(VOCAB)
    # ggml stores the fastest-varying dimension first, and GGUFWriter reads the
    # numpy shape as (…, n_embd) — so a [n_vocab, n_embd] array lands as an
    # ne = [n_embd, n_vocab] tensor, which is what the loader expects.
    writer.add_tensor("token_embd.weight", head if tied else head[::-1].copy())
    if not tied:
        writer.add_tensor("output.weight", head)
    writer.add_tensor("output_norm.weight", norm)
    writer.write_header_to_file()
    writer.write_kv_data_to_file()
    writer.write_tensors_to_file()
    writer.close()
    return head, norm


def _column_major(matrix: np.ndarray) -> list[float]:
    """`[n_embd, n_tokens]` flattened the way ggml lays a tensor out."""
    return [float(v) for v in matrix.T.reshape(-1)]


def _write_trace(
    trace_id: str,
    model_path,
    residuals: dict[int, np.ndarray],
    *,
    result_norm: np.ndarray | None = None,
    result_output: np.ndarray | None = None,
    fidelity: str = "full",
) -> traces.Trace:
    directory = traces.traces_root() / trace_id
    writer = traces.TraceWriter(
        directory,
        {
            "traceId": trace_id,
            "modelPath": str(model_path),
            "modelSha": f"sha-{trace_id}",
            "llamaBuild": "test",
            "byteOrder": "little",
        },
    )

    def append(name: str, matrix: np.ndarray) -> None:
        values = _column_major(matrix)
        payload = (
            traces.encode_f16(values)
            if fidelity == "fp16"
            else np.asarray(values, dtype="<f4").tobytes()
        )
        writer.append(
            name=name,
            op="MUL_MAT",
            dtype="f16" if fidelity == "fp16" else "f32",
            ne=[matrix.shape[0], matrix.shape[1], 1, 1],
            nb=[4, 4 * matrix.shape[0], 0, 0],
            pass_index=0,
            fidelity=fidelity,
            payload=payload,
        )

    for layer in sorted(residuals):
        append(
            "inp_embd" if layer == lens.EMBEDDING_LAYER else f"l_out-{layer}",
            residuals[layer],
        )
    if result_norm is not None:
        append("result_norm", result_norm)
    if result_output is not None:
        append("result_output", result_output)
    writer.close(
        [
            {"index": i, "id": i + 1, "text": VOCAB[i + 1], "generated": False}
            for i in range(residuals[0].shape[1])
        ]
    )
    trace = traces.load(trace_id)
    assert trace is not None
    return trace


def _consistent_trace(
    tmp_path,
    *,
    tied: bool = False,
    softcap: float | None = None,
    n_tokens: int = 3,
    fidelity: str = "full",
    trace_id: str = "t1",
):
    """A GGUF plus a trace whose logits really are that GGUF's logits."""
    model_path = tmp_path / "tiny.gguf"
    head, norm = _write_gguf(model_path, tied=tied, softcap=softcap)

    rng = np.random.default_rng(11)
    residuals = {
        layer: rng.normal(0, 1, size=(N_EMBD, n_tokens)).astype(np.float32)
        for layer in range(-1, N_LAYER)
    }
    top = residuals[N_LAYER - 1]
    normed = np.stack(
        [lens.rms_norm(top[:, i], norm, EPS) for i in range(n_tokens)], axis=1
    )
    logits = lens.softcap(head @ normed, softcap)
    trace = _write_trace(
        trace_id,
        model_path,
        residuals,
        result_norm=normed,
        result_output=logits,
        fidelity=fidelity,
    )
    return trace, head, norm, normed, logits


# --- the output head --------------------------------------------------------


def test_untied_model_uses_output_weight(data_dir, tmp_path) -> None:
    model_path = tmp_path / "m.gguf"
    _write_gguf(model_path, tied=False)
    un = lens.load_unembedding(model_path)
    assert un.tensor.name == "output.weight"
    assert un.tied is False
    assert (un.n_embd, un.n_vocab) == (N_EMBD, N_VOCAB)


def test_tied_model_falls_back_to_the_embedding_table(data_dir, tmp_path) -> None:
    model_path = tmp_path / "m.gguf"
    _write_gguf(model_path, tied=True)
    un = lens.load_unembedding(model_path)
    assert un.tensor.name == "token_embd.weight"
    assert un.tied is True


def test_softcap_is_read_from_the_metadata_not_assumed(data_dir, tmp_path) -> None:
    plain = tmp_path / "plain.gguf"
    capped = tmp_path / "capped.gguf"
    _write_gguf(plain)
    _write_gguf(capped, softcap=30.0)
    assert lens.load_unembedding(plain).logit_softcap is None
    assert lens.load_unembedding(capped).logit_softcap == 30.0


def test_weight_chunks_reconstruct_the_written_head(data_dir, tmp_path) -> None:
    model_path = tmp_path / "m.gguf"
    head, _norm = _write_gguf(model_path)
    un = lens.load_unembedding(model_path)
    rebuilt = np.zeros((N_VOCAB, N_EMBD), dtype=np.float32)
    for start, block in lens._weight_chunks(un, ""):
        rebuilt[start : start + block.shape[0]] = block
    assert np.allclose(rebuilt, head, atol=1e-6)


def test_a_single_row_read_matches_the_streamed_one(data_dir, tmp_path) -> None:
    model_path = tmp_path / "m.gguf"
    head, _norm = _write_gguf(model_path)
    un = lens.load_unembedding(model_path)
    assert np.allclose(lens._weight_row(un, "", 6), head[6], atol=1e-6)


# --- the self-check, which is the correctness argument ----------------------


def test_identity_lens_reproduces_the_traces_own_logits(data_dir, tmp_path) -> None:
    trace, _head, _norm, _normed, logits = _consistent_trace(tmp_path)
    grid = lens.compute_grid(trace, k=3)
    assert grid.verified == "true", grid.verify_note
    assert grid.verify_detail["argmaxAgrees"] is True
    # The top cell of the last row is the word the model itself would emit.
    expected = lens.render_piece(VOCAB[int(np.argmax(logits[:, -1]))], "gpt2")
    assert grid.cells[-1][-1]["texts"][0] == expected


def test_the_norm_is_checked_separately_from_the_head(data_dir, tmp_path) -> None:
    trace, *_ = _consistent_trace(tmp_path)
    grid = lens.compute_grid(trace, k=3)
    assert grid.verify_detail["normMaxAbsDiff"] < 1e-4


def test_verify_survives_an_fp16_trace(data_dir, tmp_path) -> None:
    """fp16 is the default fidelity, so the tolerance has to admit its rounding."""
    trace, *_ = _consistent_trace(tmp_path, fidelity="fp16")
    grid = lens.compute_grid(trace, k=3)
    assert grid.verified == "true", grid.verify_note


def test_verify_reports_false_when_the_output_path_disagrees(
    data_dir, tmp_path
) -> None:
    """The failure this flag exists for: numbers that parse and are wrong."""
    trace, head, norm, normed, _logits = _consistent_trace(tmp_path, trace_id="t2")
    # A model whose real logits came out of a *different* head. Every cell still
    # renders; only the check knows.
    wrong = np.roll(head, 3, axis=0) @ normed
    residuals = {
        layer: lens.read_residuals(trace).by_layer[layer]
        for layer in lens.read_residuals(trace).by_layer
    }
    broken = _write_trace(
        "t3",
        trace.manifest["modelPath"],
        residuals,
        result_output=wrong,
    )
    grid = lens.compute_grid(broken, k=3)
    assert grid.verified == "false"
    assert "does NOT reproduce" in grid.verify_note


def test_a_trace_without_captured_logits_says_unavailable(data_dir, tmp_path) -> None:
    model_path = tmp_path / "m.gguf"
    _write_gguf(model_path)
    rng = np.random.default_rng(3)
    residuals = {
        layer: rng.normal(0, 1, size=(N_EMBD, 2)).astype(np.float32)
        for layer in range(-1, N_LAYER)
    }
    trace = _write_trace("t4", model_path, residuals)
    grid = lens.compute_grid(trace, k=3)
    assert grid.verified == "unavailable"
    assert "nothing to check" in grid.verify_note


def test_softcapped_model_still_verifies(data_dir, tmp_path) -> None:
    """Gemma 2's `tanh(x/c)*c` is applied to `result_output`, so the lens must
    apply it too — without it the values disagree while the ranking does not,
    which is exactly the half-right failure the tolerance would otherwise hide."""
    trace, *_ = _consistent_trace(tmp_path, softcap=30.0)
    grid = lens.compute_grid(trace, k=3)
    assert grid.verified == "true", grid.verify_note


# --- the grid ---------------------------------------------------------------


def test_the_grid_covers_every_layer_including_the_embedding(
    data_dir, tmp_path
) -> None:
    trace, *_ = _consistent_trace(tmp_path, n_tokens=4)
    grid = lens.compute_grid(trace, k=2)
    assert grid.layers == [lens.EMBEDDING_LAYER, 0, 1, 2, 3]
    assert grid.positions == [0, 1, 2, 3]
    assert len(grid.cells) == 5 and len(grid.cells[0]) == 4
    assert len(grid.cells[0][0]["ids"]) == 2


def test_layers_and_positions_can_be_subset(data_dir, tmp_path) -> None:
    trace, *_ = _consistent_trace(tmp_path, n_tokens=4)
    grid = lens.compute_grid(trace, k=2, layers=[0, 3], positions=[1])
    assert grid.layers == [0, 3]
    assert grid.positions == [1]


def test_a_subset_that_excludes_the_top_layer_still_verifies(
    data_dir, tmp_path
) -> None:
    """The self-check rides its own column, so it does not depend on the user
    happening to ask for the layer it needs."""
    trace, *_ = _consistent_trace(tmp_path)
    grid = lens.compute_grid(trace, k=2, layers=[0])
    assert grid.layers == [0]
    assert grid.verified == "true", grid.verify_note


def test_an_empty_selection_is_an_error_not_an_empty_grid(data_dir, tmp_path) -> None:
    trace, *_ = _consistent_trace(tmp_path)
    with pytest.raises(lens.LensError):
        lens.compute_grid(trace, layers=[99])


def test_a_trace_with_no_residuals_says_so(data_dir, tmp_path) -> None:
    model_path = tmp_path / "m.gguf"
    _write_gguf(model_path)
    directory = traces.traces_root() / "bare"
    writer = traces.TraceWriter(
        directory, {"traceId": "bare", "modelPath": str(model_path)}
    )
    writer.append(
        name="attn_norm-0",
        op="RMS_NORM",
        dtype="f32",
        ne=[N_EMBD, 1, 1, 1],
        nb=[4, 4, 0, 0],
        pass_index=0,
        fidelity="full",
        payload=np.zeros(N_EMBD, dtype="<f4").tobytes(),
    )
    writer.close([])
    trace = traces.load("bare")
    assert trace is not None
    with pytest.raises(lens.LensError, match="captured no residual stream"):
        lens.compute_grid(trace)


def test_a_summary_record_is_skipped_rather_than_decoded(data_dir, tmp_path) -> None:
    """A summary record has no bytes by construction; the lens must not treat
    its absence as a zero vector."""
    trace, *_ = _consistent_trace(tmp_path)
    records = trace.manifest["records"]
    for record in records:
        if record["name"] == "l_out-1":
            record["length"] = 0
            record["fidelity"] = "summary"
    residuals = lens.read_residuals(trace)
    assert 1 not in residuals.by_layer
    assert 2 in residuals.by_layer


# --- tracking one token -----------------------------------------------------


def test_track_token_ranks_agree_with_the_grid(data_dir, tmp_path) -> None:
    trace, _head, _norm, _normed, logits = _consistent_trace(tmp_path)
    top = int(np.argmax(logits[:, -1]))
    tracked = lens.track_token(trace, top)
    assert tracked["text"] == lens.render_piece(VOCAB[top], "gpt2")
    # Rank 1 at the top layer, last position — that is the token the model emits.
    assert tracked["ranks"][-1][-1] == 1
    assert tracked["layers"] == [lens.EMBEDDING_LAYER, 0, 1, 2, 3]


def test_track_token_rejects_a_token_outside_the_vocabulary(data_dir, tmp_path) -> None:
    trace, *_ = _consistent_trace(tmp_path)
    with pytest.raises(lens.LensError, match="outside this model's vocabulary"):
        lens.track_token(trace, N_VOCAB + 5)


# --- rendering --------------------------------------------------------------


def test_byte_level_pieces_render_as_the_text_they_stand_for() -> None:
    assert lens.render_piece("Ġthe", "gpt2") == " the"
    assert lens.render_piece("▁the", "llama") == " the"
    # An unknown tokenizer is left alone rather than mangled by the wrong decoder.
    assert lens.render_piece("Ġthe", "rwkv") == "Ġthe"


# --- lens specifications ----------------------------------------------------


def test_identity_is_always_available_and_named_for_what_it_is(data_dir) -> None:
    specs = lens.available_lenses("abc123")
    assert [s.id for s in specs] == ["identity"]
    assert "logit lens" in specs[0].label
    assert specs[0].kind == "identity"


def test_an_unknown_lens_is_refused(data_dir) -> None:
    with pytest.raises(lens.LensError, match="no lens"):
        lens.resolve_lens("gemma-jlens", "abc123")


def test_a_fitted_lens_is_discovered_from_its_directory(data_dir) -> None:
    import json

    directory = lens.lens_root() / "abc123" / "wikitext"
    directory.mkdir(parents=True)
    (directory / "lens.json").write_text(
        json.dumps(
            {"kind": "jacobian", "label": "J-lens (wikitext)", "dModel": N_EMBD}
        ),
        encoding="utf-8",
    )
    specs = lens.available_lenses("abc123")
    assert [s.id for s in specs] == ["identity", "wikitext"]
    assert specs[1].kind == "jacobian"


def test_a_fitted_lens_cannot_claim_verification(data_dir, tmp_path) -> None:
    """A J-lens is not supposed to reproduce the model's output — reporting
    `true` for it would mean the check was measuring nothing."""
    import json

    trace, *_ = _consistent_trace(tmp_path, trace_id="t5")
    directory = lens.lens_root() / trace.manifest["modelSha"] / "fitted"
    directory.mkdir(parents=True)
    (directory / "lens.json").write_text(
        json.dumps({"kind": "jacobian"}), encoding="utf-8"
    )
    np.save(directory / "J_3.npy", np.eye(N_EMBD, dtype=np.float32))
    for layer in (-1, 0, 1, 2):
        np.save(directory / f"J_{layer}.npy", np.eye(N_EMBD, dtype=np.float32))

    grid = lens.compute_grid(trace, lens_id="fitted", k=2, layers=[3])
    assert grid.verified == "unavailable"
    assert "cannot be checked" in grid.verify_note
    assert "agrees" in grid.verify_note


def test_a_lens_of_the_wrong_width_is_refused(data_dir, tmp_path) -> None:
    import json

    trace, *_ = _consistent_trace(tmp_path, trace_id="t6")
    directory = lens.lens_root() / trace.manifest["modelSha"] / "wrong"
    directory.mkdir(parents=True)
    (directory / "lens.json").write_text(
        json.dumps({"kind": "jacobian"}), encoding="utf-8"
    )
    np.save(directory / "J_3.npy", np.eye(N_EMBD + 1, dtype=np.float32))
    with pytest.raises(lens.LensError, match="wide"):
        lens.compute_grid(trace, lens_id="wrong", layers=[3])


# --- routes -----------------------------------------------------------------


@pytest.fixture()
def client(data_dir):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.modules.llamacpp.routes import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


def test_grid_route_serves_cells_the_token_strip_and_the_verdict(
    client, tmp_path
) -> None:
    _consistent_trace(tmp_path, n_tokens=3)
    res = client.get("/api/llamacpp/traces/t1/lens?k=3")
    assert res.status_code == 200
    body = res.json()
    assert body["verified"] == "true"
    assert body["layers"] == [-1, 0, 1, 2, 3]
    assert len(body["cells"]) == 5
    assert len(body["cells"][0][0]["ids"]) == 3
    # The strip travels with the grid: a column is only readable next to the
    # token it belongs to, and a second round trip to fetch it would let the two
    # disagree about how many there are.
    assert len(body["tokens"]) == 3
    assert body["unembedding"]["tensor"] == "output.weight"


def test_grid_route_reports_a_bad_request_rather_than_guessing(
    client, tmp_path
) -> None:
    _consistent_trace(tmp_path)
    assert client.get("/api/llamacpp/traces/t1/lens?layers=0,oops").status_code == 400
    # A layer this trace does not have is the caller's error, not an empty grid.
    assert client.get("/api/llamacpp/traces/t1/lens?layers=99").status_code == 422


def test_track_route_returns_a_rank_per_cell(client, tmp_path) -> None:
    _consistent_trace(tmp_path)
    res = client.get("/api/llamacpp/traces/t1/lens/track?tokenId=4")
    assert res.status_code == 200
    body = res.json()
    assert len(body["ranks"]) == 5 and len(body["ranks"][0]) == 3
    assert all(1 <= rank <= N_VOCAB for row in body["ranks"] for rank in row)


def test_lenses_route_always_offers_identity(client, tmp_path) -> None:
    _consistent_trace(tmp_path)
    body = client.get("/api/llamacpp/traces/t1/lenses").json()
    assert [entry["id"] for entry in body["lenses"]] == ["identity"]
    assert body["available"] is True


def test_vocab_route_searches_the_models_own_vocabulary(client, tmp_path) -> None:
    model_path = tmp_path / "m.gguf"
    _write_gguf(model_path)
    body = client.get(f"/api/llamacpp/models/vocab?path={model_path}&q=Paris").json()
    assert [entry["id"] for entry in body["tokens"]] == [6]
    # The rendered text is what a picker shows; the raw piece is what matches a
    # search typed in the encoding.
    assert body["tokens"][0]["text"] == " Paris"
    assert body["tokens"][0]["piece"] == "ĠParis"
    assert body["total"] == N_VOCAB
    assert body["tokenizerModel"] == "gpt2"


def test_vocab_route_declares_truncation(client, tmp_path) -> None:
    model_path = tmp_path / "m.gguf"
    _write_gguf(model_path)
    body = client.get(f"/api/llamacpp/models/vocab?path={model_path}&limit=2").json()
    assert len(body["tokens"]) == 2
    assert body["truncated"] is True


def test_vocab_route_refuses_a_file_it_cannot_read(client, tmp_path) -> None:
    missing = tmp_path / "nope.gguf"
    assert client.get(f"/api/llamacpp/models/vocab?path={missing}").status_code == 422


# --- forking a trace: the intervention --------------------------------------


def test_fork_spec_replaces_only_the_edited_position(data_dir, tmp_path) -> None:
    from backend.modules.llamacpp.routes import fork_spec

    trace, *_ = _consistent_trace(tmp_path, n_tokens=3)
    spec = fork_spec(trace, [{"position": 1, "toId": 6}])
    # The parent's tokens are ids 1, 2, 3 (see _write_trace).
    assert spec["tokenIds"] == [1, 6, 3]


def test_fork_spec_stamps_what_was_actually_replaced(data_dir, tmp_path) -> None:
    """`fromId` is provenance, so it comes from the parent rather than the
    caller — a client that sent a stale one would make the fork lie about
    itself."""
    from backend.modules.llamacpp.routes import fork_spec

    trace, *_ = _consistent_trace(tmp_path, n_tokens=3)
    spec = fork_spec(trace, [{"position": 2, "fromId": 999, "toId": 4}])
    assert spec["edits"] == [{"position": 2, "fromId": 3, "toId": 4}]


def test_fork_spec_inherits_the_capture_settings(data_dir, tmp_path) -> None:
    """A fork that also changed the fidelity or the layer selection would not be
    comparable to the trace it forked, which is the only reason to make one."""
    from backend.modules.llamacpp.routes import fork_spec

    trace, *_ = _consistent_trace(tmp_path)
    trace.manifest["fidelity"] = "full"
    trace.manifest["layers"] = [0, 2]
    trace.manifest["capture"] = ["inp_embd", "l_out"]
    trace.manifest["attention"] = True
    spec = fork_spec(trace, [])
    assert spec["fidelity"] == "full"
    assert spec["layers"] == [0, 2]
    assert spec["capture"] == ["inp_embd", "l_out"]
    assert spec["attention"] is True
    assert spec["derivedFrom"] == trace.trace_id


def test_fork_spec_refuses_a_position_outside_the_prompt(data_dir, tmp_path) -> None:
    from backend.modules.llamacpp.routes import fork_spec

    trace, *_ = _consistent_trace(tmp_path, n_tokens=3)
    with pytest.raises(ValueError, match="outside this trace's 3 prompt tokens"):
        fork_spec(trace, [{"position": 3, "toId": 1}])


def test_fork_spec_ignores_generated_tokens(data_dir, tmp_path) -> None:
    """Only the prompt is forkable: a generated token is an *output*, and
    replacing one would be editing the answer rather than the question."""
    import json

    from backend.modules.llamacpp.routes import fork_spec

    trace, *_ = _consistent_trace(tmp_path, n_tokens=3)
    tokens = json.loads((trace.directory / "tokens.json").read_text(encoding="utf-8"))
    tokens.append({"index": 3, "id": 7, "text": "Ġa", "generated": True})
    (trace.directory / "tokens.json").write_text(json.dumps(tokens), encoding="utf-8")
    spec = fork_spec(trace, [])
    assert spec["tokenIds"] == [1, 2, 3]
    with pytest.raises(ValueError):
        fork_spec(trace, [{"position": 3, "toId": 1}])


def test_fork_route_reports_a_bad_edit_rather_than_running(client, tmp_path) -> None:
    _consistent_trace(tmp_path, n_tokens=3)
    res = client.post(
        "/api/llamacpp/traces/t1/fork", json={"edits": [{"position": 9, "toId": 1}]}
    )
    assert res.status_code == 422
    assert "outside this trace" in res.json()["detail"]


# --- the capture set drives what a trace costs -------------------------------


def test_a_lens_capture_set_costs_a_fraction_of_the_default(data_dir) -> None:
    common = dict(n_layer=32, n_embd=4096, n_head=32, prompt_tokens=64)
    full = traces.estimate(**common, nodes_per_layer=traces.nodes_per_layer([]))
    lens_only = traces.estimate(
        **common,
        nodes_per_layer=traces.nodes_per_layer(
            ["inp_embd", "l_out", "result_norm", "result_output"]
        ),
    )
    assert lens_only.bytes_total * 6 == full.bytes_total


def test_nodes_per_layer_ignores_the_once_per_pass_nodes(data_dir) -> None:
    """`inp_embd` and the output head are captured once per pass, not once per
    block, so counting them per-layer would inflate the estimate by half."""
    assert traces.nodes_per_layer(["l_out"]) == 1
    assert traces.nodes_per_layer(["inp_embd", "result_norm", "result_output"]) == 1
    assert traces.nodes_per_layer([]) == 6


def test_capture_sets_route_serves_the_real_node_names(client) -> None:
    from backend.modules.llamacpp.tracer import CAPTURE_PRESETS

    body = client.get("/api/llamacpp/traces/capture-sets").json()
    by_id = {entry["id"]: entry for entry in body["sets"]}
    assert by_id["lens"]["patterns"] == list(CAPTURE_PRESETS["lens"])
    # "the architecture's default", which only the tracer can resolve.
    assert by_id["default"]["patterns"] == []


def test_estimate_counts_supplied_token_ids_exactly(client, tmp_path) -> None:
    """A fork knows its token count precisely, so the estimate must not fall
    back to guessing from whitespace."""
    model_path = tmp_path / "m.gguf"
    _write_gguf(model_path)
    body = client.post(
        "/api/llamacpp/traces/estimate",
        json={"modelPath": str(model_path), "tokenIds": [1, 2, 3, 4, 5]},
    ).json()
    assert body["promptTokens"] == 5


# --- the shape a real trace actually has -------------------------------------
#
# llama.cpp prunes its graph to what the pass needs, so on a prompt pass the LAST
# block's `l_out` is one column wide (only the final position's residual produces
# logits) while every earlier block is the full width, and `result_output` is one
# column covering that same final position. A fixture where every tensor is the
# same width cannot catch any of the ways that goes wrong — these are built the
# ragged way a captured trace is.


def _pruned_trace(tmp_path, *, n_tokens: int = 4, trace_id: str = "p1"):
    """A trace shaped the way llama.cpp really writes one."""
    model_path = tmp_path / "tiny.gguf"
    head, norm = _write_gguf(model_path)

    rng = np.random.default_rng(23)
    residuals = {
        layer: rng.normal(0, 1, size=(N_EMBD, n_tokens)).astype(np.float32)
        for layer in range(N_LAYER - 1)
    }
    # The top block computed the final position alone.
    last = rng.normal(0, 1, size=(N_EMBD, 1)).astype(np.float32)
    residuals[N_LAYER - 1] = last
    normed = lens.rms_norm(last[:, 0], norm, EPS).reshape(N_EMBD, 1)
    logits = head @ normed
    trace = _write_trace(
        trace_id, model_path, residuals, result_norm=normed, result_output=logits
    )
    return trace, logits


def test_a_narrow_row_covers_the_end_of_the_sequence(data_dir, tmp_path) -> None:
    trace, _logits = _pruned_trace(tmp_path, n_tokens=4)
    residuals = lens.read_residuals(trace)
    assert residuals.n_tokens == 4
    wide = residuals.by_layer[0]
    narrow = residuals.by_layer[N_LAYER - 1]
    # The one column the top block has is the LAST position, not the first.
    assert lens.column_of(narrow, 4, 3) == 0
    assert lens.column_of(narrow, 4, 0) is None
    assert lens.column_of(wide, 4, 0) == 0


def test_an_uncomputed_cell_is_blank_rather_than_invented(data_dir, tmp_path) -> None:
    trace, _logits = _pruned_trace(tmp_path, n_tokens=4, trace_id="p2")
    grid = lens.compute_grid(trace, k=2)
    top = grid.layers.index(N_LAYER - 1)
    assert grid.cells[top][3] is not None
    assert grid.cells[top][:3] == [None, None, None]
    # Every earlier layer computed every position.
    assert all(cell is not None for cell in grid.cells[0])


def test_verify_pairs_the_ends_not_the_starts(data_dir, tmp_path) -> None:
    """The bug this catches: taking column 0 of a one-wide `result_output` and
    position 0 of the residual compares the last token's logits against the
    first token's activation, and reports every real trace as unverified."""
    trace, logits = _pruned_trace(tmp_path, n_tokens=4, trace_id="p3")
    grid = lens.compute_grid(trace, k=3)
    assert grid.verified == "true", grid.verify_note
    assert grid.verify_detail["position"] == 3
    expected = lens.render_piece(VOCAB[int(np.argmax(logits[:, 0]))], "gpt2")
    assert grid.cells[grid.layers.index(N_LAYER - 1)][3]["texts"][0] == expected


def test_track_token_leaves_uncomputed_cells_null(data_dir, tmp_path) -> None:
    trace, _logits = _pruned_trace(tmp_path, n_tokens=4, trace_id="p4")
    tracked = lens.track_token(trace, 4)
    top = tracked["layers"].index(N_LAYER - 1)
    assert tracked["ranks"][top][:3] == [None, None, None]
    assert isinstance(tracked["ranks"][top][3], int)
    assert tracked["logits"][top][0] is None


def test_a_trace_without_the_embedding_still_works(data_dir, tmp_path) -> None:
    """`inp_embd` is in the default capture set but a real trace of a real model
    can come back without it — the grid is that model's layers, not the ones we
    hoped for."""
    trace, _logits = _pruned_trace(tmp_path, n_tokens=3, trace_id="p5")
    grid = lens.compute_grid(trace, k=2)
    assert lens.EMBEDDING_LAYER not in grid.layers
    assert grid.layers == [0, 1, 2, 3]


# --- the dequantized-head cache ---------------------------------------------


def test_the_cache_is_written_where_it_is_looked_for(data_dir, tmp_path) -> None:
    """`np.save` appends `.npy` to a path that lacks it, so a temp file named
    `unembed.f16.part` lands as `unembed.f16.part.npy` and the rename after it
    finds nothing — a cache that is never installed, plus a gigabyte of litter
    per grid. Nothing about that fails loudly, which is why it is a test."""
    model_path = tmp_path / "m.gguf"
    head, _norm = _write_gguf(model_path)
    un = lens.load_unembedding(model_path)
    for _ in lens._weight_chunks(un, "shacache"):
        pass

    cached = lens.cache_path("shacache")
    assert cached.is_file(), sorted(p.name for p in cached.parent.iterdir())
    assert not list(cached.parent.glob("*.part*"))
    assert np.allclose(np.load(cached).astype(np.float32), head, atol=1e-2)


def test_a_cached_head_reproduces_the_uncached_one(data_dir, tmp_path) -> None:
    model_path = tmp_path / "m.gguf"
    _write_gguf(model_path)
    un = lens.load_unembedding(model_path)
    fresh = np.concatenate([b for _s, b in lens._weight_chunks(un, "shacache2")])
    assert lens.cache_path("shacache2").is_file()
    warm = np.concatenate([b for _s, b in lens._weight_chunks(un, "shacache2")])
    assert np.allclose(fresh, warm, atol=1e-2)


def test_a_cache_of_the_wrong_shape_is_discarded_not_used(data_dir, tmp_path) -> None:
    """Two models whose first megabyte hashes the same would otherwise share a
    cache, and a silently wrong output head produces a grid that reads fine."""
    model_path = tmp_path / "m.gguf"
    _write_gguf(model_path)
    un = lens.load_unembedding(model_path)
    cached = lens.cache_path("collide")
    cached.parent.mkdir(parents=True, exist_ok=True)
    with cached.open("wb") as handle:
        np.save(handle, np.zeros((N_VOCAB + 3, N_EMBD), dtype=np.float16))

    rebuilt = np.concatenate([b for _s, b in lens._weight_chunks(un, "collide")])
    assert rebuilt.shape == (N_VOCAB, N_EMBD)
    assert not np.allclose(rebuilt, 0)

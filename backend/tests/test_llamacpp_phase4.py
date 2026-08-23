"""Phase 4: the trace catalog, the model locus, the lens tool group, and findings.

Everything here is the *plumbing* around the lens rather than the lens itself —
which is exactly why it needs tests: each piece is a place where a fact can be
recorded in two places and drift. The catalog can disagree with the disk, a tool
can present an unverified reading as fact, and a note can be filed from a grid
that never verified.

The GGUF/trace fixtures are imported from `test_llamacpp_lens` rather than copied:
they build a real four-layer model and a trace whose stored logits genuinely are
that model's logits, and a second copy of that is a second thing to keep in step.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.modules.llamacpp import (
    findings,
    lens,
    lens_tools,
    locus,
    trace_catalog,
    traces,
)
from backend.tests.test_llamacpp_lens import _consistent_trace


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Point the whole data dir at a tmp dir — traces, `app.db`, everything.

    Defined here rather than imported from `test_llamacpp_lens`: importing a
    fixture works but reads as a redefinition (`F811`), and one line is cheaper
    than a file full of suppressions.
    """
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    return tmp_path


# --- the catalog ------------------------------------------------------------


def test_a_stored_trace_gets_a_catalog_row(data_dir, tmp_path) -> None:
    trace, *_ = _consistent_trace(tmp_path)
    trace_catalog.record(trace)

    rows = trace_catalog.rows()
    assert [r["traceId"] for r in rows] == [trace.trace_id]
    assert rows[0]["modelSha"] == trace.manifest["modelSha"]
    # The two JSON columns come back parsed: a caller must never have to know
    # they were stored as text.
    assert rows[0]["capture"] == []
    assert rows[0]["edits"] == []


def test_deleting_a_trace_drops_its_row(data_dir, tmp_path) -> None:
    """Hooked in `delete_trace`, so a *budget eviction* drops rows too — that is
    the path nothing else would tell the catalog about."""
    trace, *_ = _consistent_trace(tmp_path)
    trace_catalog.record(trace)
    assert trace_catalog.rows()

    traces.delete_trace(trace.trace_id)
    assert trace_catalog.rows() == []


def test_sync_reconciles_both_ways(data_dir, tmp_path) -> None:
    """Disk is the authority: a directory with no row is indexed, and a row whose
    directory vanished is dropped."""
    trace, *_ = _consistent_trace(tmp_path)
    # A trace that predates the table: on disk, never catalogued.
    assert trace_catalog.rows() == []
    assert trace_catalog.sync() == {"added": 1, "removed": 0}
    assert [r["traceId"] for r in trace_catalog.rows()] == [trace.trace_id]

    # A row whose directory was removed behind our back (a manual `rm -rf`).
    import shutil

    shutil.rmtree(trace.directory)
    assert trace_catalog.sync() == {"added": 0, "removed": 1}
    assert trace_catalog.rows() == []


def test_a_fork_is_findable_by_its_parent(data_dir, tmp_path) -> None:
    """The reason `derived_from` is indexed: a fork chain should be a query, not a
    walk of every manifest on disk."""
    parent, *_ = _consistent_trace(tmp_path, trace_id="parent")
    child, *_ = _consistent_trace(tmp_path, trace_id="child")
    child.manifest["derivedFrom"] = parent.trace_id
    child.manifest["edits"] = [{"position": 1, "fromId": 2, "toId": 7}]
    trace_catalog.record(parent)
    trace_catalog.record(child)

    forks = trace_catalog.rows(derived_from=parent.trace_id)
    assert [r["traceId"] for r in forks] == [child.trace_id]
    assert forks[0]["edits"] == [{"position": 1, "fromId": 2, "toId": 7}]


def test_recording_the_same_trace_twice_is_one_row(data_dir, tmp_path) -> None:
    """`run_trace` catalogues, and a re-sync may see the same trace again."""
    trace, *_ = _consistent_trace(tmp_path)
    trace_catalog.record(trace)
    trace_catalog.record(trace)
    assert len(trace_catalog.rows()) == 1


# --- the model locus --------------------------------------------------------


def test_the_locus_drops_nulls_and_stamps_a_source() -> None:
    """A `None` layer means "not stated", and storing it would make a follower
    that checks `is not None` reveal layer `null`."""
    got = locus.set_locus(
        {"traceId": "t1", "layer": 3, "position": None}, source="agent"
    )
    assert got == {"traceId": "t1", "layer": 3, "source": "agent"}
    assert locus.current_locus() == got


def test_the_locus_reaches_every_attached_browser() -> None:
    a = locus.lens_events.subscribe()
    b = locus.lens_events.subscribe()
    try:
        locus.set_locus({"layer": 7}, source="dash")
        assert a.get_nowait()["data"]["layer"] == 7
        assert b.get_nowait()["data"]["layer"] == 7
    finally:
        locus.lens_events.unsubscribe(a)
        locus.lens_events.unsubscribe(b)


# --- the lens tool group ----------------------------------------------------


def test_the_group_name_matches_the_tool_prefix() -> None:
    """The connectors rule: `_group_of` splits on the dot, and `AgentTool.group`
    does not name the group. A mismatch grants nothing, silently."""
    for tool in lens_tools.LENS_TOOLS:
        assert tool.group == tool.name.split(".", 1)[0]


def test_fork_and_save_are_gated_but_reads_are_not() -> None:
    gated = {t.name for t in lens_tools.LENS_TOOLS if t.side_effect}
    assert gated == {"lens.fork", "lens.save_finding"}


@pytest.mark.parametrize(
    "raw, expected",
    [
        ([1, 2], [1, 2]),
        # The shape that silently becomes `[]` if only lists are accepted — the
        # `load_tools` trap, where the tool succeeded against nothing.
        ("1,2", [1, 2]),
        ("[3, 4]", [3, 4]),
        (None, []),
        (5, [5]),
    ],
)
def test_ids_accepts_the_shapes_a_model_actually_sends(raw, expected) -> None:
    assert lens_tools._ids(raw) == expected


def test_an_unverified_reading_leads_with_a_warning() -> None:
    """The containment: a wrong norm convention yields confident wrong words, not
    an error, and a `verified` buried under the cells is one an agent skims past."""
    assert "warning" not in lens_tools._verdict({"verified": "true"})
    for state in ("false", "unavailable"):
        verdict = lens_tools._verdict({"verified": state, "verifyNote": "n"})
        assert verdict["verified"] == state
        assert "warning" in verdict


def test_grid_tool_reads_a_trace_as_words(data_dir, tmp_path) -> None:
    trace, *_ = _consistent_trace(tmp_path)
    got = asyncio.run(lens_tools._grid({"traceId": trace.trace_id}))

    assert got["verified"] == "true"
    assert got["traceId"] == trace.trace_id
    assert len(got["top"]) == len(got["layers"])
    # Top-1 only in the grid; the ranked list is `lens.cell`'s job.
    first = got["top"][0][0]
    assert set(first) == {"text", "p"}


def test_grid_tool_refuses_a_grid_too_big_to_carry(
    data_dir, tmp_path, monkeypatch
) -> None:
    """A tool answer is prose in a context window, not a screen that an eye skips."""
    trace, *_ = _consistent_trace(tmp_path)
    monkeypatch.setattr(lens_tools, "MAX_TOOL_CELLS", 1)
    got = asyncio.run(lens_tools._grid({"traceId": trace.trace_id}))
    assert "over the 1" in got["error"]
    # The dimensions come back, so the caller can narrow rather than guess.
    assert got["layers"] and got["positions"]


def test_cell_tool_returns_the_ranked_candidates(data_dir, tmp_path) -> None:
    trace, *_ = _consistent_trace(tmp_path)
    got = asyncio.run(
        lens_tools._cell({"traceId": trace.trace_id, "layer": 3, "position": 0, "k": 4})
    )
    assert len(got["candidates"]) == 4
    logits = [c["logit"] for c in got["candidates"]]
    assert logits == sorted(logits, reverse=True)
    # Named, never implied: this is a softmax over the shown candidates.
    assert "candidates only" in got["relProbNote"]


def test_track_token_resolves_text_to_a_vocabulary_id(data_dir, tmp_path) -> None:
    trace, *_ = _consistent_trace(tmp_path)
    got = asyncio.run(
        lens_tools._track_token({"traceId": trace.trace_id, "text": " Paris"})
    )
    assert got["text"] == " Paris"
    assert got["ranks"]


def test_a_leading_space_survives_to_the_lookup(data_dir, tmp_path) -> None:
    """Stripping the argument deletes the one character that makes " Paris" a
    token — and then the error advises adding the space that was just removed."""
    trace, *_ = _consistent_trace(tmp_path)
    spaced = asyncio.run(
        lens_tools._track_token({"traceId": trace.trace_id, "text": " Paris"})
    )
    bare = asyncio.run(
        lens_tools._track_token({"traceId": trace.trace_id, "text": "Paris"})
    )
    assert spaced["tokenId"] == 6
    assert "error" in bare


def test_track_token_says_so_when_the_text_is_not_one_token(data_dir, tmp_path) -> None:
    """Silently tracking nothing is the failure; the leading-space hint is the
    single most common cause."""
    trace, *_ = _consistent_trace(tmp_path)
    got = asyncio.run(
        lens_tools._track_token({"traceId": trace.trace_id, "text": "Nope"})
    )
    assert "not a single token" in got["error"]


def test_tools_report_a_missing_trace_rather_than_raising(data_dir) -> None:
    for handler in (lens_tools._grid, lens_tools._track_token):
        assert "no such trace" in asyncio.run(handler({"traceId": "nope"}))["error"]


# --- findings ---------------------------------------------------------------


def test_a_note_records_the_reading_and_its_provenance(data_dir, tmp_path) -> None:
    trace, *_ = _consistent_trace(tmp_path)
    grid = lens.compute_grid(trace, k=3)
    title, markdown = findings.render_note(
        trace, grid, note="the answer appears at layer 2"
    )

    assert "the answer appears at layer 2" in markdown
    # Provenance, so the note is reproducible after the trace is pruned.
    assert trace.trace_id in markdown
    assert trace.manifest["modelSha"] in markdown
    assert "**Verified**: true" in markdown
    # The embedding row is labelled, not rendered as the integer -1.
    assert "| embed |" in markdown


def test_a_fork_note_says_what_was_changed(data_dir, tmp_path) -> None:
    trace, *_ = _consistent_trace(tmp_path)
    trace.manifest["derivedFrom"] = "parent-id"
    trace.manifest["edits"] = [{"position": 1, "fromId": 2, "toId": 7}]
    _title, markdown = findings.render_note(trace, lens.compute_grid(trace, k=1))
    assert "**Forked from** `parent-id`" in markdown
    assert "position 1: 2 → 7" in markdown


def test_a_missing_cell_renders_as_a_gap_not_a_neighbour(data_dir, tmp_path) -> None:
    """The rows are ragged — llama.cpp prunes the graph, so the last block holds
    only the final position. A gap filled in from the cell beside it would be a
    reading of a token the model never computed there."""
    trace, *_ = _consistent_trace(tmp_path)
    grid = lens.compute_grid(trace, k=1)
    grid.cells[0][0] = None
    _title, markdown = findings.render_note(trace, grid)
    assert "| — |" in markdown or "| — " in markdown


def test_an_unverified_grid_is_refused_rather_than_filed(
    data_dir, tmp_path, monkeypatch
) -> None:
    """A caveat in a note body survives exactly as far as the first retrieval that
    quotes the numbers without it."""
    trace, *_ = _consistent_trace(tmp_path)
    real = lens.compute_grid

    def unverified(*args, **kwargs):
        grid = real(*args, **kwargs)
        grid.verified = "false"
        grid.verify_note = "the head disagreed"
        return grid

    monkeypatch.setattr(lens, "compute_grid", unverified)
    got = asyncio.run(findings.save_finding(trace.trace_id))
    assert got["verified"] == "false"
    assert "not a finding yet" in got["error"]
    assert "sourceId" not in got


def test_saving_a_finding_writes_a_library_note(
    data_dir, tmp_path, monkeypatch
) -> None:
    trace, *_ = _consistent_trace(tmp_path)
    created: dict[str, object] = {}
    ingested: dict[str, object] = {}

    from backend.modules.library import store as library_store

    def fake_create_source(**kw):
        created.update(kw)
        return {"id": "src-1", **kw}

    async def fake_ingest(source_id, req):
        ingested["source_id"] = source_id
        ingested["text"] = req.text

    monkeypatch.setattr(library_store, "create_source", fake_create_source)
    monkeypatch.setattr("backend.modules.library.ingest.ingest_source", fake_ingest)

    got = asyncio.run(findings.save_finding(trace.trace_id, note="a finding"))

    assert got["sourceId"] == "src-1"
    assert not got.get("error")
    assert created["type"] == "note"
    assert "lens" in created["tags"]
    assert ingested["source_id"] == "src-1"
    assert "a finding" in str(ingested["text"])


def test_a_note_that_failed_to_index_is_not_reported_as_saved(
    data_dir, tmp_path, monkeypatch
) -> None:
    """`ingest_source` records failure on the row instead of raising, so awaiting
    it proves the pipeline ran, not that it worked. A source left `failed` with
    zero chunks is one no search will ever return — saying "saved" for it is the
    silent success this surface refuses everywhere else. (Seen for real: an
    embedding-width mismatch against an existing collection.)"""
    trace, *_ = _consistent_trace(tmp_path)

    from backend.modules.library import store as library_store

    monkeypatch.setattr(
        library_store, "create_source", lambda **kw: {"id": "src-2", **kw}
    )
    monkeypatch.setattr(
        library_store,
        "get_source",
        lambda _id: {"status": "failed", "error": "384-dim vs 768", "chunk_count": 0},
    )

    async def fake_ingest(_source_id, _req):
        return None

    monkeypatch.setattr("backend.modules.library.ingest.ingest_source", fake_ingest)

    got = asyncio.run(findings.save_finding(trace.trace_id))
    assert "could not be indexed" in got["error"]
    assert "384-dim vs 768" in got["error"]

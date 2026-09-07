"""The dataset catalog, the file sources, and the build pipeline."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.modules.datasets import builder, registry, sources
from backend.modules.datasets.models import BuildStepModel, PipelineModel


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    """Point every path at a fresh dir. `registry.reset()` matters as much as the
    env var: the init flag is keyed by path, so a stale `True` would make these
    tests query tables that were never created here."""
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    registry.reset()
    yield
    registry.reset()


def _write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


# --- registry ----------------------------------------------------------------


def test_registering_the_same_definition_twice_is_one_dataset():
    """Keyed by fingerprint, not by name: two rows for one dataset would let a
    sweep point at either with nothing saying they were the same."""
    first = registry.register(name="A", source="hub", ref="x/y", split="train")
    second = registry.register(name="A renamed", source="hub", ref="x/y", split="train")
    assert first.id == second.id
    assert second.name == "A renamed"
    assert len(registry.list_datasets()) == 1


def test_a_different_split_is_a_different_dataset():
    train = registry.register(name="d", source="hub", ref="x/y", split="train")
    test = registry.register(name="d", source="hub", ref="x/y", split="test")
    assert train.id != test.id


def test_editing_the_column_map_changes_the_fingerprint():
    """A column map edit changes what a rerun trains on, so it must change the
    identity — otherwise two runs record the same fingerprint for two datasets."""
    saved = registry.register(
        name="d", source="hub", ref="x/y", fmt="alpaca", column_map={"output": "a"}
    )
    before = saved.fingerprint
    updated = registry.update(saved.id, column_map={"output": "b"})
    assert updated is not None
    assert updated.fingerprint != before
    assert updated.id == saved.id  # same row, new identity


def test_delete_and_missing_reads():
    saved = registry.register(name="d", source="hub", ref="x/y")
    assert registry.get(saved.id) is not None
    assert registry.delete(saved.id) is True
    assert registry.get(saved.id) is None
    assert registry.delete(saved.id) is False
    assert registry.get("") is None


# --- file sources ------------------------------------------------------------


def test_local_source_reads_jsonl_and_unions_columns():
    """Later rows may carry keys the first does not; a column list built from row
    one alone silently hides them from the mapper."""
    root = sources.data_root()
    _write(root / "d.jsonl", [{"a": 1}, {"a": 2, "b": 3}])
    columns, rows = sources.LocalSource().peek("d.jsonl", "", "train", 10)
    assert columns == ["a", "b"]
    assert len(rows) == 2


def test_local_source_refuses_to_escape_its_directory():
    """A `..` is how a peek route becomes an arbitrary-file-read route."""
    with pytest.raises(sources.SourceError, match="escapes"):
        sources.LocalSource().peek("../../secrets.json", "", "train", 5)


def test_local_source_skips_unparseable_lines_but_keeps_the_rest():
    root = sources.data_root()
    (root / "mixed.jsonl").write_text(
        '{"a": 1}\nnot json\n\n{"a": 2}\n', encoding="utf-8"
    )
    _, rows = sources.LocalSource().peek("mixed.jsonl", "", "train", 10)
    assert [r["a"] for r in rows] == [1, 2]


def test_a_file_reports_one_split_rather_than_none():
    """An empty list renders as 'this dataset has no splits', a different and
    wrong fact."""
    assert sources.LocalSource().splits("anything") == [
        {"config": "", "split": "train"}
    ]


def test_exports_source_spans_both_exporters(tmp_path):
    _write(tmp_path / "evals" / "exports" / "run-1.jsonl", [{"messages": []}])
    _write(tmp_path / "trajectories" / "exports" / "graded.jsonl", [{"messages": []}])
    found = sources.ExportsSource().search("", 10)
    origins = {r.meta["origin"] for r in found}
    assert origins == {"evals", "trajectories"}


def test_unknown_source_names_the_known_ones():
    with pytest.raises(sources.SourceError, match="hub"):
        sources.get_source("nope")


def test_plugin_sources_are_listed_but_do_not_shadow_builtins():
    from backend.sdk.registry import registry as plugin_registry

    class Fake:
        id = "hub"
        label = "Impostor"
        local = False

    plugin_registry.dataset_sources["hub"] = Fake()
    try:
        assert sources.get_source("hub").label == "Hugging Face Hub"
    finally:
        plugin_registry.dataset_sources.clear()


# --- builder -----------------------------------------------------------------


def _pipeline(*steps: BuildStepModel) -> PipelineModel:
    return PipelineModel(name="p", steps=list(steps))


def _load(ref: str) -> BuildStepModel:
    return BuildStepModel(op="load", params={"source": "local", "ref": ref})


def test_validate_catches_a_filter_on_a_column_nothing_produces():
    """The check that earns the validate pass its keep: this pipeline yields zero
    rows and blames the data."""
    pipeline = _pipeline(
        BuildStepModel(
            op="load", params={"source": "local", "ref": "d.jsonl", "columns": ["a"]}
        ),
        BuildStepModel(op="filter", params={"column": "answer", "test": "nonempty"}),
    )
    problems = builder.validate(pipeline)
    assert any("answer" in p for p in problems)


def test_validate_rejects_a_pipeline_that_does_not_start_from_data():
    problems = builder.validate(
        _pipeline(
            BuildStepModel(op="filter", params={"column": "a", "test": "nonempty"})
        )
    )
    assert any("load" in p for p in problems)


def test_validate_reports_an_invalid_regex_rather_than_raising_at_build_time():
    problems = builder.validate(
        _pipeline(
            _load("d.jsonl"),
            BuildStepModel(
                op="filter", params={"column": "a", "test": "matches", "value": "("}
            ),
        )
    )
    assert any("regex" in p for p in problems)


def test_preview_reports_per_step_counts_and_flags_the_step_that_emptied_it():
    """A filter that drops everything is invisible in the output and obvious in
    the counts — which is the whole reason the counts are reported."""
    _write(sources.data_root() / "d.jsonl", [{"a": "keep"}, {"a": "keep too"}])
    result = builder.preview(
        _pipeline(
            _load("d.jsonl"),
            BuildStepModel(
                op="filter",
                params={"column": "a", "test": "contains", "value": "absent"},
            ),
        )
    )
    assert result.problems == []
    assert result.rows == []
    emptied = [s for s in result.steps if s.get("warning")]
    assert len(emptied) == 1
    assert emptied[0]["op"] == "filter"


def test_a_template_naming_a_missing_column_is_an_error_not_empty_strings():
    _write(sources.data_root() / "d.jsonl", [{"a": "x"}])
    result = builder.preview(
        _pipeline(
            _load("d.jsonl"),
            BuildStepModel(
                op="template", params={"template": "{nope}", "output": "text"}
            ),
        )
    )
    assert any("nope" in p for p in result.problems)


def test_dedupe_normalizes_whitespace_and_case_by_default():
    _write(
        sources.data_root() / "d.jsonl",
        [{"a": "Hello  World"}, {"a": "hello world"}, {"a": "other"}],
    )
    result = builder.preview(
        _pipeline(_load("d.jsonl"), BuildStepModel(op="dedupe", params={"column": "a"}))
    )
    assert len(result.rows) == 2


def test_a_second_load_appends_rather_than_replacing():
    """Mixing your own eval failures into a public dataset is the motivating case
    for the whole builder."""
    _write(sources.data_root() / "a.jsonl", [{"x": 1}])
    _write(sources.data_root() / "b.jsonl", [{"x": 2}])
    result = builder.preview(_pipeline(_load("a.jsonl"), _load("b.jsonl")))
    assert sorted(r["x"] for r in result.rows) == [1, 2]


def test_split_tags_rows_instead_of_returning_two_streams():
    _write(sources.data_root() / "d.jsonl", [{"a": i} for i in range(10)])
    result = builder.preview(
        _pipeline(
            _load("d.jsonl"), BuildStepModel(op="split", params={"fraction": 0.2})
        ),
        limit=10,
    )
    assert {r["split"] for r in result.rows} == {"train", "test"}


def test_sample_is_seeded_so_a_preview_and_its_build_agree():
    _write(sources.data_root() / "d.jsonl", [{"a": i} for i in range(50)])
    step = BuildStepModel(op="sample", params={"count": 5, "seed": 7})
    first = builder.preview(_pipeline(_load("d.jsonl"), step), limit=5)
    second = builder.preview(_pipeline(_load("d.jsonl"), step), limit=5)
    assert [r["a"] for r in first.rows] == [r["a"] for r in second.rows]


def test_a_build_writes_jsonl_and_registers_it_with_a_detected_format():
    _write(
        sources.data_root() / "d.jsonl",
        [{"instruction": f"q{i}", "output": f"a{i}"} for i in range(4)],
    )
    build_id = builder.start_build(_pipeline(_load("d.jsonl")), name="built", save=True)
    _wait_for(build_id)
    record = builder.build_status(build_id)[0]
    assert record["state"] == "finished", record.get("error")
    assert record["rows"] == 4
    saved = registry.get(record["datasetId"])
    assert saved is not None
    assert saved.format == "alpaca"
    assert saved.rows == 4


def test_a_failing_build_is_reported_not_raised_into_the_thread():
    build_id = builder.start_build(_pipeline(_load("missing.jsonl")), name="x")
    _wait_for(build_id)
    record = builder.build_status(build_id)[0]
    assert record["state"] == "failed"
    assert "missing.jsonl" in record["error"]


def _wait_for(build_id: str, timeout: float = 20.0) -> None:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = builder.build_status(build_id)
        if found and found[0]["state"] in ("finished", "failed"):
            return
        time.sleep(0.05)
    raise AssertionError(f"build {build_id} never finished")


# --- the local source spans training projects ---------------------------------


@pytest.fixture
def _projects_root(tmp_path, monkeypatch):
    """A projects root with one project holding a fetched file in `data/`."""
    from backend.modules.training import projects

    root = tmp_path / "projects"
    monkeypatch.setattr(projects, "projects_root", lambda: root)
    project = projects.create_project("Kaggle Run", [], "3.12")
    _write(Path(project.root) / "data" / "train.jsonl", [{"a": 1}, {"a": 2}])
    return project


def test_local_source_peeks_a_file_fetched_into_a_training_project(_projects_root):
    """A provider fetch lands under `training.projectsRoot`, nowhere near the data
    dir — so a `local` source that only saw the data dir made "fetch it, then peek
    at it" an instruction that could not be followed."""
    ref = f"project:{_projects_root.id}/train.jsonl"
    columns, rows = sources.LocalSource().peek(ref, "", "train", 10)
    assert columns == ["a"]
    assert len(rows) == 2
    assert sources.LocalSource().locate(ref).endswith("train.jsonl")


def test_local_search_lists_both_roots_and_labels_the_project_one(_projects_root):
    _write(sources.data_root() / "train.jsonl", [{"a": 1}])
    found = {ref.id: ref for ref in sources.LocalSource().search("train", 10)}
    assert "train.jsonl" in found  # the data dir keeps bare refs
    project_ref = found[f"project:{_projects_root.id}/train.jsonl"]
    # Two identically-named files are indistinguishable by title alone.
    assert "Kaggle Run" in project_ref.description


def test_a_project_ref_cannot_escape_that_project(_projects_root):
    with pytest.raises(sources.SourceError, match="escapes"):
        sources.LocalSource().peek(
            f"project:{_projects_root.id}/../../secrets.json", "", "train", 5
        )


def test_a_broken_projects_root_degrades_local_rather_than_emptying_it(monkeypatch):
    """`local` must still serve the data dir when the training module cannot
    answer; an exception here would take out the data dir listing too."""
    from backend.modules.training import projects

    def _boom():
        raise RuntimeError("no projects root")

    monkeypatch.setattr(projects, "list_projects", _boom)
    _write(sources.data_root() / "d.jsonl", [{"a": 1}])
    assert [ref.id for ref in sources.LocalSource().search("", 10)] == ["d.jsonl"]

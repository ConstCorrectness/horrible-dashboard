"""Model lineage, and the training→localtrack mirror.

Both exist to make the fine-tune loop legible, and both are **best-effort by
construction**: a conversion that produced a GGUF must not be reported as failed
because a bookkeeping row could not be written, and a training run must not die
because a chart could not be updated. Those two properties are the ones most likely
to be broken by a later "tidy-up", so they are pinned first.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from backend.modules.training import lineage


@pytest.fixture(autouse=True)
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    # Keyed by path, so a fresh data dir must not inherit the previous test's flag
    # and query tables that were never created in the new file.
    lineage._initialized.clear()
    yield tmp_path
    lineage._initialized.clear()


def test_records_and_reads_back_a_conversion():
    lineage.record(
        "/models/trained/proj-ckpt-1200-f16.gguf",
        project_id="proj",
        checkpoint="ckpt-1200",
        base_model="meta-llama/Llama-3.2-3B",
        out_type="f16",
        recipe={"dataset": "tatsu-lab/alpaca"},
    )
    got = lineage.get("/models/trained/proj-ckpt-1200-f16.gguf")
    assert got is not None
    assert got["baseModel"] == "meta-llama/Llama-3.2-3B"
    assert got["projectId"] == "proj"
    assert got["isAdapter"] is False
    assert got["recipe"]["dataset"] == "tatsu-lab/alpaca"


def test_an_unrecorded_file_is_none_not_an_error():
    """Every GGUF the user downloaded has no lineage, and that is normal.

    A caller has to be able to render "no provenance" — inventing one, or raising,
    would make the common case the broken one.
    """
    assert lineage.get("/models/somebody-elses.gguf") is None


def test_reconverting_the_same_file_replaces_its_row():
    """Converting a checkpoint twice at the same output type overwrites the same
    file on disk, so it must overwrite the same row rather than conflict."""
    path = "/models/trained/proj-ckpt-f16.gguf"
    lineage.record(path, project_id="proj", checkpoint="ckpt", base_model="base-a")
    lineage.record(path, project_id="proj", checkpoint="ckpt", base_model="base-b")

    assert lineage.by_path()[path]["baseModel"] == "base-b"
    assert len(lineage.by_path()) == 1


def test_by_path_keys_every_row(tmp_path: Path):
    """The evals target picker labels a whole catalog from one query."""
    lineage.record("/a.gguf", project_id="p1", checkpoint="c")
    lineage.record("/b.gguf", project_id="p2", checkpoint="c")
    assert set(lineage.by_path()) == {"/a.gguf", "/b.gguf"}


def test_forget_drops_a_row():
    lineage.record("/a.gguf", project_id="p", checkpoint="c")
    lineage.forget("/a.gguf")
    assert lineage.get("/a.gguf") is None


def test_a_broken_database_never_raises(monkeypatch: pytest.MonkeyPatch):
    """The load-bearing property: bookkeeping cannot fail a conversion.

    `run_conversion` calls `record` after the converter has already written the
    GGUF. If this raised, a successful conversion would surface to the user as a
    failure and the file would be left unexplained on disk.
    """

    def boom(*_a: Any, **_k: Any):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(lineage, "_conn", boom)

    lineage.record("/a.gguf", project_id="p", checkpoint="c")  # must not raise
    assert lineage.get("/a.gguf") is None  # and reads degrade to "unknown"
    assert lineage.by_path() == {}
    lineage.forget("/a.gguf")


# --- the shared localtrack mirror -------------------------------------------


def test_mirror_is_inert_without_a_project():
    """An empty project name means "do not track", not "track into a blank"."""
    from backend.modules.localtrack.mirror import RunMirror

    mirror = RunMirror("", name="run")
    assert mirror.active is False
    mirror.log(0, {"train/loss": 1.0})  # no-op, no raise
    mirror.finish()


def test_mirror_survives_a_broken_localtrack(monkeypatch: pytest.MonkeyPatch):
    """A tracking failure must never reach the training loop or the sweep."""
    from backend.modules.localtrack import store as lt
    from backend.modules.localtrack.mirror import RunMirror

    def boom(*_a: Any, **_k: Any):
        raise RuntimeError("localtrack is down")

    monkeypatch.setattr(lt, "create_project", boom)

    mirror = RunMirror("proj", name="run")
    assert mirror.active is False
    assert mirror.error  # but it knows, so a caller could warn once
    mirror.log(0, {"train/loss": 1.0})
    mirror.finish()


def test_mirror_writes_metrics_under_its_own_run():
    from backend.modules.localtrack import store as lt
    from backend.modules.localtrack.mirror import RunMirror

    mirror = RunMirror("proj", name="sft-1", tags=["training"])
    assert mirror.active

    mirror.log(0, {"train/loss": 2.0})
    mirror.log(1, {"train/loss": 1.5})
    mirror.finish(summary={"final": 1.5})

    series = lt.query_metrics([mirror.run_id], ["train/loss"], max_points=100)
    assert series and series[0].values == [2.0, 1.5]
    run = lt.get_run(mirror.run_id)
    assert run is not None and run.status == "finished"


# --- the metrics chokepoint --------------------------------------------------


def test_every_metric_point_is_stored_even_when_the_wire_coalesces():
    """The reason persistence sits before the coalescing gate.

    `record_event` drops intermediate points on the wire at 20/s because a chart
    cannot draw faster than that. A *store* has no such excuse — a loss curve
    missing 90% of its points is not the curve — so this fires 50 events back to
    back (far above the rate limit) and requires all 50 in the database.
    """
    from backend.modules.localtrack import store as lt
    from backend.modules.training import metrics

    metrics.reset()
    for step in range(50):
        metrics.record_event(
            "metrics",
            {
                "runId": "run-a",
                "projectId": "proj",
                "step": step,
                "values": {"train/loss": 1.0 / (step + 1)},
            },
        )

    mirror = metrics._mirrors["run-a"]
    series = lt.query_metrics([mirror.run_id], ["train/loss"], max_points=1000)
    assert series and len(series[0].values) == 50, "the store must keep every point"
    metrics.reset()


def test_metrics_land_under_the_projects_own_localtrack_project():
    """Not one shared bucket.

    The evals sweep hardcodes `localtrack_project='evals'` so every sweep from
    every suite piles into one project. Training deliberately does not copy that:
    the localtrack project is the training project id, so each keeps its own runs.
    """
    from backend.modules.localtrack import store as lt
    from backend.modules.training import metrics

    metrics.reset()
    metrics.record_event(
        "metrics",
        {"runId": "r1", "projectId": "alpaca-sft", "step": 0, "values": {"train/loss": 1.0}},
    )
    run = lt.get_run(metrics._mirrors["r1"].run_id)
    assert run is not None and run.project_id == "alpaca-sft"
    metrics.reset()


def test_a_new_run_closes_the_previous_one_for_that_project():
    """The only end-of-run signal available here.

    The helper emits no "finished" event and kernel lifecycle lives in shared
    notebook_core, so a superseding run is what marks its predecessor done. A run
    whose process merely died stays `running` until the next one — deliberate, and
    better than guessing from a timeout.
    """
    from backend.modules.localtrack import store as lt
    from backend.modules.training import metrics

    metrics.reset()
    base = {"projectId": "proj", "step": 0, "values": {"train/loss": 1.0}}
    metrics.record_event("metrics", {**base, "runId": "first"})
    first_id = metrics._mirrors["first"].run_id
    metrics.record_event("metrics", {**base, "runId": "second"})

    assert lt.get_run(first_id).status == "finished"
    assert lt.get_run(metrics._mirrors["second"].run_id).status == "running"
    metrics.reset()


def test_a_metric_event_with_no_values_stores_nothing():
    from backend.modules.training import metrics

    metrics.reset()
    metrics.record_event("metrics", {"runId": "r1", "projectId": "p", "step": 0})
    metrics.record_event("metrics", {"runId": "r1", "projectId": "p", "values": {}})
    assert "r1" not in metrics._mirrors
    metrics.reset()


def test_a_storage_failure_never_reaches_the_training_run(monkeypatch: pytest.MonkeyPatch):
    """The load-bearing property on this side.

    `record_event` runs on the kernel's output pump thread. Raising here would take
    down the stream that carries the training run's stdout — losing the run's
    visible output because a chart could not be filed.
    """
    from backend.modules.training import metrics

    metrics.reset()

    def boom(*_a: Any, **_k: Any):
        raise RuntimeError("store is gone")

    monkeypatch.setattr(metrics, "_mirror_for", boom)
    metrics.record_event(
        "metrics",
        {"runId": "r1", "projectId": "p", "step": 0, "values": {"train/loss": 1.0}},
    )
    # And the in-memory buffer still got its point, so the live chart is unaffected.
    assert len(metrics.backfill("r1")) == 1
    metrics.reset()

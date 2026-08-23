"""The LocalTrack live channel, and the panel-layout store.

Two behaviours here are easy to get subtly wrong and impossible to notice once
wrong, so both are pinned:

- **Coalescing must union its keys, not overwrite them.** A tight training loop
  emits faster than 20/s, so bursts collapse. If the coalesced payload simply
  replaces the previous one, every key but the last in a burst is silently never
  announced — and a chart for one of those keys sits empty while its neighbour
  updates, which reads as a broken panel rather than a dropped event.
- **A layout of `[]` is not the same as no layout.** `None` means "never saved
  one" and yields the defaults; `[]` means the user removed every panel. Collapse
  them and a deliberately cleared workspace springs back to four charts on every
  reload.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from backend.modules.localtrack import store, stream
from backend.modules.localtrack.models import MetricLogItem


@pytest.fixture(autouse=True)
def isolated_localtrack_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Reset BEFORE as well as after. `stream`'s coalescing state is module-global
    # and includes live `threading.Timer`s, and other suites (test_localtrack_
    # agent_eval) ingest metrics without ever resetting it — so a timer they
    # started can fire *inside* a test here and publish a foreign run's event into
    # whatever this test is capturing. That produced a genuinely intermittent
    # failure: green in isolation, red in roughly one full-suite run out of two.
    stream.reset()
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    store.init_db()
    yield tmp_path
    stream.reset()


@pytest.fixture
def published(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    """Capture what would go on the wire, without needing a socket or a loop."""
    seen: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        stream, "publish", lambda event, data: seen.append((event, data))
    )
    return seen


def events_for(published: list[tuple[str, dict]], run_id: str, event: str) -> list[dict]:
    """Only the events for the run under test.

    Filtered rather than asserting over the whole capture: a stray publish from
    another suite's leaked coalescing timer would otherwise break an equality
    assertion here, which is a flake in this test rather than a defect in the code
    it covers. The fixture cancels inherited timers too — this is the second layer,
    because one of them races and the other cannot.
    """
    return [d for e, d in published if e == event and d.get("runId") == run_id]


# --- the channel ------------------------------------------------------------


def test_ingest_announces_the_run_and_its_keys(published):
    store.create_run("r1", project_id="default", name="r1")
    store.ingest_metrics(
        [
            MetricLogItem(
                run_id="r1", step=0, metrics={"train/loss": 1.0, "eval/acc": 0.5}
            )
        ]
    )

    assert events_for(published, "r1", "metrics") == [
        {"runId": "r1", "keys": ["eval/acc", "train/loss"]}
    ]


def test_run_lifecycle_is_announced(published):
    store.create_run("r1", project_id="p", name="first")
    store.update_run("r1", status="finished")
    store.delete_run("r1")

    assert [e for e, d in published if d.get("runId") == "r1"] == [
        "run_created",
        "run_updated",
        "run_deleted",
    ]
    # The project rides along on create/update so a pane watching one project can
    # ignore another project's traffic without a round trip to find out whose it is.
    assert published[0][1]["projectId"] == "p"
    assert published[1][1]["status"] == "finished"


def test_a_write_that_inserts_nothing_announces_nothing(published):
    """An empty batch is not an event.

    Note what is NOT tested here: a non-numeric value. `MetricLogItem.metrics` is
    `dict[str, float | int]`, so Pydantic rejects one before `ingest_metrics` ever
    sees it, and the store's own `float()` guard is unreachable from the route. A
    test asserting on it would be asserting on dead code.
    """
    store.create_run("r1", project_id="default", name="r1")
    published.clear()

    store.ingest_metrics([])
    store.ingest_metrics([MetricLogItem(run_id="r1", step=0, metrics={})])

    assert events_for(published, "r1", "metrics") == []


def test_coalescing_unions_keys_rather_than_overwriting(monkeypatch):
    """The bug this exists to prevent: a burst dropping every key but the last."""
    sent: list[dict] = []
    monkeypatch.setattr(stream, "publish", lambda event, data: sent.append(data))

    # First call goes straight out and starts the window.
    stream.publish_metrics("r1", ["train/loss"])
    # These three land inside it and must merge, not replace.
    stream.publish_metrics("r1", ["train/grad_norm"])
    stream.publish_metrics("r1", ["eval/accuracy"])
    stream.publish_metrics("r1", ["train/loss"])

    # Wait out the coalescing window so the tail flush fires.
    deadline = time.monotonic() + 2.0
    while len(sent) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)

    assert len(sent) == 2, (
        f"expected an immediate send plus one coalesced flush, got {sent}"
    )
    assert sent[0] == {"runId": "r1", "keys": ["train/loss"]}
    # Every key from the burst survived.
    assert sent[1]["keys"] == ["eval/accuracy", "train/grad_norm", "train/loss"]


def test_separate_runs_do_not_share_a_coalescing_window(monkeypatch):
    sent: list[dict] = []
    monkeypatch.setattr(stream, "publish", lambda event, data: sent.append(data))

    stream.publish_metrics("r1", ["train/loss"])
    stream.publish_metrics("r2", ["train/loss"])

    # Two different runs, so neither throttles the other — both go out at once.
    assert [d["runId"] for d in sent] == ["r1", "r2"]


def test_publish_without_a_loop_is_silent_not_fatal():
    """Tests, CLI use and scripts write to the store with no app running.

    A tracking module that raises because nothing is listening would take a
    training run down with it.
    """
    stream.reset()  # no loop captured
    stream.publish("metrics", {"runId": "r1", "keys": []})  # must not raise


# --- panel layout -----------------------------------------------------------


def test_layout_absent_is_none_and_empty_is_empty():
    store.create_project("p", "p")

    assert store.get_layout("p") is None, (
        "never saved must be None, so the caller uses defaults"
    )

    store.save_layout("p", [])
    assert store.get_layout("p") == [], (
        "a deliberately cleared workspace must stay cleared"
    )

    store.save_layout("p", [{"id": "p-1", "metricKey": "train/loss"}])
    assert store.get_layout("p") == [{"id": "p-1", "metricKey": "train/loss"}]


def test_layout_save_replaces_rather_than_accumulating():
    store.create_project("p", "p")
    store.save_layout("p", [{"id": "a"}])
    store.save_layout("p", [{"id": "b"}])
    assert store.get_layout("p") == [{"id": "b"}]

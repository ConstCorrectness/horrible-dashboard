"""Sentinel protocol: line parsing, chunk-boundary splitting, and the metrics
ring buffer / coalescing layer."""

import json
import time

from backend.modules.training import metrics
from backend.modules.training.sentinel import SENTINEL, LineSplitter, parse_line


def _line(payload: dict) -> str:
    return SENTINEL + json.dumps(payload) + "\n"


def test_parse_line() -> None:
    assert parse_line("plain text") is None
    assert parse_line(SENTINEL + '{"type": "metric"}') == {"type": "metric"}
    assert parse_line(SENTINEL + "not json") == {}  # sentinel but junk → strip
    assert parse_line(SENTINEL + "[1,2]") == {}  # non-dict → strip


def test_splitter_passthrough_and_events() -> None:
    s = LineSplitter()
    text, events = s.feed("before\n" + _line({"type": "metric", "step": 1}) + "after\n")
    assert text == "before\nafter\n"
    assert events == [{"type": "metric", "step": 1}]


def test_splitter_handles_chunk_boundaries_inside_sentinel() -> None:
    s = LineSplitter()
    whole = _line({"type": "metric", "values": {"loss": 1.0}})
    collected_text = ""
    collected_events = []
    # Feed one character at a time — worst-case chunking.
    for ch in "x\n" + whole + "y":
        text, events = s.feed(ch)
        collected_text += text
        collected_events += events
    collected_text += s.flush()
    assert collected_text == "x\ny"
    assert collected_events == [{"type": "metric", "values": {"loss": 1.0}}]


def test_splitter_keeps_partial_plain_lines_live() -> None:
    s = LineSplitter()
    text, events = s.feed("progress 42%")  # no newline, not sentinel-like
    assert text == "progress 42%" and events == []


def test_splitter_carriage_return_updates_pass_through() -> None:
    s = LineSplitter()
    text, events = s.feed("\r50%|█████     |\n")
    assert "50%" in text and events == []


def test_helper_emission_format(capsys) -> None:
    import importlib.util
    import pathlib

    helper = pathlib.Path("backend/modules/training/helper/horrible_train/__init__.py")
    spec = importlib.util.spec_from_file_location("horrible_train_test", helper)
    ht = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ht)

    run_id = ht.run("baseline")
    ht.log(step=3, loss=0.25, acc=0.9)
    captured = capsys.readouterr().out
    lines = [line for line in captured.splitlines() if line]
    assert all(line.startswith(SENTINEL) for line in lines)
    run_evt = json.loads(lines[0][len(SENTINEL) :])
    metric_evt = json.loads(lines[1][len(SENTINEL) :])
    assert run_evt == {
        "type": "run",
        "runId": run_id,
        "name": "baseline",
        "ts": run_evt["ts"],
    }
    assert metric_evt["type"] == "metric"
    assert metric_evt["runId"] == run_id
    assert metric_evt["step"] == 3
    assert metric_evt["values"] == {"loss": 0.25, "acc": 0.9}


def test_metrics_buffer_and_backfill(monkeypatch) -> None:
    metrics.reset()
    sent: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        metrics, "broadcast_threadsafe", lambda ev, data: sent.append((ev, data))
    )
    for step in range(50):
        metrics.record_event(
            "metrics",
            {"runId": "r1", "step": step, "values": {"loss": 1.0 - step / 100}},
        )
    # Every point buffered, wire coalesced far below 50.
    points = metrics.backfill("r1")
    assert len(points) == 50
    assert points[0]["step"] == 0 and points[-1]["step"] == 49
    assert 0 < len(sent) < 50
    assert "r1" in metrics.known_runs()
    # Coalesced tail flushes via the timer.
    time.sleep(0.15)
    assert sent[-1][1]["step"] == 49
    metrics.reset()


def test_metrics_non_metric_events_pass_straight_through(monkeypatch) -> None:
    metrics.reset()
    sent: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        metrics, "broadcast_threadsafe", lambda ev, data: sent.append((ev, data))
    )
    metrics.record_event(
        "frame", {"projectId": "p", "dataUrl": "data:image/png;base64,x"}
    )
    metrics.record_event("model_graph", {"projectId": "p", "graph": {}})
    assert [e for e, _ in sent] == ["frame", "model_graph"]
    metrics.reset()


# --- the architecture cache ---------------------------------------------------
#
# `model_graph` is emitted **once**, at the top of `trainer.train()`, and the
# Architecture strip starts closed. Forwarding it and keeping nothing meant the
# normal sequence was: training starts, the graph goes to nobody, the user opens
# the strip, and it says "no model yet" for the rest of the run.


def test_the_last_architecture_is_kept_per_project(monkeypatch) -> None:
    metrics.reset()
    monkeypatch.setattr(metrics, "broadcast_threadsafe", lambda ev, data: None)
    metrics.record_event(
        "model_graph", {"projectId": "a", "graph": {"kind": "modules"}}
    )
    metrics.record_event("model_stats", {"projectId": "a", "stats": {"x": {}}})
    metrics.record_event("model_graph", {"projectId": "b", "graph": {"kind": "fx"}})

    cached = metrics.graph_backfill("a")
    assert cached["model_graph"]["graph"] == {"kind": "modules"}
    assert cached["model_stats"]["stats"] == {"x": {}}
    # A second project does not clobber the first: two notebooks side by side is
    # exactly when a strip showing the wrong model is hardest to notice.
    assert metrics.graph_backfill("b")["model_graph"]["graph"] == {"kind": "fx"}
    assert "model_stats" not in metrics.graph_backfill("b")
    metrics.reset()


def test_a_later_graph_replaces_the_earlier_one(monkeypatch) -> None:
    # Unlike a metric curve there is only ever a latest — a re-run publishes a new
    # architecture and the old one is not history, it is wrong.
    metrics.reset()
    monkeypatch.setattr(metrics, "broadcast_threadsafe", lambda ev, data: None)
    metrics.record_event(
        "model_graph", {"projectId": "a", "graph": {"kind": "modules"}}
    )
    metrics.record_event("model_graph", {"projectId": "a", "graph": {"kind": "fx"}})
    assert metrics.graph_backfill("a")["model_graph"]["graph"] == {"kind": "fx"}
    metrics.reset()


def test_an_unwatched_project_backfills_nothing(monkeypatch) -> None:
    metrics.reset()
    monkeypatch.setattr(metrics, "broadcast_threadsafe", lambda ev, data: None)
    assert metrics.graph_backfill("never-trained") == {}
    # And `reset` clears it, or a test suite would leak graphs between cases.
    metrics.record_event("model_graph", {"projectId": "a", "graph": {}})
    metrics.reset()
    assert metrics.graph_backfill("a") == {}


# --- the terminal status ------------------------------------------------------
#
# A run had no end. `_mirror_for` closed the previous run when a *new* one started,
# which for the last run of a session is never — so a twelve-point sweep showed
# twelve live runs forever. `ht.finish()` and the script runner's process exit are
# the two real signals; the supersede branch is now only a backstop.


def test_the_helper_emits_a_finish_event_that_the_sentinel_maps():
    from backend.modules.training.sentinel import EVENT_NAMES, parse_line

    event = parse_line(
        '@@HORRIBLE@@{"type": "finish", "runId": "a", "status": "failed"}'
    )
    assert event["type"] == "finish"
    assert EVENT_NAMES["finish"] == "run_finished"


def test_finishing_a_run_twice_is_a_no_op_the_second_time(monkeypatch):
    """A generated recipe emits `ht.finish()` from `on_train_end` AND the script
    runner closes the run on process exit, so two calls for one run is the common
    case. The second must not overwrite the real status with the process's."""
    from backend.modules.training import metrics

    metrics.reset()
    closed: list[tuple[str, str]] = []

    class FakeMirror:
        run_id = "lt-1"

        def finish(self, status="finished", summary=None):
            closed.append((self.run_id, status))

    metrics._mirrors["r1"] = FakeMirror()
    assert metrics.finish_run("r1", "finished") is True
    assert metrics.finish_run("r1", "crashed") is False
    assert closed == [("lt-1", "finished")]
    metrics.reset()


def test_an_unknown_status_is_coerced_rather_than_written_through(monkeypatch):
    """localtrack renders an unknown status as no status at all."""
    from backend.modules.training import metrics

    metrics.reset()
    seen: list[str] = []

    class FakeMirror:
        run_id = "lt-1"

        def finish(self, status="finished", summary=None):
            seen.append(status)

    metrics._mirrors["r1"] = FakeMirror()
    metrics.finish_run("r1", "exploded")
    assert seen == ["failed"]
    metrics.reset()


def test_a_run_started_event_records_the_name_for_the_config_lookup():
    """A script mints its own run id, so the sweep that launched it can only agree
    with it on the *name*."""
    from backend.modules.training import metrics

    metrics.reset()
    metrics.declare_run_config("proj", "lr=0.001", {"learning_rate": 0.001})
    metrics.record_event("run_started", {"runId": "abc", "name": "lr=0.001"})
    assert metrics._names["abc"] == "lr=0.001"
    metrics.reset()


def test_a_notebook_run_is_tracked_with_the_recipe_it_ran(monkeypatch):
    """A sweep point declares what it varies; a run started from the notebook
    declared nothing and was tracked as `{source, projectId}` — so the runs you get
    by pressing Run were the ones `compare_runs` could say nothing about."""
    from backend.modules.training import metrics

    metrics.reset()
    monkeypatch.setattr(
        metrics,
        "_recipe_config",
        lambda project_id: {"baseModel": "Qwen/Qwen3-0.6B", "learning_rate": 0.0002},
    )
    captured: dict = {}

    class FakeMirror:
        def __init__(self, project, name="", config=None, tags=()):
            captured["config"] = dict(config or {})
            captured["tags"] = list(tags)

        def log(self, *a, **kw):
            return None

    monkeypatch.setattr(metrics, "RunMirror", FakeMirror)
    metrics._mirror_for("r9", "proj")

    assert captured["config"]["baseModel"] == "Qwen/Qwen3-0.6B"
    assert captured["config"]["learning_rate"] == 0.0002
    # Not a sweep point: the tag means "a sweep declared this", and the fallback
    # must not claim it.
    assert captured["tags"] == ["training"]
    metrics.reset()


def test_a_declared_config_still_marks_a_sweep_point(monkeypatch):
    from backend.modules.training import metrics

    metrics.reset()
    monkeypatch.setattr(metrics, "_recipe_config", lambda project_id: {"lr": "recipe"})
    captured: dict = {}

    class FakeMirror:
        def __init__(self, project, name="", config=None, tags=()):
            captured["config"] = dict(config or {})
            captured["tags"] = list(tags)

        def log(self, *a, **kw):
            return None

    monkeypatch.setattr(metrics, "RunMirror", FakeMirror)
    metrics.declare_run_config("proj", "point-1", {"lr": 0.001})
    metrics._names["r10"] = "point-1"
    metrics._mirror_for("r10", "proj")

    assert captured["config"]["lr"] == 0.001  # the declaration wins
    assert "sweep" in captured["tags"]
    metrics.reset()


def test_watch_graph_replays_the_cache_to_one_connection(monkeypatch) -> None:
    """The seam the Architecture strip depends on: a pane that opens after
    training began asks for what it missed, and gets it under the **live** event
    names so it needs one code path rather than two."""
    import asyncio

    from backend.modules.training.kernels import TrainingKernelManager

    metrics.reset()
    monkeypatch.setattr(metrics, "broadcast_threadsafe", lambda ev, data: None)
    metrics.record_event("model_graph", {"projectId": "p", "graph": {"kind": "fx"}})
    metrics.record_event("model_stats", {"projectId": "p", "stats": {"a": {}}})

    class _Conn:
        def __init__(self) -> None:
            self.sent: list[dict] = []

        async def send_json(self, payload: dict) -> None:
            self.sent.append(payload)

    conn = _Conn()
    handled = asyncio.run(
        TrainingKernelManager()._handle_extra(conn, "watch_graph", {"projectId": "p"})
    )
    assert handled is True
    assert [m["event"] for m in conn.sent] == ["model_graph", "model_stats"]
    assert conn.sent[0]["data"]["graph"] == {"kind": "fx"}

    # A project nothing has published for sends nothing at all — the pane's empty
    # state is already the right rendering of "nothing has trained here".
    quiet = _Conn()
    asyncio.run(
        TrainingKernelManager()._handle_extra(quiet, "watch_graph", {"projectId": "q"})
    )
    assert quiet.sent == []
    metrics.reset()

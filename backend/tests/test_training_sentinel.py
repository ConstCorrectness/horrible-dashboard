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

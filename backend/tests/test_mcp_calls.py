"""MCP call summaries and turn-stamped wire messages.

The design rests on one property of the MCP SDK that no stub can prove: a request is
*sent* from the task that made the call, while responses are *received* on the
session's own long-lived task. Capture and turn stamping both depend on it, so the
integration tests here run against the real stdio fixture server and the real
`ClientSession`, not a mock.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from backend.modules.mcp import calls
from backend.modules.mcp import config as cfg
from backend.modules.mcp import transcript
from backend.modules.mcp.client import McpManager

FIXTURE_SERVER = str(Path(__file__).parent / "mcp_fixture_server.py")


@pytest.fixture()
def db():
    calls._initialized.clear()
    calls.init_calls_db()
    return calls


def _row(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = dict(
        server_id="s",
        tool="t",
        started_at=time.time(),
        duration_ms=100,
        ok=True,
    )
    body.update(over)
    return body


# ------------------------------------------------------------------ aggregation


def test_percentile_is_nearest_rank():
    """Every reported latency is one a call actually took. An interpolated p95 of
    [10, 3000] would be a number no call ever had."""
    assert calls.percentile([10, 3000], 95) == 3000
    assert calls.percentile([10, 20, 30, 40], 50) == 20
    assert calls.percentile([], 50) is None


def test_tool_and_transport_errors_are_counted_apart(db):
    """They look identical to the model and mean opposite things to a human."""
    db.record(**_row())
    db.record(**_row(ok=False, error_kind="tool", error="bad arg"))
    db.record(**_row(ok=False, error_kind="transport", error="timed out"))
    db.record(**_row(ok=False, error_kind="transport", error="not connected"))

    server = db.activity()[0]
    assert server["calls"] == 4
    assert server["tool_errors"] == 1
    assert server["transport_errors"] == 2
    assert server["tool_error_rate"] == pytest.approx(0.25)
    assert server["transport_error_rate"] == pytest.approx(0.5)


def test_a_successful_row_never_carries_an_error(db):
    db.record(**_row(ok=True, error_kind="tool", error="stale"))
    row = db.list_calls()[0]
    assert row["error_kind"] is None
    assert row["error"] is None


def test_activity_breaks_down_by_tool_busiest_first(db):
    for _ in range(3):
        db.record(**_row(tool="search"))
    db.record(**_row(tool="read"))
    tools = db.activity()[0]["tools"]
    assert [t["tool"] for t in tools] == ["search", "read"]


def test_activity_respects_its_window(db):
    db.record(**_row(started_at=time.time() - 30 * 86400))
    db.record(**_row())
    assert db.activity(since=time.time() - 86400)[0]["calls"] == 1


def test_cost_is_of_the_runs_that_used_the_server_and_says_how_many_were_priced(db):
    """A turn's bill cannot be split across its tools. The figure is the total over
    runs that touched the server, with the priced count beside it so "$0.40" can be
    read as "across 1 of 2 runs"."""
    from backend.modules.trajectories import store as traj

    traj._initialized.clear()
    traj.init_trajectories_db()
    traj.create_dataset("d", "D")
    priced = traj.start_run("d", turn_id="t-priced")
    traj.finish_run(priced, cost_usd=0.4)
    unpriced = traj.start_run("d", turn_id="t-unpriced")
    traj.finish_run(unpriced)

    db.record(**_row(turn_id="t-priced"))
    db.record(**_row(turn_id="t-unpriced"))

    runs = db.activity()[0]["runs"]
    assert runs == {
        "runs_seen": 2,
        "runs_priced": 1,
        "cost_usd_of_runs_using_server": pytest.approx(0.4),
    }


def test_no_priced_runs_is_no_figure_not_zero(db):
    db.record(**_row())
    assert db.activity()[0]["runs"]["cost_usd_of_runs_using_server"] is None


def test_record_never_raises(db, monkeypatch):
    """This runs after a tool has already answered. A failed dashboard write must not
    turn a working call into a failed one."""

    def broken():
        raise RuntimeError("disk full")

    monkeypatch.setattr(calls, "get_db_conn", broken)
    assert calls.record(**_row()) is None


def test_prune_keeps_the_newest(db, monkeypatch):
    monkeypatch.setattr(calls, "MAX_ROWS", 2)
    now = time.time()
    for i in range(5):
        db.record(**_row(tool=f"t{i}", started_at=now + i))
    db.prune()
    assert {r["tool"] for r in db.list_calls()} == {"t3", "t4"}


# ------------------------------------------------------------- transcript stamps


class _Root:
    def __init__(self, ident: Any, method: str = "tools/call") -> None:
        self.id = ident
        self.method = method

    def model_dump_json(self, **_: Any) -> str:
        return "{}"


class _Msg:
    def __init__(self, ident: Any, method: str = "tools/call") -> None:
        self.message = type("M", (), {"root": _Root(ident, method)})()


def test_an_inbound_message_inherits_its_requests_turn_not_the_ambient_one():
    """The receive loop runs in a task started at connect time, where the turn
    contextvar is stale. Stamping from it would attribute a response to the wrong turn;
    stamping by JSON-RPC id cannot."""
    from backend.modules.telemetry import turn

    ring = transcript.Transcript()

    token = turn.enter("turn-A")
    try:
        ring.record("out", _Msg(7))
    finally:
        turn.leave(token)

    # The response arrives while a *different* turn is ambient.
    token = turn.enter("turn-STALE")
    try:
        ring.record("in", _Msg(7, method=""))
    finally:
        turn.leave(token)

    out, inbound = ring.by_ids(["7"])
    assert out.turn_id == "turn-A"
    assert inbound.turn_id == "turn-A"


def test_a_response_inherits_the_turn_of_its_own_connections_request():
    """JSON-RPC ids restart on reconnect, so id 1 exists once per connection. A
    response must take the stamp of the request on *its* connection — not simply the
    most recent request with that id, which after a reconnect belongs to another one."""
    from backend.modules.telemetry import turn

    ring = transcript.Transcript()
    for name, conn in (("turn-on-A", "A"), ("turn-on-B", "B")):
        token = turn.enter(name)
        try:
            ring.record("out", _Msg(1), conn)
        finally:
            turn.leave(token)
    # A late response on connection A arrives after B's request with the same id.
    ring.record("in", _Msg(1, method=""), "A")
    assert ring._messages[-1].turn_id == "turn-on-A"


def test_the_transcript_route_keeps_the_turn_fields():
    """`response_model` strips undeclared fields silently. Declared in one place only,
    the stamp would exist in memory and never reach the browser."""
    from fastapi.testclient import TestClient

    from backend.app import app
    from backend.modules.telemetry import turn

    ring = transcript.for_server("route-check")
    ring.clear()
    token = turn.enter("turn-R")
    try:
        ring.record("out", _Msg(3))
    finally:
        turn.leave(token)

    body = (
        TestClient(app).get("/api/mcp/servers/route-check/transcript?rpc_ids=3").json()
    )
    assert body["messages"][0]["turn_id"] == "turn-R"


# ------------------------------------------------------ against the real server


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    calls._initialized.clear()
    return tmp_path


@pytest.mark.timeout(120)
def test_real_calls_are_summarized_with_their_turn_and_wire(data_dir: Path) -> None:
    cfg.save_server(
        {
            "id": "fixture",
            "name": "Fixture",
            "transport": "stdio",
            "command": sys.executable,
            "args": [FIXTURE_SERVER],
        }
    )
    manager = McpManager()

    async def scenario() -> dict[str, Any]:
        from backend.modules.telemetry import turn

        runtime = await manager.start_server("fixture")
        assert runtime is not None and runtime.state == "ready", (
            runtime and runtime.error
        )
        session = manager.get("fixture")
        assert session is not None

        token = turn.enter("turn-real")
        turn.mark_round("turn-real", 2)
        try:
            await session.call_tool("peek", {"key": "k"})
            await session.call_tool("boom", {})
            # Two calls at once on one session: each must see only its own request id.
            # Scanning the ring for "ids sent since I started" would cross them over.
            await asyncio.gather(
                session.call_tool("peek", {"key": "a"}),
                session.call_tool("poke", {"key": "b", "value": "c"}),
            )
        finally:
            turn.leave(token)

        # Reconnect, then call again: the new connection restarts JSON-RPC ids, so this
        # call reuses an id one of the calls above already had.
        await manager.stop_server("fixture")
        await manager.start_server("fixture")
        session = manager.get("fixture")
        assert session is not None
        token = turn.enter("turn-real")
        turn.mark_round("turn-real", 2)
        try:
            await session.call_tool("peek", {"key": "after-reconnect"})
        finally:
            turn.leave(token)

        wire = {
            c["id"]: [
                m.public()
                for m in session.transcript.by_ids(c["rpc_ids"], session=c["session"])
            ]
            for c in calls.list_calls(turn_id="turn-real")
        }
        by_id_alone = {
            c["id"]: len(session.transcript.by_ids(c["rpc_ids"]))
            for c in calls.list_calls(turn_id="turn-real")
        }
        await manager.stop_all()
        # After the server is gone, a call is a transport failure — and still recorded.
        after = await session.call_tool("peek", {"key": "late"})
        return {"wire": wire, "by_id_alone": by_id_alone, "after": after}

    out = asyncio.run(scenario())
    rows = calls.list_calls(turn_id="turn-real")

    assert [r["tool"] for r in rows[:2]] == ["peek", "boom"]
    assert rows[0]["ok"] is True and rows[0]["error_kind"] is None
    # The server answered, and said the call failed: a tool error, not a transport one.
    assert rows[1]["ok"] is False and rows[1]["error_kind"] == "tool"
    assert all(r["round"] == 2 for r in rows)

    concurrent = rows[2:4]
    ids = [set(r["rpc_ids"]) for r in concurrent]
    assert all(len(i) == 1 for i in ids)
    assert ids[0].isdisjoint(ids[1])

    # Two connections, and the second really did reuse an id from the first.
    first, reconnected = rows[:4], rows[4]
    assert len({r["session"] for r in first}) == 1
    assert reconnected["session"] and reconnected["session"] != first[0]["session"]
    reused = [r for r in first if r["rpc_ids"] == reconnected["rpc_ids"]]
    assert reused, "the reconnect should have reused an earlier request id"
    # By id alone that exchange is ambiguous: it comes back with the other connection's.
    assert out["by_id_alone"][reused[0]["id"]] == 4

    # Each call's own wire, scoped by connection: exactly its request and its response,
    # both stamped with the turn — the response by id-match, since it arrived on the
    # session's receive task.
    for row in rows:
        messages = out["wire"][row["id"]]
        assert [m["direction"] for m in messages] == ["out", "in"]
        assert all(m["session"] == row["session"] for m in messages)
        assert all(m["turn_id"] == "turn-real" for m in messages)

    assert "not connected" in out["after"]["error"]
    late = [r for r in calls.list_calls(server_id="fixture") if r["turn_id"] is None]
    assert late and late[0]["error_kind"] == "transport"


# ------------------------------------------------------------ the step <-> call join


def test_run_steps_pair_with_their_calls_by_round_name_and_ordinal(db):
    """The same tool twice in one round must pair first-with-first, a call in another
    round must not be claimed by this one, and a tool whose raw name has characters a
    provider rejects must still match the sanitized name the agent used."""
    from fastapi.testclient import TestClient

    from backend.app import app
    from backend.modules.trajectories import store as traj
    from backend.modules.trajectories.models import StepWrite

    traj._initialized.clear()
    traj.init_trajectories_db()
    traj.create_dataset("d", "D")
    run_id = traj.start_run("d", turn_id="tj")
    for name, rnd in [
        ("open_pane", 0),  # not an MCP step: never paired
        ("mcp-fs.read", 0),
        ("mcp-fs.read", 0),
        ("mcp-fs.read", 1),
        ("mcp-fs.list_dir", 1),  # the server's own name is `list.dir`
        ("mcp-fs.read", 2),  # no call recorded for it: must stay unpaired
    ]:
        traj.append_step(run_id, StepWrite(kind="action", name=name, round=rnd))

    now = time.time()
    db.record(
        **_row(
            server_id="fs",
            tool="read",
            turn_id="tj",
            round_no=0,
            started_at=now,
            duration_ms=1,
        )
    )
    db.record(
        **_row(
            server_id="fs",
            tool="read",
            turn_id="tj",
            round_no=0,
            started_at=now + 1,
            duration_ms=2,
        )
    )
    db.record(
        **_row(
            server_id="fs",
            tool="read",
            turn_id="tj",
            round_no=1,
            started_at=now + 2,
            duration_ms=3,
        )
    )
    db.record(
        **_row(
            server_id="fs",
            tool="list.dir",
            turn_id="tj",
            round_no=1,
            started_at=now + 3,
            duration_ms=4,
        )
    )

    body = TestClient(app).get(f"/api/trajectories/runs/{run_id}/mcp").json()
    paired = {seq: call["duration_ms"] for seq, call in body["calls"].items()}
    assert paired == {"1": 1, "2": 2, "3": 3, "4": 4}
    assert body["joinable"] is True


def test_a_run_with_no_turn_has_no_mcp_join(db):
    from fastapi.testclient import TestClient

    from backend.app import app
    from backend.modules.trajectories import store as traj

    traj._initialized.clear()
    traj.init_trajectories_db()
    traj.create_dataset("d", "D")
    run_id = traj.start_run("d")
    body = TestClient(app).get(f"/api/trajectories/runs/{run_id}/mcp").json()
    assert body == {"calls": {}, "joinable": False}


def test_a_steps_wire_says_when_it_has_aged_out(db):
    """The ring is small and in memory. An old call's exchange is gone, and the route
    must say so rather than return an empty list that reads as "nothing was sent"."""
    from fastapi.testclient import TestClient

    from backend.app import app
    from backend.modules.telemetry import turn
    from backend.modules.trajectories import store as traj
    from backend.modules.trajectories.models import StepWrite

    traj._initialized.clear()
    traj.init_trajectories_db()
    traj.create_dataset("d", "D")
    run_id = traj.start_run("d", turn_id="tw")
    traj.append_step(run_id, StepWrite(kind="action", name="mcp-wiresrv.read", round=0))
    traj.append_step(run_id, StepWrite(kind="action", name="mcp-wiresrv.read", round=0))

    ring = transcript.for_server("wiresrv")
    ring.clear()
    token = turn.enter("tw")
    try:
        ring.record("out", _Msg(41), "conn-1")
    finally:
        turn.leave(token)
    ring.record("in", _Msg(41, method=""), "conn-1")

    now = time.time()
    db.record(
        **_row(
            server_id="wiresrv",
            tool="read",
            turn_id="tw",
            round_no=0,
            started_at=now,
            rpc_ids=["41"],
            session="conn-1",
        )
    )
    # The second call's messages were never kept.
    db.record(
        **_row(
            server_id="wiresrv",
            tool="read",
            turn_id="tw",
            round_no=0,
            started_at=now + 1,
            rpc_ids=["99"],
            session="conn-1",
        )
    )

    client = TestClient(app)
    live = client.get(f"/api/trajectories/runs/{run_id}/mcp/0/wire").json()
    assert live["available"] is True
    assert [m["direction"] for m in live["messages"]] == ["out", "in"]

    gone = client.get(f"/api/trajectories/runs/{run_id}/mcp/1/wire").json()
    assert gone["available"] is False
    assert gone["call"]["rpc_ids"] == ["99"]

    assert client.get(f"/api/trajectories/runs/{run_id}/mcp/7/wire").status_code == 404


def test_a_reused_id_from_another_connection_is_not_part_of_a_calls_wire(db):
    """JSON-RPC ids restart per connection and the ring survives reconnects, so id 4
    names one exchange per connection. Found by reconnecting the fixture server in the
    running app: an old call's wire came back with later connections' id-4 exchanges
    appended.

    A time window was the first fix and it was not enough — a quick reconnect put two
    connections' id-4 calls one second apart. So both exchanges here happen at the same
    instant, and only the connection tells them apart.
    """
    from fastapi.testclient import TestClient

    from backend.app import app
    from backend.modules.trajectories import store as traj
    from backend.modules.trajectories.models import StepWrite

    ring = transcript.for_server("reuse")
    ring.clear()
    for conn in ("conn-old", "conn-new"):
        ring.record("out", _Msg(4), conn)
        ring.record("in", _Msg(4, method=""), conn)

    traj._initialized.clear()
    traj.init_trajectories_db()
    traj.create_dataset("d", "D")
    run_id = traj.start_run("d", turn_id="tr")
    traj.append_step(run_id, StepWrite(kind="action", name="mcp-reuse.peek", round=0))
    db.record(
        **_row(
            server_id="reuse",
            tool="peek",
            turn_id="tr",
            round_no=0,
            rpc_ids=["4"],
            session="conn-old",
        )
    )

    wire = TestClient(app).get(f"/api/trajectories/runs/{run_id}/mcp/0/wire").json()
    assert len(wire["messages"]) == 2
    assert {m["session"] for m in wire["messages"]} == {"conn-old"}


def test_a_call_with_no_recorded_connection_does_not_guess_its_wire(db):
    """Rows written before the connection was recorded carry ids but no session. With
    ids alone the exchange is ambiguous, so the route reports it unavailable rather
    than return what might be another connection's."""
    from fastapi.testclient import TestClient

    from backend.app import app
    from backend.modules.trajectories import store as traj
    from backend.modules.trajectories.models import StepWrite

    ring = transcript.for_server("legacy")
    ring.clear()
    ring.record("out", _Msg(4), "some-conn")
    ring.record("in", _Msg(4, method=""), "some-conn")

    traj._initialized.clear()
    traj.init_trajectories_db()
    traj.create_dataset("d", "D")
    run_id = traj.start_run("d", turn_id="tl")
    traj.append_step(run_id, StepWrite(kind="action", name="mcp-legacy.peek", round=0))
    db.record(
        **_row(server_id="legacy", tool="peek", turn_id="tl", round_no=0, rpc_ids=["4"])
    )

    wire = TestClient(app).get(f"/api/trajectories/runs/{run_id}/mcp/0/wire").json()
    assert wire["available"] is False


def test_an_existing_calls_table_gains_the_session_column(db):
    """`CREATE TABLE IF NOT EXISTS` leaves an older `mcp_calls` alone, so without the
    explicit ALTER every insert on an upgraded install would fail on an unknown column
    — and `record` swallows that, so the dashboard would simply stop filling."""
    import sqlite3

    from backend.modules.database.app_db import ensure_app_db_dir

    with sqlite3.connect(str(ensure_app_db_dir())) as conn:
        conn.execute("DROP TABLE mcp_calls")
        conn.execute(
            "CREATE TABLE mcp_calls (id TEXT PRIMARY KEY, server_id TEXT NOT NULL,"
            " tool TEXT NOT NULL, turn_id TEXT, round INTEGER, started_at REAL NOT NULL,"
            " duration_ms INTEGER, ok INTEGER NOT NULL, error_kind TEXT, error TEXT,"
            " request_bytes INTEGER, response_bytes INTEGER, content_blocks INTEGER,"
            " rpc_ids TEXT NOT NULL DEFAULT '')"
        )
    calls._initialized.clear()
    assert calls.record(**_row(rpc_ids=["1"], session="c")) is not None
    assert calls.list_calls()[0]["session"] == "c"

"""Tests for the Python REPL kernel and session manager.

The kernel is exercised directly; the manager's relay is driven through a fake
conn (the agent's `pending` future map) and a fake browser that answers tool_calls,
so the dash.* round-trip is deterministic and needs no real socket.
"""

import asyncio
import io
from typing import Any

from backend.modules.repl.kernel import ReplKernel
from backend.modules.repl.manager import ReplManager


# --- kernel ----------------------------------------------------------------


def _run(code: str, kernel: ReplKernel) -> tuple[Any, str, str]:
    out, err = io.StringIO(), io.StringIO()
    result = kernel.exec_cell(code, out, err)
    return result, out.getvalue(), err.getvalue()


def test_expression_echoes_repr() -> None:
    result, _, _ = _run("1 + 1", ReplKernel())
    assert result.ok and result.value_repr == "2"


def test_statement_has_no_repr() -> None:
    result, _, _ = _run("x = 5", ReplKernel())
    assert result.ok and result.value_repr is None


def test_state_persists_across_cells() -> None:
    kernel = ReplKernel()
    _run("x = 5", kernel)
    result, _, _ = _run("x * 2", kernel)
    assert result.value_repr == "10"


def test_stdout_is_captured() -> None:
    result, out, _ = _run("print('hi')", ReplKernel())
    assert result.ok and out == "hi\n"


def test_error_is_formatted_not_raised() -> None:
    result, _, _ = _run("1 / 0", ReplKernel())
    assert not result.ok
    assert "ZeroDivisionError" in (result.error or "")
    # The kernel's own frame is stripped; the user frame shows as <repl>.
    assert "exec_cell" not in (result.error or "")


def test_syntax_error_is_reported() -> None:
    result, _, _ = _run("def (", ReplKernel())
    assert not result.ok and "SyntaxError" in (result.error or "")


def test_empty_cell_is_ok() -> None:
    result, _, _ = _run("   \n  ", ReplKernel())
    assert result.ok and result.value_repr is None


# --- manager relay ---------------------------------------------------------


class FakeConn:
    """Mirrors WsConnection's `send_json` + `pending` future map."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.pending: dict[str, asyncio.Future[dict[str, Any]]] = {}

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)

    def events(self) -> list[tuple[str, dict[str, Any]]]:
        return [(s["event"], s["data"]) for s in self.sent]


async def _wait_for(conn: FakeConn, event: str, timeout: float = 2.0) -> dict[str, Any]:
    async def poll() -> dict[str, Any]:
        while True:
            for ev, data in conn.events():
                if ev == event:
                    return data
            await asyncio.sleep(0.01)

    return await asyncio.wait_for(poll(), timeout)


def test_start_emits_started_and_tracks_session() -> None:
    async def go() -> None:
        conn = FakeConn()
        mgr = ReplManager(conn)
        await mgr.handle({"event": "start", "data": {"id": "r1"}})
        started = [d for ev, d in conn.events() if ev == "started"]
        assert started and started[0]["id"] == "r1"
        assert "r1" in mgr.sessions

    asyncio.run(go())


def test_exec_streams_stdout_and_result() -> None:
    async def go() -> None:
        conn = FakeConn()
        mgr = ReplManager(conn)
        await mgr.handle({"event": "start", "data": {"id": "r1"}})
        await mgr.handle({"event": "exec", "data": {"id": "r1", "code": "print(2+2)"}})
        result = await _wait_for(conn, "result")
        assert result["ok"] and result["id"] == "r1"
        # stdout streams live, possibly in several chunks — join them.
        out = "".join(d["data"] for ev, d in conn.events() if ev == "stdout")
        assert out == "4\n"

    asyncio.run(go())


def test_dash_call_relays_tool_and_blocks_for_result() -> None:
    async def go() -> None:
        conn = FakeConn()
        mgr = ReplManager(conn)
        await mgr.handle({"event": "start", "data": {"id": "r1"}})

        # Fake browser: answer the tool_call as soon as it appears.
        async def answer() -> None:
            tc = await _wait_for(conn, "tool_call")
            await mgr.handle(
                {
                    "event": "tool_result",
                    "data": {
                        "id": "r1",
                        "callId": tc["callId"],
                        "ok": True,
                        "result": {"opened": tc["args"]["id"]},
                    },
                }
            )

        answerer = asyncio.create_task(answer())
        await mgr.handle(
            {
                "event": "exec",
                "data": {"id": "r1", "code": "dash.panes.open('settings.main')"},
            }
        )
        await answerer
        tool_call = await _wait_for(conn, "tool_call")
        assert tool_call["name"] == "open_pane"
        assert tool_call["args"] == {"id": "settings.main"}
        result = await _wait_for(conn, "result")
        # The cell echoed the dict the fake browser returned.
        assert result["ok"] and "settings.main" in (result["repr"] or "")

    asyncio.run(go())


def test_exec_unknown_session_errors() -> None:
    async def go() -> None:
        conn = FakeConn()
        mgr = ReplManager(conn)
        await mgr.handle({"event": "exec", "data": {"id": "ghost", "code": "1"}})
        err = await _wait_for(conn, "error")  # exec runs detached
        assert err["id"] == "ghost"

    asyncio.run(go())


def test_close_and_close_all_drop_sessions() -> None:
    async def go() -> None:
        conn = FakeConn()
        mgr = ReplManager(conn)
        await mgr.handle({"event": "start", "data": {"id": "r1"}})
        await mgr.handle({"event": "start", "data": {"id": "r2"}})
        await mgr.handle({"event": "close", "data": {"id": "r1"}})
        assert "r1" not in mgr.sessions and "r2" in mgr.sessions
        await mgr.close_all()
        assert mgr.sessions == {}

    asyncio.run(go())


def test_duplicate_start_errors() -> None:
    async def go() -> None:
        conn = FakeConn()
        mgr = ReplManager(conn)
        await mgr.handle({"event": "start", "data": {"id": "r1"}})
        await mgr.handle({"event": "start", "data": {"id": "r1"}})
        errors = [d for ev, d in conn.events() if ev == "error"]
        assert errors and errors[0]["id"] == "r1"

    asyncio.run(go())

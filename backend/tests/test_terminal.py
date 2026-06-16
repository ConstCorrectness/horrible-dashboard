"""Tests for the terminal session manager (D1).

The PTY lifecycle is driven through a fake backend so the tests are deterministic
and platform-independent; the real ptyprocess/pywinpty adapter is exercised at
runtime. The fake mirrors the read/write/setwinsize/terminate surface the manager
uses, with a blocking `read` (run in a thread by the pump) unblocked by EOF.
"""

import asyncio
import queue
from typing import Any

from backend.modules.terminal.manager import TerminalManager


class FakePty:
    def __init__(self) -> None:
        self._q: queue.Queue[str | None] = queue.Queue()
        self.writes: list[str] = []
        self.size: tuple[int, int] | None = None
        self.terminated = False

    # control hook used by tests
    def feed(self, text: str) -> None:
        self._q.put(text)

    # PtyProcess surface
    def read(self, size: int = 1024) -> str:
        item = self._q.get()
        if item is None:
            raise EOFError
        return item

    def write(self, data: str) -> int:
        self.writes.append(data)
        return len(data)

    def setwinsize(self, rows: int, cols: int) -> None:
        self.size = (rows, cols)

    def isalive(self) -> bool:
        return not self.terminated

    def terminate(self, force: bool = False) -> None:
        self.terminated = True
        self._q.put(None)  # unblock read with EOF


class FakeConn:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

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


def _manager() -> tuple[TerminalManager, FakeConn, list[FakePty]]:
    conn = FakeConn()
    spawned: list[FakePty] = []

    def spawn(argv, **kwargs):  # noqa: ANN001, ANN003
        pty = FakePty()
        spawned.append(pty)
        return pty

    return TerminalManager(conn, spawn=spawn), conn, spawned


def test_start_emits_started_and_tracks_session() -> None:
    async def go() -> None:
        mgr, conn, _ = _manager()
        await mgr.handle(
            {"event": "start", "data": {"id": "t1", "cols": 80, "rows": 24}}
        )
        assert ("started", {"id": "t1"}) in conn.events()
        assert "t1" in mgr.sessions
        await mgr.close_all()

    asyncio.run(go())


def test_output_is_pumped_to_connection() -> None:
    async def go() -> None:
        mgr, conn, spawned = _manager()
        await mgr.handle({"event": "start", "data": {"id": "t1"}})
        spawned[0].feed("hello\r\n")
        out = await _wait_for(conn, "output")
        assert out == {"id": "t1", "data": "hello\r\n"}
        await mgr.close_all()

    asyncio.run(go())


def test_input_is_forwarded_to_pty() -> None:
    async def go() -> None:
        mgr, _, spawned = _manager()
        await mgr.handle({"event": "start", "data": {"id": "t1"}})
        await mgr.handle({"event": "input", "data": {"id": "t1", "data": "ls\n"}})
        assert spawned[0].writes == ["ls\n"]
        await mgr.close_all()

    asyncio.run(go())


def test_resize_is_forwarded_to_pty() -> None:
    async def go() -> None:
        mgr, _, spawned = _manager()
        await mgr.handle({"event": "start", "data": {"id": "t1"}})
        await mgr.handle(
            {"event": "resize", "data": {"id": "t1", "rows": 30, "cols": 100}}
        )
        assert spawned[0].size == (30, 100)
        await mgr.close_all()

    asyncio.run(go())


def test_kill_terminates_and_emits_exit() -> None:
    async def go() -> None:
        mgr, conn, spawned = _manager()
        await mgr.handle({"event": "start", "data": {"id": "t1"}})
        await mgr.handle({"event": "kill", "data": {"id": "t1"}})
        await _wait_for(conn, "exit")
        assert spawned[0].terminated
        assert "t1" not in mgr.sessions

    asyncio.run(go())


def test_duplicate_id_errors() -> None:
    async def go() -> None:
        mgr, conn, _ = _manager()
        await mgr.handle({"event": "start", "data": {"id": "t1"}})
        await mgr.handle({"event": "start", "data": {"id": "t1"}})
        errors = [d for ev, d in conn.events() if ev == "error"]
        assert errors and errors[0]["id"] == "t1"
        await mgr.close_all()

    asyncio.run(go())


def test_close_all_terminates_every_session() -> None:
    async def go() -> None:
        mgr, _, spawned = _manager()
        await mgr.handle({"event": "start", "data": {"id": "t1"}})
        await mgr.handle({"event": "start", "data": {"id": "t2"}})
        await mgr.close_all()
        assert all(p.terminated for p in spawned)
        assert mgr.sessions == {}

    asyncio.run(go())


def test_input_to_unknown_session_is_ignored() -> None:
    async def go() -> None:
        mgr, _, _ = _manager()
        # No exception for input to a session that doesn't exist.
        await mgr.handle({"event": "input", "data": {"id": "ghost", "data": "x"}})

    asyncio.run(go())

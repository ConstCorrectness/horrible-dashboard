"""Comm (ipywidgets) routing unit tests — no real widget kernel needed: synthetic
iopub messages are pushed through the session's router and the browser-facing events
are captured on a fake subscriber."""

import asyncio
from pathlib import Path
from typing import Any

from backend.notebook_core import notebooks
from backend.notebook_core.config import SessionConfig
from backend.notebook_core.session import KernelSession, _b64_buffers


class FakeConn:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)

    def events(self, event: str) -> list[dict[str, Any]]:
        return [s["data"] for s in self.sent if s.get("event") == event]


def _session(tmp_path: Path, loop: asyncio.AbstractEventLoop) -> KernelSession:
    path = tmp_path / "n.ipynb"
    notebooks.new_notebook(path, [{"cell_type": "code", "source": "x = 1"}])
    config = SessionConfig(
        key="nb:n.ipynb",
        python_executable="python",
        cwd=str(tmp_path),
        notebook_abs_path=path,
        rel_path="n.ipynb",
    )
    return KernelSession(config, loop)


def test_b64_buffers_roundtrip() -> None:
    assert _b64_buffers([b"hi", memoryview(b"yo")]) == ["aGk=", "eW8="]
    assert _b64_buffers(None) == []


def test_comm_open_msg_close_are_forwarded(tmp_path) -> None:
    async def go() -> None:
        sess = _session(tmp_path, asyncio.get_running_loop())
        conn = FakeConn()
        sess.subscribers.add(conn)

        # comm_open (a widget model appears) — via the full iopub router.
        sess._route_iopub(
            {
                "msg_type": "comm_open",
                "content": {
                    "comm_id": "c1",
                    "target_name": "jupyter.widget",
                    "data": {"state": {"value": 3}},
                },
                "parent_header": {},
            }
        )
        # comm_msg (a state update) with a binary buffer.
        sess._route_iopub(
            {
                "msg_type": "comm_msg",
                "content": {"comm_id": "c1", "data": {"method": "update"}},
                "parent_header": {},
                "buffers": [memoryview(b"abc")],
            }
        )
        sess._route_iopub(
            {
                "msg_type": "comm_close",
                "content": {"comm_id": "c1"},
                "parent_header": {},
            }
        )
        await asyncio.sleep(0.05)  # let the fanout coroutines run

        assert (
            conn.events("comm_open")
            and conn.events("comm_open")[0]["comm"]["comm_id"] == "c1"
        )
        assert conn.events("comm_msg")[0]["buffers"] == ["YWJj"]  # base64("abc")
        assert conn.events("comm_close")
        assert "c1" not in sess.comms  # tracked open then dropped on close

    asyncio.run(go())


def test_comms_snapshot_folds_updates_for_reattach(tmp_path) -> None:
    """A pane attaching to a running kernel gets each open comm's *current* state
    (comm_open state + folded update patches), so widgets rehydrate live."""

    async def go() -> None:
        sess = _session(tmp_path, asyncio.get_running_loop())
        sess._route_iopub(
            {
                "msg_type": "comm_open",
                "content": {
                    "comm_id": "c1",
                    "target_name": "jupyter.widget",
                    "data": {"state": {"value": 3, "max": 10}},
                },
                "parent_header": {},
            }
        )
        # A later state update from the kernel must be reflected in the snapshot.
        sess._route_iopub(
            {
                "msg_type": "comm_msg",
                "content": {
                    "comm_id": "c1",
                    "data": {"method": "update", "state": {"value": 7}},
                },
                "parent_header": {},
            }
        )
        snap = sess.comms_snapshot()
        assert snap == [
            {
                "comm_id": "c1",
                "target_name": "jupyter.widget",
                "state": {"value": 7, "max": 10},
            }
        ]
        # Closed comms drop out of the snapshot.
        sess._route_iopub(
            {
                "msg_type": "comm_close",
                "content": {"comm_id": "c1"},
                "parent_header": {},
            }
        )
        assert sess.comms_snapshot() == []

    asyncio.run(go())


def test_send_comm_queues_for_worker(tmp_path) -> None:
    async def go() -> None:
        sess = _session(tmp_path, asyncio.get_running_loop())
        sess.send_comm("c1", {"method": "update", "state": {"value": 5}}, [b"x"])
        item = sess.comm_q.get_nowait()
        assert item.comm_id == "c1"
        assert item.data["state"]["value"] == 5
        assert item.buffers == [b"x"]

    asyncio.run(go())

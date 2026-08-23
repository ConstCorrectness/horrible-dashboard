"""Kernel-session integration: spawns a REAL ipykernel (from the backend dev env's
python) and exercises execute/stream/result/error/interrupt/restart plus .ipynb
persistence and sentinel stripping. This test gates the Windows interrupt story —
if `interrupt` stops delivering KeyboardInterrupt under the SelectorEventLoop,
this fails before any user does."""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from backend.modules.training import kernels as kernels_mod
from backend.modules.training import notebooks, projects, stream
from backend.modules.training.kernels import TrainingKernelManager
from backend.modules.training.models import EnvironmentRefModel
from backend.modules.training.providers.base import code_cell


class FakeConn:
    """Mirrors WsConnection's send_json (collects every training event)."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)

    def events(self, event: str) -> list[dict[str, Any]]:
        return [s["data"] for s in self.sent if s.get("event") == event]


async def _handle(mgr, conn, message, timeout: float = 60.0):
    """`mgr.handle`, bounded.

    Every *observation* here has a deadline (`_wait`), but the thirteen `mgr.handle`
    calls and the `shutdown_all` in the `finally` did not — so a kernel that stopped
    responding parked the coroutine in the event loop forever, and pytest-timeout
    killed the **whole session** with a stack dump 180 seconds later. A bounded await
    turns that into one named failure, which is the difference between a report and a
    mystery. Kept now that the underlying race is fixed (see `_shutdown`): this is
    what would name the next one.
    """
    event = message.get("event", "?")
    try:
        return await asyncio.wait_for(mgr.handle(conn, message), timeout)
    except TimeoutError:
        raise AssertionError(
            f"kernel manager never finished handling {event!r}"
        ) from None


async def _shutdown(mgr, timeout: float = 30.0) -> None:
    """Tear the manager down without letting teardown hang the run.

    This test used to fail intermittently (~1 in 6 in isolation) in two shapes: a
    hang and a segfault. Both came from one bug — `stop_channels()` closed the zmq
    sockets the pump threads were still reading, which is undefined behaviour in
    libzmq. `session._stop_pumps` now joins the pumps first. The deadline stays
    because a teardown that silently never returns is the worst thing this suite can
    do to the rest of the run.
    """
    try:
        await asyncio.wait_for(mgr.shutdown_all(), timeout)
    except TimeoutError:
        raise AssertionError(
            "kernel shutdown did not finish — a kernel process is wedged "
            "(known intermittent Windows kernel-lifecycle bug)"
        ) from None


async def _wait(predicate, timeout: float = 60.0, what: str = "condition"):
    async def poll():
        while True:
            found = predicate()
            if found:
                return found
            await asyncio.sleep(0.05)

    try:
        return await asyncio.wait_for(poll(), timeout)
    except TimeoutError:
        raise AssertionError(f"timed out waiting for {what}") from None


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A project whose 'venv python' is this test process's python (which has
    ipykernel as a dev dependency)."""
    import os

    settings = Path(os.environ["HORRIBLE_DATA_DIR"]) / "settings.json"
    settings.write_text(
        json.dumps({"training.projectsRoot": str(tmp_path / "projects")})
    )
    proj = projects.create_project(
        "Kernel Test",
        [EnvironmentRefModel(provider="gymnasium", kind="env", id="CartPole-v1")],
        "3.12",
    )
    notebooks.new_notebook(proj, "main.ipynb", [code_cell("x = 1")])
    monkeypatch.setattr(kernels_mod, "python_path", lambda p: Path(sys.executable))
    monkeypatch.setattr(kernels_mod, "venv_ready", lambda p: True)
    return proj


def _cell_id(conn: FakeConn, index: int = 0) -> str:
    opened = conn.events("opened")[0]
    return opened["notebook"]["cells"][index]["id"]


def test_kernel_session_end_to_end(project) -> None:
    async def go() -> None:
        mgr = TrainingKernelManager()
        conn = FakeConn()
        unsub = stream.subscribe_conn(conn)  # also captures the loop for fanout
        try:
            await _handle(
                mgr, conn, {"event": "open", "data": {"projectId": project.id}}
            )
            opened = (await _wait(lambda: conn.events("opened"), 90, "kernel start"))[0]
            key = opened["sessionKey"]
            assert opened["kernel"] == "idle"
            cid = _cell_id(conn)

            # --- print → stream output, done state -------------------------
            await _handle(
                mgr,
                conn,
                {
                    "event": "cells",
                    "data": {
                        "sessionKey": key,
                        "ops": [{"op": "edit", "cellId": cid, "source": "print('hi')"}],
                    },
                },
            )
            await _handle(
                mgr,
                conn,
                {"event": "run_cell", "data": {"sessionKey": key, "cellId": cid}},
            )
            await _wait(
                lambda: [
                    s
                    for s in conn.events("execution_state")
                    if s["cellId"] == cid and s["state"] == "done"
                ],
                60,
                "print cell done",
            )
            outs = [
                o["output"]
                for o in conn.events("output")
                if o["cellId"] == cid and o["output"]
            ]
            assert any(o["output_type"] == "stream" and "hi" in o["text"] for o in outs)

            # --- trailing expression → execute_result -----------------------
            insert = {"op": "insert", "source": "40 + 2"}
            await _handle(
                mgr,
                conn,
                {"event": "cells", "data": {"sessionKey": key, "ops": [insert]}},
            )
            session = mgr.sessions[key]
            expr_id = session.doc.cells[-1]["id"]
            await _handle(
                mgr,
                conn,
                {"event": "run_cell", "data": {"sessionKey": key, "cellId": expr_id}},
            )
            await _wait(
                lambda: [
                    o
                    for o in conn.events("output")
                    if o["cellId"] == expr_id
                    and o["output"]
                    and o["output"]["output_type"] == "execute_result"
                    and "42" in str(o["output"]["data"].get("text/plain", ""))
                ],
                60,
                "execute_result 42",
            )

            # --- error → traceback + error state ----------------------------
            await _handle(
                mgr,
                conn,
                {
                    "event": "cells",
                    "data": {
                        "sessionKey": key,
                        "ops": [{"op": "edit", "cellId": cid, "source": "1/0"}],
                    },
                },
            )
            await _handle(
                mgr,
                conn,
                {"event": "run_cell", "data": {"sessionKey": key, "cellId": cid}},
            )
            await _wait(
                lambda: [
                    s
                    for s in conn.events("execution_state")
                    if s["cellId"] == cid and s["state"] == "error"
                ],
                60,
                "error state",
            )
            err = [
                o["output"]
                for o in conn.events("output")
                if o["cellId"] == cid
                and o["output"]
                and o["output"]["output_type"] == "error"
            ]
            assert err and err[0]["ename"] == "ZeroDivisionError"

            # --- sentinel lines are stripped and re-emitted ------------------
            sentinel_src = (
                "import sys\n"
                "print('before')\n"
                'sys.stdout.write("@@HORRIBLE@@" + '
                '\'{"type": "metric", "runId": "r1", "step": 1, '
                '"values": {"loss": 0.5}}\' + "\\n")\n'
                "print('after')"
            )
            await _handle(
                mgr,
                conn,
                {
                    "event": "cells",
                    "data": {
                        "sessionKey": key,
                        "ops": [{"op": "edit", "cellId": cid, "source": sentinel_src}],
                    },
                },
            )
            conn.sent.clear()
            await _handle(
                mgr,
                conn,
                {"event": "run_cell", "data": {"sessionKey": key, "cellId": cid}},
            )
            metrics = await _wait(
                lambda: conn.events("metrics"), 60, "sentinel metric event"
            )
            assert metrics[0]["values"] == {"loss": 0.5}
            assert metrics[0]["projectId"] == project.id
            await _wait(
                lambda: [
                    s
                    for s in conn.events("execution_state")
                    if s["cellId"] == cid and s["state"] == "done"
                ],
                60,
                "sentinel cell done",
            )
            text = "".join(
                o["output"]["text"]
                for o in conn.events("output")
                if o["cellId"] == cid
                and o["output"]
                and o["output"]["output_type"] == "stream"
            )
            assert "before" in text and "after" in text
            assert "@@HORRIBLE@@" not in text

            # --- interrupt (the Windows gate) --------------------------------
            # A loop of short statements, like a real training loop. A single
            # long blocking C call (one `time.sleep(600)`) would NOT interrupt
            # promptly on Windows: `interrupt_main` only raises between
            # bytecodes — documented limitation; restart is the hard stop.
            await _handle(
                mgr,
                conn,
                {
                    "event": "cells",
                    "data": {
                        "sessionKey": key,
                        "ops": [
                            {
                                "op": "edit",
                                "cellId": cid,
                                "source": (
                                    "import time\n"
                                    "for _ in range(600):\n"
                                    "    time.sleep(0.1)"
                                ),
                            }
                        ],
                    },
                },
            )
            conn.sent.clear()
            await _handle(
                mgr,
                conn,
                {"event": "run_cell", "data": {"sessionKey": key, "cellId": cid}},
            )
            await _wait(
                lambda: [
                    s
                    for s in conn.events("execution_state")
                    if s["cellId"] == cid and s["state"] == "running"
                ],
                60,
                "sleep cell running",
            )
            await asyncio.sleep(0.3)
            await _handle(
                mgr, conn, {"event": "interrupt", "data": {"sessionKey": key}}
            )
            final = await _wait(
                lambda: [
                    s
                    for s in conn.events("execution_state")
                    if s["cellId"] == cid and s["state"] in ("done", "error")
                ],
                60,
                "interrupt to land",
            )
            assert final[0]["state"] == "error"  # KeyboardInterrupt

            # --- outputs persisted to disk -----------------------------------
            session.save_now()
            on_disk = notebooks.load(notebooks.notebook_path(project, "main.ipynb"))
            expr_cell = next(c for c in on_disk.cells if c["id"] == expr_id)
            assert any(
                o["output_type"] == "execute_result" for o in expr_cell["outputs"]
            )

            # --- restart ------------------------------------------------------
            conn.sent.clear()
            await _handle(mgr, conn, {"event": "restart", "data": {"sessionKey": key}})
            await _wait(
                lambda: [
                    s for s in conn.events("kernel_status") if s["status"] == "idle"
                ],
                90,
                "restart to idle",
            )
        finally:
            unsub()
            await _shutdown(mgr)

    asyncio.run(go())


def test_a_project_with_no_notebook_says_so(project, monkeypatch) -> None:
    """A missing notebook must arrive as a sentence, not as a bare path.

    `notebook_path` resolves without touching the disk, so the open used to reach a
    bare `FileNotFoundError`, whose `str()` is *only the path* — and that is exactly
    what the pane renders. The result was a project showing a lone path beside a
    kernel badge still reading "starting".
    """

    async def go() -> None:
        mgr = TrainingKernelManager()
        conn = FakeConn()
        unsub = stream.subscribe_conn(conn)
        try:
            await _handle(
                mgr,
                conn,
                {"event": "open", "data": {"projectId": project.id, "notebook": "gone.ipynb"}},
            )
            # `open` is detached (`handle` returns before `_open` runs), so the
            # failure has to be waited for like the success is.
            errors = await _wait(lambda: conn.events("error"), 30, "the open to fail")
            message = errors[0]["message"]
            assert "gone.ipynb" in message
            assert "has no" in message, f"not a sentence: {message!r}"
            # It is not `unknown_project`: the project is fine, so a pane that
            # self-heals by closing itself would be throwing away the wrong thing.
            assert errors[0].get("code") != "unknown_project"
            assert not conn.events("opened")
        finally:
            unsub()
            await mgr.shutdown_all()

    asyncio.run(go())


def test_an_owned_project_explains_whose_it_is(project, monkeypatch) -> None:
    """The message names the module, since "no notebook" reads like a bug on a
    project you thought was yours."""
    project.owner = "evals"
    projects.update_project(project)

    async def go() -> None:
        mgr = TrainingKernelManager()
        conn = FakeConn()
        unsub = stream.subscribe_conn(conn)
        try:
            await _handle(
                mgr,
                conn,
                {"event": "open", "data": {"projectId": project.id, "notebook": "gone.ipynb"}},
            )
            errors = await _wait(lambda: conn.events("error"), 30, "the open to fail")
            message = errors[0]["message"]
            assert "evals" in message and "working storage" in message
        finally:
            unsub()
            await mgr.shutdown_all()

    asyncio.run(go())

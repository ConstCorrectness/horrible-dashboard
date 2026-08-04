"""Kernel teardown contracts — no real kernel, because the bug these pin is a race.

Two rules, both invisible until they are broken, and both previously broken:

1. A zmq socket belongs to one thread. `stop_channels()` *closes* the sockets the
   pump threads read, so nothing may close a channel while a pump is still inside
   one. Getting this wrong is undefined behaviour in libzmq — it surfaced as an
   intermittent `ZMQError: not a socket`, an intermittent hang, and an intermittent
   segfault, none of which point at the cause.
2. A blocking kernel call must not run on the loop's **default executor**, which
   `asyncio.run` teardown and interpreter exit both join without a timeout.

The real-kernel suite (`test_training_kernels.py`) exercises the same paths, but it
could only ever catch this *sometimes*, and only as a mystery.
"""

import asyncio
import queue
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from backend.notebook_core import notebooks
from backend.notebook_core.config import SessionConfig
from backend.notebook_core.detach import fire_and_forget, run_detached
from backend.notebook_core.session import KernelSession

PUMP_PREFIXES = ("kernel-exec-", "kernel-iopub-")


def live_pumps() -> list[str]:
    return [t.name for t in threading.enumerate() if t.name.startswith(PUMP_PREFIXES)]


class FakeClient:
    """A `jupyter_client` blocking client that only knows how to time out.

    The pumps' entire exit path is "the read timed out, re-check `closing`", so a
    client that always times out is enough to reproduce the ordering.
    """

    def __init__(self, log: list[tuple[str, list[str]]]) -> None:
        self.log = log
        self.shell_channel = type("Ch", (), {"socket": object()})()
        self.session = None

    def _block(self, timeout: float | None) -> None:
        time.sleep(min(timeout or 0.05, 0.05))
        raise queue.Empty

    def get_iopub_msg(self, timeout: float | None = None) -> Any:
        self._block(timeout)

    def get_shell_msg(self, timeout: float | None = None) -> Any:
        self._block(timeout)

    def start_channels(self) -> None:
        self.log.append(("start_channels", live_pumps()))

    def stop_channels(self) -> None:
        # The moment of truth: who else is holding these sockets?
        self.log.append(("stop_channels", live_pumps()))

    def wait_for_ready(self, timeout: float | None = None) -> None:
        pass


class FakeKernelManager:
    has_kernel = True

    def __init__(self, log: list[tuple[str, list[str]]]) -> None:
        self.log = log

    def is_alive(self) -> bool:
        return True

    def client(self) -> FakeClient:
        return FakeClient(self.log)

    def restart_kernel(self, now: bool = False) -> None:
        self.log.append(("restart_kernel", live_pumps()))

    def shutdown_kernel(self, now: bool = False) -> None:
        self.log.append(("shutdown_kernel", live_pumps()))


def _session(tmp_path: Path, loop: asyncio.AbstractEventLoop) -> KernelSession:
    path = tmp_path / "n.ipynb"
    notebooks.new_notebook(path, [{"cell_type": "code", "source": "x = 1"}])
    config = SessionConfig(
        key="nb:lifecycle",
        python_executable="python",
        cwd=str(tmp_path),
        notebook_abs_path=path,
        rel_path="n.ipynb",
    )
    return KernelSession(config, loop)


def _wired(tmp_path: Path, loop: asyncio.AbstractEventLoop):
    log: list[tuple[str, list[str]]] = []
    session = _session(tmp_path, loop)
    session.km = FakeKernelManager(log)
    session.kc = FakeClient(log)
    session._start_pumps()
    return session, log


def _closes(log: list[tuple[str, list[str]]]) -> list[tuple[str, list[str]]]:
    return [row for row in log if row[0] in ("stop_channels", "shutdown_kernel")]


def test_shutdown_quiesces_the_pumps_before_closing_the_channels(tmp_path) -> None:
    async def go() -> None:
        session, log = _wired(tmp_path, asyncio.get_running_loop())
        assert len(live_pumps()) == 2  # both really started

        await run_detached(session.shutdown)

        assert _closes(log), "teardown never reached the channels"
        for call, pumps in _closes(log):
            assert pumps == [], f"{call} ran with pumps still in the sockets: {pumps}"
        assert live_pumps() == []

    asyncio.run(go())


def test_restart_quiesces_the_pumps_and_brings_new_ones_back(tmp_path) -> None:
    async def go() -> None:
        session, log = _wired(tmp_path, asyncio.get_running_loop())
        original = (session._iopub, session._worker)

        await run_detached(session.restart)

        # Same rule as shutdown — restart was the worse offender, because it closed
        # the channels and immediately opened new ones over two live pumps.
        for call, pumps in log:
            if call in ("stop_channels", "restart_kernel"):
                assert pumps == [], f"{call} ran with live pumps: {pumps}"

        # And it must leave a *working* session behind, not a quiet one: new pumps,
        # running (a stale `closing` or a leftover poison pill stops them dead).
        assert not session.closing
        assert len(live_pumps()) == 2
        assert session.status == "idle"
        time.sleep(0.2)
        assert len(live_pumps()) == 2, "the new pumps exited immediately"
        # Names are reused, so identity is the only way to see they were replaced.
        assert (session._iopub, session._worker) != original
        assert session._iopub is not None and session._iopub.is_alive()

        await run_detached(session.shutdown)

    asyncio.run(go())


def test_concurrent_restart_and_shutdown_do_not_interleave(tmp_path) -> None:
    """They arrive on independent detached threads, so only the lock orders them."""

    async def go() -> None:
        session, log = _wired(tmp_path, asyncio.get_running_loop())
        fire_and_forget(session.restart, "kernel-restart-test")
        fire_and_forget(session.shutdown, "kernel-shutdown-test")
        deadline = time.monotonic() + 10
        while session.status != "dead" and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert session.status == "dead"
        for call, pumps in log:
            assert pumps == [], f"{call} ran with live pumps: {pumps}"

    asyncio.run(go())


def test_run_detached_relays_results_and_errors_off_the_default_executor() -> None:
    async def go() -> None:
        seen: dict[str, Any] = {}

        def work(a: int, b: int) -> int:
            seen["thread"] = threading.current_thread()
            return a + b

        assert await run_detached(work, 2, 3, name="kernel-probe") == 5
        # A default-executor worker could never carry this name — that is the whole
        # difference from `asyncio.to_thread`, and it is what keeps a wedged call
        # from being joined by `asyncio.run`'s teardown.
        assert seen["thread"].name == "kernel-probe"
        assert seen["thread"].daemon

        def boom() -> None:
            raise ValueError("kaboom")

        with pytest.raises(ValueError, match="kaboom"):
            await run_detached(boom)

    asyncio.run(go())


def test_abandoning_a_detached_call_does_not_wedge_the_loop() -> None:
    """`wait_for` cancels the await, never the thread. The loop must still close."""
    release = threading.Event()

    async def go() -> None:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(run_detached(release.wait, 30), 0.2)

    started = time.monotonic()
    asyncio.run(go())  # would hang here if the worker sat in the default executor
    assert time.monotonic() - started < 10
    release.set()

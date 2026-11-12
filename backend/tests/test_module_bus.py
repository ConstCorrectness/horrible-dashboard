"""The local module bus: records, the per-process MCP endpoint, mirrored tools,
and the one-owner-per-module rule."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

from backend.kernel import bus, bus_node
from backend.kernel.bus_bridge import BusBridge, decode_result
from backend.kernel.module_manager import ModuleManager
from backend.kernel.module_mcp import encode_result
from backend.sdk.registry import PluginRegistry
from backend.tests.test_module_lifecycle import _package


@pytest.fixture(autouse=True)
def _clean_modules():
    yield
    bus_node.node = None
    for name in [m for m in sys.modules if m.startswith("horrible_mod_test_")]:
        sys.modules.pop(name, None)


# --- records ---------------------------------------------------------------------


def _write(rec: bus.BusRecord) -> None:
    bus.bus_dir().mkdir(parents=True, exist_ok=True)
    (bus.bus_dir() / f"proc-{rec.pid}.json").write_text(json.dumps(rec.to_json()))


def _rec(**kw) -> bus.BusRecord:
    me = os.getpid()
    base = dict(
        pid=me,
        started_at=bus._create_time(me),
        kind="standalone",
        name="x",
        host="127.0.0.1",
        port=1,
        token="t",
        heartbeat_at=time.time(),
    )
    base.update(kw)
    return bus.BusRecord(**base)


def test_a_fresh_record_from_a_live_process_is_read() -> None:
    _write(_rec(modules=["karaoke"]))
    [rec] = bus.read_records(include_self=True)
    assert rec.modules == ["karaoke"]
    # A process never sees itself as a peer.
    assert bus.read_records() == []


def test_stale_and_recycled_pid_records_are_pruned() -> None:
    _write(_rec(pid=os.getpid(), heartbeat_at=time.time() - bus.STALE_S - 1))
    assert bus.read_records(include_self=True) == []
    # The pid is alive, but it is not the process that wrote the record: Windows
    # reuses pids, so a crashed runner's record must not come back to life.
    parent = os.getppid()
    _write(_rec(pid=parent, started_at=1.0))
    assert bus.read_records() == []
    assert not (bus.bus_dir() / f"proc-{parent}.json").exists()


def test_tool_results_round_trip_through_text() -> None:
    for value in ({"playing": True, "queue": [1, 2]}, [1, "a"], "plain words", 3):
        assert decode_result(encode_result(value)) == value


# --- one owner per module ----------------------------------------------------------


def test_a_module_held_by_a_peer_is_deferred_then_loaded(tmp_path: Path) -> None:
    builtin = tmp_path / "modules"
    builtin.mkdir()
    _package(builtin, "solo")
    reg = PluginRegistry()
    mgr = ModuleManager(reg)
    mgr.scan_kwargs = {
        "builtin_dir": builtin,
        "installed_dir": tmp_path / "i",
        "entry_points": False,
    }
    holder: dict[str, str | None] = {"solo": "karaoke-runner (pid 1)"}
    released: list[str] = []
    mgr.claim = holder.get
    mgr.release = released.append
    mgr.discover()
    mgr.load_all()
    assert not mgr.is_loaded("solo")
    assert "running in karaoke-runner" in (mgr.describe("solo")["error"] or "")
    assert "solo.hello" not in reg.agent_tools

    holder["solo"] = None  # the runner stopped
    assert asyncio.run(mgr.retry_deferred()) == ["solo"]
    assert mgr.is_loaded("solo")
    assert mgr.describe("solo")["error"] is None
    asyncio.run(mgr.unload("solo"))
    assert released == ["solo"]


def test_a_runner_takes_a_module_the_dashboard_disabled(tmp_path: Path) -> None:
    # The handover found live: the dashboard disables karaoke (writing the shared
    # blocklist) and lets go of it; the runner, started for karaoke, must still take
    # it. "Enabled" in a runner means "one it was started for".
    builtin = tmp_path / "modules"
    builtin.mkdir()
    _package(builtin, "solo")
    mgr = ModuleManager(PluginRegistry())
    mgr.scan_kwargs = {
        "builtin_dir": builtin,
        "installed_dir": tmp_path / "i",
        "entry_points": False,
    }
    mgr.only = {"solo"}
    holder: dict[str, str | None] = {"solo": "dashboard (pid 1)"}
    mgr.claim = holder.get
    mgr.discover()
    mgr.load_all()
    mgr._write_disabled({"solo"})  # what the dashboard's Disable wrote
    holder["solo"] = None
    assert asyncio.run(mgr.retry_deferred()) == ["solo"]
    assert mgr.describe("solo")["enabled"] is True


def test_a_claim_is_exclusive_and_a_dead_holders_claim_is_taken() -> None:
    assert bus.claim("karaoke") is None
    assert bus.claim("karaoke") is None  # this process already holds it
    # A live process other than this one holds it: refused, and named.
    parent = os.getppid()
    path = bus.bus_dir() / "claim-music.json"
    path.write_text(json.dumps({"pid": parent, "startedAt": bus._create_time(parent)}))
    assert bus.claim("music") == f"pid {parent}"
    # Its holder died (the create time no longer matches): the claim is taken over.
    path.write_text(json.dumps({"pid": parent, "startedAt": 1.0}))
    assert bus.claim("music") is None
    bus.release("karaoke")
    assert not (bus.bus_dir() / "claim-karaoke.json").exists()


# --- across a real socket -------------------------------------------------------------


class _Served:
    """A slim app on a real loopback port, in a thread."""

    def __init__(self, app, sock: socket.socket) -> None:
        self.server = uvicorn.Server(
            uvicorn.Config(app, lifespan="on", log_level="warning")
        )
        self.thread = threading.Thread(
            target=self.server.run, kwargs={"sockets": [sock]}, daemon=True
        )

    def __enter__(self) -> _Served:
        self.thread.start()
        deadline = time.time() + 20
        while not self.server.started:
            assert time.time() < deadline, "slim app did not start"
            time.sleep(0.05)
        return self

    def __exit__(self, *exc) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=20)


def test_a_runner_serves_its_tools_and_a_peer_calls_them(
    tmp_path: Path, monkeypatch
) -> None:
    from backend.kernel.slim_app import build_app

    monkeypatch.setenv(bus_node.BUS_ENV, "on")
    builtin = tmp_path / "modules"
    builtin.mkdir()
    _package(builtin, "echo")
    reg = PluginRegistry()
    mgr = ModuleManager(reg)
    mgr.scan_kwargs = {
        "builtin_dir": builtin,
        "installed_dir": tmp_path / "i",
        "entry_points": False,
    }

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(16)
    port = sock.getsockname()[1]
    app = build_app(["echo"], port=port, manager=mgr, reg=reg, shell_dist=None)
    assert mgr.is_loaded("echo")

    with _Served(app, sock):
        [rec] = bus.read_records(include_self=True)
        assert rec.modules == ["echo"] and rec.port == port
        assert rec.tool_groups == [{"id": "echo", "description": "echo tools"}]

        # No token, no tools.
        assert httpx.post(rec.url, json={}).status_code == 401
        # A page that resolved its own name to 127.0.0.1 still sends its own Host.
        rebound = httpx.post(
            rec.url,
            json={},
            headers={"Authorization": f"Bearer {rec.token}", "Host": "evil.example"},
        )
        assert rebound.status_code in (400, 403, 421)

        peer = PluginRegistry()
        bridge = BusBridge(peer, include_self=True)

        async def scenario() -> None:
            assert await bridge.sync_once() is True
            mirror = peer.agent_tools["echo.hello"]
            # Same name, same group as in-process; never always-on.
            assert mirror.group == "echo"
            assert bridge.group_description("echo") == "echo tools"
            assert await peer.invoke_agent_tool("echo.hello", {}) == "hi"
            # Nothing changed on the peer: the next poll does not re-list.
            assert await bridge.sync_once() is False

        asyncio.run(scenario())

    # The runner stopped and removed its record; the next poll drops the mirror.
    assert bus.read_records(include_self=True) == []
    assert asyncio.run(bridge.sync_once()) is True
    assert "echo.hello" not in peer.agent_tools

"""Tests for the backend plugin SDK (`backend.sdk`).

The example plugin (examples/backend-plugins/ping) is loaded into a *fresh* registry
so it never pollutes the global one, then every capability it registers — HTTP
route, agent tool, /ws channel, dash facade, lifecycle hooks — is exercised.
"""

import asyncio
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.modules.repl.sdk import build_namespace
from backend.sdk import PluginRegistry, load_plugins
from backend.sdk.registry import registry as global_registry

EXAMPLE_DIR = (
    Path(__file__).resolve().parent.parent.parent / "examples" / "backend-plugins"
)


def _load_example() -> PluginRegistry:
    reg = PluginRegistry()
    load_plugins(extra_dirs=[EXAMPLE_DIR], reg=reg)
    return reg


def test_plugin_is_discovered_and_loaded() -> None:
    reg = _load_example()
    assert "ping" in [m.id for m in reg.loaded]
    assert reg.errors == []


def test_plugin_http_route_mounts() -> None:
    reg = _load_example()
    app = FastAPI()
    for mounted in reg.routers:
        app.include_router(mounted.router, prefix=f"/api{mounted.prefix}")
    res = TestClient(app).get("/api/plugins/ping")
    assert res.status_code == 200
    assert res.json() == {"pong": True, "plugin": "ping"}


def test_plugin_agent_tool_is_advertised_and_runs() -> None:
    reg = _load_example()
    assert "ping.echo" in reg.agent_tools
    assert any(t["function"]["name"] == "ping.echo" for t in reg.provider_tools())
    result = asyncio.run(reg.invoke_agent_tool("ping.echo", {"text": "hi"}))
    assert result == {"echo": "hi"}


def test_plugin_ws_channel_dispatches() -> None:
    reg = _load_example()

    class FakeConn:
        def __init__(self) -> None:
            self.sent: list[dict] = []

        async def send_json(self, data: dict) -> None:
            self.sent.append(data)

    conn = FakeConn()
    handled = asyncio.run(
        reg.dispatch_ws(conn, "ping", {"channel": "ping", "data": 42})
    )
    assert handled is True
    assert conn.sent[0]["event"] == "pong" and conn.sent[0]["data"] == 42
    # An unregistered channel is not handled (the /ws loop ignores it).
    assert asyncio.run(reg.dispatch_ws(conn, "nope", {})) is False


def test_plugin_lifecycle_hooks_registered_and_run() -> None:
    reg = _load_example()
    assert len(reg.startup_hooks) == 1 and len(reg.shutdown_hooks) == 1

    # The registry runs both sync and async hooks, in order.
    reg2 = PluginRegistry()
    log: list[str] = []
    reg2.startup_hooks.append(lambda: log.append("up"))

    async def adown() -> None:
        log.append("down")

    reg2.shutdown_hooks.append(adown)
    asyncio.run(reg2.run_startup())
    asyncio.run(reg2.run_shutdown())
    assert log == ["up", "down"]


def test_plugin_dash_facade_attaches_to_repl_namespace() -> None:
    reg = _load_example()
    assert "ping" in reg.dash_facades
    # build_namespace reads the global registry; load the example into it briefly.
    global_registry.reset()
    try:
        load_plugins(extra_dirs=[EXAMPLE_DIR], reg=global_registry)
        dash = build_namespace(lambda name, args: None)["dash"]
        assert hasattr(dash, "ping")
        assert dash.ping.pong() == "pong"
    finally:
        global_registry.reset()


def test_broken_plugin_is_recorded_not_raised(tmp_path: Path) -> None:
    (tmp_path / "boom.py").write_text("raise RuntimeError('kaboom')\n")
    (tmp_path / "empty.py").write_text("x = 1  # no PLUGIN\n")
    reg = PluginRegistry()
    load_plugins(extra_dirs=[tmp_path], reg=reg)
    messages = " ".join(msg for _, msg in reg.errors)
    assert "kaboom" in messages
    assert "no PLUGIN" in messages
    assert reg.loaded == []  # neither bad module registered

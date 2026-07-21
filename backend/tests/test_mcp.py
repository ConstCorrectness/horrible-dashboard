"""The MCP module: config custody, name/group derivation, and a real end-to-end
session against a fixture server (transport -> handshake -> bridge -> agent tools).

The integration tests deliberately spawn a real subprocess rather than mocking the
session. The whole reason this module ships its own stdio transport is that the SDK's
fails on the event loop uvicorn actually uses; a mocked session would pass on a
transport that cannot spawn anything.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from backend.modules.mcp import bridge
from backend.modules.mcp import config as cfg
from backend.modules.mcp.client import McpManager, _flatten_result
from backend.modules.mcp.transport import resolve_command
from backend.sdk.registry import registry

FIXTURE_SERVER = str(Path(__file__).parent / "mcp_fixture_server.py")


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the config store at a temp dir so tests never touch real `.data`."""
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    return tmp_path


# --- config store ---------------------------------------------------------------


def test_validate_rejects_bad_ids() -> None:
    assert cfg.validate_id("filesystem") is None
    assert cfg.validate_id("my-server_2") is None
    # Uppercase, dots and spaces would break provider tool-name rules or the
    # group-splitting on the first dot.
    assert cfg.validate_id("My.Server") is not None
    assert cfg.validate_id("has space") is not None
    assert cfg.validate_id("") is not None


def test_validate_requires_transport_fields() -> None:
    assert cfg.validate({"id": "x", "transport": "stdio", "command": "npx"}) is None
    assert cfg.validate({"id": "x", "transport": "stdio", "command": ""}) is not None
    assert cfg.validate({"id": "x", "transport": "http", "url": "https://a.b"}) is None
    assert (
        cfg.validate({"id": "x", "transport": "http", "url": "ftp://a.b"}) is not None
    )
    assert cfg.validate({"id": "x", "transport": "carrier-pigeon"}) is not None


def test_save_list_delete_roundtrip(data_dir: Path) -> None:
    cfg.save_server(
        {"id": "a", "transport": "stdio", "command": "echo", "args": ["hi"]}
    )
    cfg.save_server({"id": "b", "transport": "http", "url": "https://example.test"})
    assert [s["id"] for s in cfg.list_servers()] == ["a", "b"]

    # Saving the same id replaces rather than duplicates.
    cfg.save_server({"id": "a", "transport": "stdio", "command": "echo", "args": []})
    assert [s["id"] for s in cfg.list_servers()] == ["b", "a"]

    assert cfg.delete_server("a") is True
    assert cfg.delete_server("a") is False
    assert [s["id"] for s in cfg.list_servers()] == ["b"]


def test_secrets_never_reach_the_plaintext_config(data_dir: Path) -> None:
    """A token submitted alongside a config must not be persisted with it.

    `mcp-servers.json` is plaintext in the data dir; a token there would leak by
    copying the directory. This asserts the allow-list actually drops it.
    """
    cfg.save_server(
        {
            "id": "leaky",
            "transport": "http",
            "url": "https://example.test",
            "token": "super-secret",
            "password": "also-secret",
        }
    )
    raw = (data_dir / "mcp-servers.json").read_text(encoding="utf-8")
    assert "super-secret" not in raw
    assert "also-secret" not in raw
    stored = cfg.get_server("leaky")
    assert stored is not None and "token" not in stored


def test_corrupt_config_file_reads_as_empty(data_dir: Path) -> None:
    (data_dir / "mcp-servers.json").write_text("{not json", encoding="utf-8")
    assert cfg.list_servers() == []


# --- naming / grouping ----------------------------------------------------------


def test_group_and_tool_naming() -> None:
    assert cfg.group_name("github") == "mcp-github"
    assert bridge.tool_name("github", "search_code") == "mcp-github.search_code"
    # Characters a provider would reject are rewritten.
    assert bridge.tool_name("x", "weird name!") == "mcp-x.weird_name_"


def test_mcp_group_cannot_collide_with_a_connector_group() -> None:
    """The prefix is the whole reason an MCP server named `github` is safe."""
    assert cfg.group_name("github") != "github"
    assert cfg.group_name("github").startswith(cfg.GROUP_PREFIX)


def test_split_tool_name_roundtrip() -> None:
    assert bridge.split_tool_name("mcp-fs.read_file") == ("fs", "read_file")
    # Non-MCP tools must not be claimed by the bridge.
    assert bridge.split_tool_name("github.searchCode") is None
    assert bridge.split_tool_name("open_pane") is None


def test_group_of_derives_the_mcp_group() -> None:
    """The orchestrator's own splitter must land on our group name."""
    from backend.modules.agent.orchestrator import _group_of

    assert _group_of(bridge.tool_name("fs", "read_file")) == "mcp-fs"


# --- result flattening ----------------------------------------------------------


def test_flatten_result_joins_text_and_marks_errors() -> None:
    from mcp import types

    ok = types.CallToolResult(content=[types.TextContent(type="text", text="hello")])
    assert _flatten_result(ok)["content"] == "hello"

    failed = types.CallToolResult(
        content=[types.TextContent(type="text", text="bad")], isError=True
    )
    flat = _flatten_result(failed)
    assert flat["error"] == "bad"
    assert "content" not in flat


def test_flatten_result_names_binary_instead_of_inlining_it() -> None:
    """A base64 image inlined into a tool result would blow the context window."""
    from mcp import types

    result = types.CallToolResult(
        content=[
            types.TextContent(type="text", text="see image"),
            types.ImageContent(type="image", data="AAAA", mimeType="image/png"),
        ]
    )
    flat = _flatten_result(result)
    assert flat["content"] == "see image"
    assert flat["attachments"] == ["<image image/png>"]
    assert "AAAA" not in json.dumps(flat)


# --- transport ------------------------------------------------------------------


def test_resolve_command_finds_python_and_misses_nonsense() -> None:
    assert resolve_command(sys.executable) is not None
    assert resolve_command("definitely-not-a-real-command-xyz") is None


# --- end-to-end against a real server -------------------------------------------


def _start(manager: McpManager, server_id: str) -> Any:
    return asyncio.run(manager.start_server(server_id))


@pytest.mark.timeout(120)
def test_end_to_end_session_bridges_tools(data_dir: Path) -> None:
    """Spawn the fixture server, connect, and assert the agent can see its tools."""
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
        runtime = await manager.start_server("fixture")
        assert runtime is not None
        bridge.sync(manager)
        session = manager.get("fixture")
        assert session is not None
        ok = await session.call_tool("peek", {"key": "k"})
        err = await session.call_tool("boom", {})
        missing = await session.call_tool("no_such_tool", {})
        # Snapshot live state *before* teardown — stop_all mutates the runtime.
        connected = {
            "state": runtime.state,
            "error": runtime.error,
            "server_name": runtime.server_name,
            "tools": {t.name for t in runtime.tools},
        }
        await manager.stop_all()
        return {"connected": connected, "ok": ok, "err": err, "missing": missing}

    try:
        out = asyncio.run(scenario())
        connected = out["connected"]

        assert connected["state"] == "ready", connected["error"]
        assert connected["server_name"] == "fixture"
        assert connected["tools"] == {"peek", "poke", "boom"}

        # Tools registered under the prefixed group, with schemas carried over.
        peek = registry.agent_tools.get("mcp-fixture.peek")
        poke = registry.agent_tools.get("mcp-fixture.poke")
        assert peek is not None and poke is not None
        assert peek.group == "mcp-fixture"
        assert "key" in peek.parameters
        assert set(poke.required) == {"key", "value"}

        # readOnlyHint downgrades to pass-through; everything else stays gated.
        assert peek.side_effect is False
        assert poke.side_effect is True

        # Calls, error path, and unknown-tool path all return results, never raise.
        assert out["ok"]["content"] == "value:k"
        assert "error" in out["err"]
        assert "error" in out["missing"]
    finally:
        for name in list(registry.agent_tools):
            if name.startswith(cfg.GROUP_PREFIX):
                registry.agent_tools.pop(name, None)


@pytest.mark.timeout(120)
def test_guide_carries_server_instructions(data_dir: Path) -> None:
    """A server's own docs become the group guide — the documentation half of MCP."""
    cfg.save_server(
        {
            "id": "fixture",
            "transport": "stdio",
            "command": sys.executable,
            "args": [FIXTURE_SERVER],
        }
    )
    manager = McpManager()

    async def scenario() -> Any:
        runtime = await manager.start_server("fixture")
        await manager.stop_all()
        return runtime

    runtime = asyncio.run(scenario())
    guide = bridge.guide_for(runtime)
    assert guide is not None
    assert "Fixture server used by the test suite." in guide
    # The untrusted-content warning must always ride along with third-party text.
    assert "third-party" in guide.lower()


@pytest.mark.timeout(60)
def test_missing_command_reports_error_state_instead_of_raising(data_dir: Path) -> None:
    """A broken server is a status, not a crash — the backend must still boot."""
    cfg.save_server(
        {
            "id": "broken",
            "transport": "stdio",
            "command": "definitely-not-a-real-command-xyz",
            "args": [],
        }
    )
    manager = McpManager()
    runtime = asyncio.run(manager.start_server("broken"))
    assert runtime is not None
    assert runtime.state == "error"
    assert "not on PATH" in (runtime.error or "")
    asyncio.run(manager.stop_all())


# --- HTTP surface ---------------------------------------------------------------


@pytest.fixture
def client(data_dir: Path):
    from fastapi.testclient import TestClient

    from backend.app import app

    return TestClient(app)


def test_routes_list_add_and_delete(client) -> None:
    assert client.get("/api/mcp/servers").json()["servers"] == []

    created = client.post(
        "/api/mcp/servers",
        json={
            "id": "broken",
            "transport": "stdio",
            "command": "definitely-not-a-real-command-xyz",
            "enabled": True,
        },
    )
    assert created.status_code == 200
    body = created.json()
    # A server that can't start is still created, reported as an error state.
    assert body["id"] == "broken"
    assert body["group"] == "mcp-broken"
    assert body["state"] == "error"
    assert body["target"]["available"] is False

    assert len(client.get("/api/mcp/servers").json()["servers"]) == 1
    assert client.delete("/api/mcp/servers/broken").status_code == 200
    assert client.get("/api/mcp/servers").json()["servers"] == []
    assert client.delete("/api/mcp/servers/broken").status_code == 404


def test_routes_reject_invalid_config(client) -> None:
    bad_id = client.post(
        "/api/mcp/servers",
        json={"id": "Bad.Id", "transport": "stdio", "command": "echo"},
    )
    assert bad_id.status_code == 400

    no_url = client.post("/api/mcp/servers", json={"id": "x", "transport": "http"})
    assert no_url.status_code == 400


def test_routes_never_return_a_token(client) -> None:
    """The token is write-only across the API boundary."""
    res = client.post(
        "/api/mcp/servers",
        json={
            "id": "authed",
            "transport": "http",
            "url": "https://example.test/mcp",
            "token": "shhh-secret",
            "enabled": False,
        },
    )
    assert res.status_code == 200
    assert "shhh-secret" not in res.text
    assert res.json()["hasToken"] is True
    # And not on the way back out through the listing either.
    listing = client.get("/api/mcp/servers")
    assert "shhh-secret" not in listing.text


def test_connect_unknown_server_is_404(client) -> None:
    assert client.post("/api/mcp/servers/ghost/connect", json={}).status_code == 404


@pytest.mark.timeout(120)
def test_start_enabled_skips_disabled_servers(data_dir: Path) -> None:
    cfg.save_server(
        {
            "id": "off",
            "transport": "stdio",
            "command": sys.executable,
            "args": [FIXTURE_SERVER],
            "enabled": False,
        }
    )
    manager = McpManager()
    asyncio.run(manager.start_enabled())
    assert manager.get("off") is None
    # It still appears in the listing, as a stopped server the UI can start.
    states = {r.id: r.state for r in manager.runtimes()}
    assert states == {"off": "stopped"}

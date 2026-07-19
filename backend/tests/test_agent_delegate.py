"""The agent.delegate backend tool: validation, the happy path, timeout, and the
end-to-end turn wiring (agentId-tagged events, spec-driven system prompt)."""

import asyncio
import json
from typing import Any

import httpx

from backend.modules.agent import delegate, orchestrator
from backend.modules.agent.models import AgentConfig


class FakeConn:
    """Browser stand-in: auto-answers relayed tool_calls (see test_agent_orchestrator)."""

    def __init__(self, tool_results: dict[str, Any] | None = None) -> None:
        self.sent: list[dict[str, Any]] = []
        self.pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self.pending_approvals: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self.agent_tools: list[dict[str, Any]] = []
        self.tool_results = tool_results or {}

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)
        if data.get("event") == "tool_call":
            d = data["data"]
            fut = self.pending.get(d["callId"])
            if fut is not None and not fut.done():
                fut.set_result(
                    {
                        "ok": True,
                        "result": self.tool_results.get(d["name"], {"ok": True}),
                    }
                )

    def events(self) -> list[tuple[str, dict[str, Any]]]:
        return [(s["event"], s["data"]) for s in self.sent]


def _configure(monkeypatch) -> None:
    config = AgentConfig(model="m", endpoint="http://ollama.test")
    monkeypatch.setattr(orchestrator, "_load_config", lambda: config)
    monkeypatch.setattr(delegate, "_load_config", lambda: config)


def _mock_ollama(monkeypatch, handler) -> None:
    monkeypatch.setattr(
        orchestrator,
        "instrumented_client",
        lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def test_delegate_rejects_bad_targets(monkeypatch) -> None:
    _configure(monkeypatch)
    conn = FakeConn()
    assert "error" in asyncio.run(delegate.run_delegate(conn, "t", "", "do it"))
    assert "error" in asyncio.run(delegate.run_delegate(conn, "t", "coder", ""))
    unknown = asyncio.run(delegate.run_delegate(conn, "t", "nope", "do it"))
    assert "unknown agent" in unknown["error"]
    # 'coder' is listed as an available target in the error message.
    assert "coder" in unknown["error"]
    main = asyncio.run(delegate.run_delegate(conn, "t", "main", "do it"))
    assert "cannot delegate" in main["error"]


def test_delegate_happy_path_returns_answer(monkeypatch) -> None:
    _configure(monkeypatch)

    async def fake_loop(conn, turn_id, messages, tools, *args, **kwargs) -> str:
        # The sub-turn carries the target spec's prompt and scope.
        assert messages[0]["role"] == "system"
        assert "database agent" in messages[0]["content"]
        assert kwargs["spec"].id == "dba"
        assert kwargs["active_groups"] == {"database"}
        assert turn_id.startswith("parent:dba:")
        return "there are 12 tables"

    monkeypatch.setattr(orchestrator, "run_agent_loop", fake_loop)
    result = asyncio.run(
        delegate.run_delegate(FakeConn(), "parent", "dba", "what tables exist?")
    )
    assert result == {"agent": "dba", "answer": "there are 12 tables"}


def test_delegate_times_out(monkeypatch) -> None:
    _configure(monkeypatch)
    monkeypatch.setattr(delegate, "DELEGATE_TIMEOUT_S", 0.05)

    async def slow_loop(*args, **kwargs) -> str:
        await asyncio.sleep(1)
        return "too late"

    monkeypatch.setattr(orchestrator, "run_agent_loop", slow_loop)
    result = asyncio.run(delegate.run_delegate(FakeConn(), "t", "coder", "hang"))
    assert "timed out" in result["error"]


def test_specialized_turn_uses_spec_prompt_and_tags_events(monkeypatch) -> None:
    _configure(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        # The dba persona drives the turn, not the layout orchestrator.
        assert "database agent" in body["messages"][0]["content"]
        names = {t["function"]["name"] for t in body["tools"]}
        assert "agent.ask_peer" not in names
        assert "agent.delegate" not in names
        return httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": "hello from dba"}},
        )

    _mock_ollama(monkeypatch, handler)
    conn = FakeConn()
    asyncio.run(orchestrator.run_agent_turn(conn, "t1", "hi", agent_id="dba"))
    events = dict(conn.events())
    assert events["answer"]["agentId"] == "dba"
    assert events["done"]["agentId"] == "dba"


def test_unknown_agent_turn_errors(monkeypatch) -> None:
    _configure(monkeypatch)
    conn = FakeConn()
    asyncio.run(orchestrator.run_agent_turn(conn, "t1", "hi", agent_id="ghost"))
    events = dict(conn.events())
    assert "Unknown agent" in events["error"]["message"]


def test_delegate_dispatches_through_backend_tools(monkeypatch) -> None:
    """agent.delegate routes through _run_backend_tool into run_delegate."""
    _configure(monkeypatch)

    async def fake_run_delegate(conn, turn_id, agent_id, prompt) -> dict[str, Any]:
        return {"agent": agent_id, "answer": f"did: {prompt}"}

    monkeypatch.setattr(delegate, "run_delegate", fake_run_delegate)

    class _Call:
        name = "agent.delegate"
        arguments = {"agentId": "coder", "prompt": "fix the bug"}

    result = asyncio.run(orchestrator._run_backend_tool(FakeConn(), "t", _Call()))
    assert result == {"agent": "coder", "answer": "did: fix the bug"}


def test_roster_route_lists_builtins(monkeypatch, tmp_path) -> None:
    from fastapi.testclient import TestClient

    from backend.app import app

    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    res = client.get("/api/agent/roster")
    assert res.status_code == 200
    agents = {a["id"]: a for a in res.json()["agents"]}
    assert {"main", "coder", "dba", "researcher"} <= set(agents)
    assert agents["main"]["tool_groups"] is None
    assert "database" in agents["dba"]["tool_groups"]

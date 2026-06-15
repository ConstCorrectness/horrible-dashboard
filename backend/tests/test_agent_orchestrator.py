import asyncio
import json
from typing import Any

import httpx

from backend.modules.agent import orchestrator
from backend.modules.agent.models import AgentConfig
from backend.modules.ws import WsConnection


class FakeConn:
    """Captures sends and auto-answers each tool_call by resolving its future,
    standing in for the browser end of the `agent` channel."""

    def __init__(self, tool_results: dict[str, Any] | None = None) -> None:
        self.sent: list[dict[str, Any]] = []
        self.pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
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
    monkeypatch.setattr(
        orchestrator,
        "_load_config",
        lambda: AgentConfig(model="m", endpoint="http://ollama.test"),
    )


def _mock_ollama(monkeypatch, handler) -> None:
    monkeypatch.setattr(
        orchestrator,
        "instrumented_client",
        lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def test_turn_calls_tool_then_answers(monkeypatch) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        body = json.loads(request.content)
        if calls["n"] == 1:
            # tools are advertised; the model asks to open a pane
            assert any(t["function"]["name"] == "open_pane" for t in body["tools"])
            return httpx.Response(
                200,
                json={
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "open_pane",
                                    "arguments": {"id": "observability.io"},
                                }
                            }
                        ],
                    }
                },
            )
        # second round: the tool result must have been fed back in
        assert any(m.get("role") == "tool" for m in body["messages"])
        return httpx.Response(
            200,
            json={
                "message": {
                    "role": "assistant",
                    "content": "Opened the Data flow pane.",
                }
            },
        )

    _configure(monkeypatch)
    _mock_ollama(monkeypatch, handler)

    conn = FakeConn()
    asyncio.run(orchestrator.run_agent_turn(conn, "t1", "open data flow"))
    events = conn.events()

    tool_calls = [d for ev, d in events if ev == "tool_call"]
    assert tool_calls and tool_calls[0]["name"] == "open_pane"
    assert tool_calls[0]["args"] == {"id": "observability.io"}

    answers = [d["text"] for ev, d in events if ev == "answer"]
    assert answers and "Data flow" in answers[0]
    assert any(ev == "done" for ev, _ in events)
    assert calls["n"] == 2


def test_answer_without_tools(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"message": {"role": "assistant", "content": "Hi there."}}
        )

    _configure(monkeypatch)
    _mock_ollama(monkeypatch, handler)

    conn = FakeConn()
    asyncio.run(orchestrator.run_agent_turn(conn, "t", "hello"))
    events = conn.events()
    assert not [ev for ev, _ in events if ev == "tool_call"]
    assert [d["text"] for ev, d in events if ev == "answer"] == ["Hi there."]


def test_unconfigured_emits_error(monkeypatch) -> None:
    monkeypatch.setattr(orchestrator, "_load_config", lambda: None)
    conn = FakeConn()
    asyncio.run(orchestrator.run_agent_turn(conn, "t", "hi"))
    assert conn.sent[0]["event"] == "error"


def test_tool_result_resolves_pending_future() -> None:
    async def go() -> dict[str, Any]:
        conn = WsConnection(websocket=None)
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        conn.pending["abc"] = fut
        await orchestrator.handle_agent_message(
            conn,
            {
                "channel": "agent",
                "event": "tool_result",
                "data": {"callId": "abc", "ok": True, "result": 1},
            },
        )
        return fut.result()

    assert asyncio.run(go()) == {"callId": "abc", "ok": True, "result": 1}

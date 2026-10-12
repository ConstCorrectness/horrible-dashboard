"""Tests for the Agent Client Protocol (ACP) integration.

Exercises ACP models, JSON-RPC stdio transport with the fixture server,
session prompt streaming, workspace file operations, and orchestrator turn dispatch.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from typing import Any
import pytest

from backend.modules.acp.client import AcpClientSession, run_acp_turn
from backend.modules.acp.models import (
    AcpClientRequest,
    AcpInitializeParams,
    AcpInitializeResult,
    AcpSessionPromptParams,
    AcpSessionUpdateNotification,
    SessionUpdateAgentMessageChunk,
)
from backend.modules.agent import roster

FIXTURE_SERVER = [sys.executable, str(Path(__file__).parent / "acp_fixture_server.py")]


class MockWsConnection:
    def __init__(self) -> None:
        self.sent_events: list[dict[str, Any]] = []

    async def send_json(self, msg: dict[str, Any]) -> None:
        self.sent_events.append(msg)


def test_acp_models_validation() -> None:
    """Verify ACP Pydantic models structure."""
    init_params = AcpInitializeParams(
        protocolVersion=1,
        clientInfo={"name": "horrible-dashboard"},
        capabilities={"fs": {"readTextFile": True}},
    )
    req = AcpClientRequest(
        id=1,
        method="initialize",
        params=init_params.model_dump(),
    )
    assert req.jsonrpc == "2.0"
    assert req.id == 1
    assert req.method == "initialize"

    init_res = AcpInitializeResult(
        protocolVersion=1,
        agentInfo={"name": "claude"},
        capabilities={},
    )
    assert init_res.agentInfo["name"] == "claude"

    prompt_params = AcpSessionPromptParams(
        sessionId="sess-123",
        prompt=[{"type": "text", "text": "Hello ACP"}],
    )
    assert prompt_params.sessionId == "sess-123"

    chunk = SessionUpdateAgentMessageChunk(delta="world")
    update_notif = AcpSessionUpdateNotification(
        params={"sessionId": "sess-123", "update": chunk.model_dump()}
    )
    assert update_notif.method == "session/update"


def test_acp_session_roundtrip(tmp_path: Path) -> None:
    """Test full stdio JSON-RPC cycle with the ACP fixture server."""
    async def go() -> None:
        session = AcpClientSession(command=FIXTURE_SERVER, cwd=tmp_path)
        try:
            await session.start()

            # 1. Initialize
            res = await session.initialize()
            assert res.get("agentInfo", {}).get("name") == "fixture-acp-agent"
            assert session.agent_info.get("name") == "fixture-acp-agent"

            # 2. New session
            sess_id = await session.new_session()
            assert sess_id == "test-acp-session-42"
            assert session.session_id == "test-acp-session-42"

            # 3. Prompt turn with streaming
            tokens: list[str] = []
            reasoning: list[str] = []

            async def on_token(delta: str) -> None:
                tokens.append(delta)

            async def on_reasoning(delta: str) -> None:
                reasoning.append(delta)

            prompt_res = await session.prompt(
                "Say hello",
                on_token=on_token,
                on_reasoning=on_reasoning,
            )
            assert prompt_res.get("stopReason") == "end_turn"
            assert reasoning == ["Thinking about it..."]
            assert tokens == ["Hello from ACP agent!"]
        finally:
            await session.close()

    asyncio.run(go())


def test_acp_fs_security_sandbox(tmp_path: Path) -> None:
    """Verify that file read/write operations stay strictly within cwd."""
    async def go() -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        secret_file = tmp_path / "secret.txt"
        secret_file.write_text("super secret", encoding="utf-8")

        session = AcpClientSession(command=FIXTURE_SERVER, cwd=workspace)
        responses: list[dict[str, Any]] = []

        async def mock_send(msg: dict[str, Any]) -> None:
            responses.append(msg)

        session._send_json = mock_send  # type: ignore

        # 1. Valid write inside workspace
        await session._handle_agent_request(
            req_id=1,
            method="fs/write_text_file",
            params={"path": "hello.py", "content": "print('hello')"},
        )
        written_file = workspace / "hello.py"
        assert written_file.exists()
        assert written_file.read_text(encoding="utf-8") == "print('hello')"
        assert len(responses) == 1
        assert responses[0]["result"]["ok"] is True


        # 2. Valid read inside workspace
        responses.clear()
        await session._handle_agent_request(
            req_id=2,
            method="fs/read_text_file",
            params={"path": "hello.py"},
        )
        assert len(responses) == 1
        assert responses[0]["result"]["content"] == "print('hello')"

        # 3. Path traversal attack blocked
        responses.clear()
        await session._handle_agent_request(
            req_id=3,
            method="fs/read_text_file",
            params={"path": "../secret.txt"},
        )
        assert len(responses) == 1
        assert "error" in responses[0]
        assert "Access denied" in responses[0]["error"]["message"]

    asyncio.run(go())


def test_run_acp_turn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify run_acp_turn sends token, answer, and done events over WsConnection."""
    async def go() -> None:
        monkeypatch.setattr(
            "backend.modules.acp.client.get_acp_session",
            lambda sid: AcpClientSession(command=FIXTURE_SERVER, cwd=tmp_path),
        )

        conn = MockWsConnection()
        await run_acp_turn(
            conn=conn,
            turn_id="turn-101",
            agent_id="claude-acp",
            prompt="Explain quantum physics",
        )

        event_types = [evt.get("event") for evt in conn.sent_events]
        assert "reasoning" in event_types
        assert "token" in event_types
        assert "answer" in event_types
        assert "done" in event_types

        answer_evt = next(e for e in conn.sent_events if e.get("event") == "answer")
        assert answer_evt["data"]["text"] == "Hello from ACP agent!"

    asyncio.run(go())



def test_roster_contains_claude_acp() -> None:
    """Verify claude-acp is registered in the agent roster."""
    agent = roster.get_agent("claude-acp")
    assert agent is not None
    assert agent.id == "claude-acp"
    assert "ACP" in agent.name


def test_orchestrator_routes_to_acp(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify run_agent_turn dispatches to run_acp_turn when agent_id is claude-acp."""
    from backend.modules.agent.orchestrator import run_agent_turn

    called_with: dict[str, Any] = {}

    async def mock_run_acp(conn: Any, turn_id: str, agent_id: str, prompt: str) -> None:
        called_with.update({"turn_id": turn_id, "agent_id": agent_id, "prompt": prompt})

    monkeypatch.setattr("backend.modules.acp.client.run_acp_turn", mock_run_acp)

    async def go() -> None:
        conn = MockWsConnection()
        await run_agent_turn(
            conn=conn,  # type: ignore
            turn_id="turn-acp-dispatch",
            prompt="Refactor this module",
            agent_id="claude-acp",
        )
        assert called_with == {
            "turn_id": "turn-acp-dispatch",
            "agent_id": "claude-acp",
            "prompt": "Refactor this module",
        }

    asyncio.run(go())


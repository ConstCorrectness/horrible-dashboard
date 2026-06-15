"""Provider abstraction: dialect normalization and the vLLM spawn manager."""

import asyncio
import json
from typing import Any

import httpx
import pytest

from backend.modules.agent import providers as P
from backend.modules.agent.vllm import VllmManager


def _client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- dialect: chat normalization ------------------------------------------------


def test_ollama_chat_normalizes_tool_calls() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        return httpx.Response(
            200,
            json={
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "open_pane", "arguments": {"id": "x"}}}
                    ],
                }
            },
        )

    async def go() -> P.ChatResult:
        async with _client(handler) as c:
            return await P.chat(c, P.provider_for("ollama"), "http://o", "m", [], [])

    result = asyncio.run(go())
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "open_pane"
    assert result.tool_calls[0].arguments == {"id": "x"}


def test_openai_chat_normalizes_stringified_arguments() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "function": {
                                        "name": "open_pane",
                                        # OpenAI stringifies arguments
                                        "arguments": '{"id": "y"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    async def go() -> P.ChatResult:
        async with _client(handler) as c:
            return await P.chat(c, P.provider_for("lmstudio"), "http://l", "m", [], [])

    result = asyncio.run(go())
    assert result.content == ""
    assert result.tool_calls[0].id == "call_1"
    assert result.tool_calls[0].arguments == {"id": "y"}


def test_tool_result_message_keys_per_dialect() -> None:
    call = P.ToolCall(id="abc", name="open_pane", arguments={})
    ollama = P.tool_result_message(P.provider_for("ollama"), call, {"ok": True})
    openai = P.tool_result_message(P.provider_for("vllm"), call, {"ok": True})
    assert ollama["tool_name"] == "open_pane" and "tool_call_id" not in ollama
    assert openai["tool_call_id"] == "abc" and "tool_name" not in openai


def test_openai_generate_stream_normalizes_sse() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = (
            'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=body)

    async def go() -> list[str]:
        out: list[str] = []
        async with _client(handler) as c:
            async for line in P.generate_stream(
                c, P.provider_for("lmstudio"), "http://l", "m", "hi"
            ):
                out.append(json.loads(line)["response"])
        return out

    assert asyncio.run(go()) == ["Hel", "lo"]


# --- vLLM spawn manager ---------------------------------------------------------


class _FakeProc:
    def __init__(self) -> None:
        self.pid = 4242
        self.stdout = iter(["loading…\n", "ready\n"])
        self._alive = True
        self.terminated = False

    def poll(self) -> int | None:
        return None if self._alive else 0

    def terminate(self) -> None:
        self.terminated = True
        self._alive = False

    def wait(self, timeout: float | None = None) -> int:
        self._alive = False
        return 0

    def kill(self) -> None:
        self._alive = False


def test_vllm_spawn_and_stop_with_injected_launcher() -> None:
    proc = _FakeProc()
    mgr = VllmManager(launcher=lambda cmd: proc)  # type: ignore[arg-type,return-value]

    status = mgr.spawn("google/gemma-2-2b-it", port=8001)
    assert status["running"] is True
    assert status["model"] == "google/gemma-2-2b-it"
    assert status["endpoint"] == "http://localhost:8001"
    assert status["pid"] == 4242
    # The launched command is recorded in the log ring buffer.
    assert any("api_server" in line for line in status["logs"])

    stopped = mgr.stop()
    assert proc.terminated is True
    assert stopped["running"] is False
    assert stopped["pid"] is None


def test_vllm_double_spawn_is_rejected() -> None:
    mgr = VllmManager(launcher=lambda cmd: _FakeProc())  # type: ignore[arg-type,return-value]
    mgr.spawn("m")
    with pytest.raises(RuntimeError, match="already running"):
        mgr.spawn("m")

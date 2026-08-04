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


def test_malformed_arguments_are_reported_not_silently_emptied() -> None:
    """A truncated or non-object payload used to collapse to `{}`, so the call ran
    with no arguments at all — `close_pane` with no instanceId — and the model saw a
    plain failure with no hint its JSON was to blame. Now it carries `arg_error`.

    An empty payload stays an ordinary no-arg call: plenty of tools take none.
    """
    ok, err = P._coerce_args('{"path": "/x"}')
    assert ok == {"path": "/x"} and err is None

    for empty in ("", "   ", None):
        args, err = P._coerce_args(empty)
        assert args == {} and err is None, f"{empty!r} should be a valid no-arg call"

    # Truncated mid-string — the classic small-model streaming failure.
    args, err = P._coerce_args('{"path": "/x/y')
    assert args == {} and err and "not valid JSON" in err

    # Valid JSON, wrong shape.
    args, err = P._coerce_args("[1, 2]")
    assert args == {} and err and "must be a JSON object" in err

    # And it survives onto the ToolCall the loop dispatches.
    call = P._parse_tool_calls(
        [{"id": "c1", "function": {"name": "close_pane", "arguments": '{"a": '}}]
    )[0]
    assert call.name == "close_pane"
    assert call.arguments == {}
    assert call.arg_error is not None


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


def test_ollama_chat_stream_hyperparameters() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        payload = json.loads(request.read())
        assert payload["options"]["temperature"] == 0.7
        assert payload["options"]["num_ctx"] == 8192
        assert payload["options"]["num_predict"] == 256
        assert payload["options"]["top_p"] == 0.95

        # Stream response back
        body = '{"message":{"role":"assistant","content":"ok"},"done":true}\n'
        return httpx.Response(200, text=body)

    async def go() -> P.ChatResult:
        async with _client(handler) as c:

            async def on_delta(r: str, c: str) -> None:
                pass

            return await P.chat_stream(
                c,
                P.provider_for("ollama"),
                "http://o",
                "m",
                [],
                [],
                on_delta,
                temperature=0.7,
                context_size=8192,
                max_tokens=256,
                top_p=0.95,
            )

    result = asyncio.run(go())
    assert result.content == "ok"


def test_openai_chat_stream_hyperparameters() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        payload = json.loads(request.read())
        assert payload["temperature"] == 0.7
        assert payload["max_tokens"] == 256
        assert payload["top_p"] == 0.95
        assert "context_size" not in payload
        assert "num_ctx" not in payload

        # Stream response back
        body = 'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, text=body)

    async def go() -> P.ChatResult:
        async with _client(handler) as c:

            async def on_delta(r: str, c: str) -> None:
                pass

            return await P.chat_stream(
                c,
                P.provider_for("lmstudio"),
                "http://o",
                "m",
                [],
                [],
                on_delta,
                temperature=0.7,
                max_tokens=256,
                top_p=0.95,
            )

    result = asyncio.run(go())
    assert result.content == "ok"


def test_thinking_extractor() -> None:
    deltas: list[tuple[str, str]] = []

    async def on_delta(reasoning: str, content: str) -> None:
        deltas.append((reasoning, content))

    async def go() -> tuple[str, str]:
        extractor = P.ThinkingExtractor(on_delta)
        await extractor.feed_content("Hello ")
        await extractor.feed_content("<th")
        await extractor.feed_content("ink>Let's plan: ")
        await extractor.feed_content("split the pane. </th")
        await extractor.feed_content("ink>I will split the pane.")
        return await extractor.flush()

    reasoning, content = asyncio.run(go())
    assert reasoning == "Let's plan: split the pane. "
    assert content == "Hello I will split the pane."

    non_empty_deltas = [d for d in deltas if d[0] or d[1]]
    assert non_empty_deltas == [
        ("", "Hello "),
        ("Let's plan: ", ""),
        ("split the pane. ", ""),
        ("", "I will split the pane."),
    ]

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


# --- system-message flattening ---------------------------------------------------


def test_leading_system_messages_are_merged_into_one() -> None:
    """Many chat templates raise `System message must be at the beginning.` on the
    second one — a 500 from the engine, so the turn is lost rather than degraded."""
    out = P.normalize_system_messages(
        [
            {"role": "system", "content": "You are the orchestrator"},
            {"role": "system", "content": "## Available skills"},
            {"role": "system", "content": "github guide"},
            {"role": "user", "content": "hi"},
        ]
    )
    assert [m["role"] for m in out] == ["system", "user"]
    assert out[0]["content"] == (
        "You are the orchestrator\n\n## Available skills\n\ngithub guide"
    )


def test_mid_conversation_system_becomes_a_user_message() -> None:
    """The forced-tool nudge answers the assistant turn before it, so it must keep
    its position — merging it into the preamble would send it before the failure."""
    out = P.normalize_system_messages(
        [
            {"role": "system", "content": "prompt"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "I'll open the pane."},
            {"role": "system", "content": "Emit the tool call."},
        ]
    )
    assert [m["role"] for m in out] == ["system", "user", "assistant", "user"]
    assert out[-1]["content"] == "Emit the tool call."


def test_flattening_is_a_no_op_on_an_already_valid_turn() -> None:
    messages = [
        {"role": "system", "content": "prompt"},
        {"role": "user", "content": "hi"},
    ]
    assert P.normalize_system_messages(messages) == messages


def test_a_turn_with_no_system_message_gains_none() -> None:
    """An empty system message must not become an empty leading block either."""
    assert P.normalize_system_messages([{"role": "user", "content": "hi"}]) == [
        {"role": "user", "content": "hi"}
    ]
    assert P.normalize_system_messages(
        [{"role": "system", "content": ""}, {"role": "user", "content": "hi"}]
    ) == [{"role": "user", "content": "hi"}]


def test_chat_sends_one_system_message_on_the_wire() -> None:
    """The seam is what the fix rests on: every dialect goes through `chat`, so no
    call site can reintroduce the shape the template rejects."""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["messages"] = json.loads(request.content)["messages"]
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    async def go() -> None:
        async with _client(handler) as c:
            await P.chat(
                c,
                P.provider_for("lmstudio"),
                "http://l",
                "qwen",
                [
                    {"role": "system", "content": "a"},
                    {"role": "system", "content": "b"},
                    {"role": "user", "content": "hi"},
                ],
                [],
            )

    asyncio.run(go())
    assert [m["role"] for m in seen["messages"]] == ["system", "user"]


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


# --- remote model listings -------------------------------------------------------


def test_remote_listing_is_cached_and_gets_its_own_timeout(monkeypatch) -> None:
    """NVIDIA NIM's `/v1/models` is fetched across the internet, but the probe
    client's budget is sized for a loopback port (2s). Inheriting it made a slow
    network indistinguishable from a provider with no models, because `_probe`
    swallows the timeout — so the request carries `REMOTE_TIMEOUT`, and the answer
    is cached so every `/agent/status` poll does not pay for it again."""
    P.invalidate_catalogs()
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"data": [{"id": "meta/llama-3.3-70b"}]})

    info = P.PROVIDERS["nim"]
    # NIM is hosted, so a listing is refused outright without a key — that check is
    # what stops the probe calling NVIDIA unauthenticated on every status poll.
    monkeypatch.setattr(P, "api_key_for", lambda i: "nvapi-test" if i is info else None)
    client = _client(handler)

    async def run() -> list[str]:
        first = await P.list_models(client, info, info.default_endpoint)
        second = await P.list_models(client, info, info.default_endpoint)
        assert first == second
        await client.aclose()
        return first

    assert asyncio.run(run()) == ["meta/llama-3.3-70b"]
    assert len(calls) == 1, "the second call must come from the cache"
    assert calls[0].extensions["timeout"]["read"] == P.REMOTE_TIMEOUT


def test_a_local_listing_is_never_cached() -> None:
    """`ollama pull` changes a local server's list, so a stale one is a worse lie
    than a slow one — only the remote providers are cached."""
    P.invalidate_catalogs()
    seen = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen
        seen += 1
        return httpx.Response(200, json={"data": [{"id": f"model-{seen}"}]})

    info = P.PROVIDERS["lmstudio"]
    client = _client(handler)

    async def run() -> None:
        assert await P.list_models(client, info, info.default_endpoint) == ["model-1"]
        assert await P.list_models(client, info, info.default_endpoint) == ["model-2"]
        await client.aclose()

    asyncio.run(run())
    assert seen == 2


def test_a_repointed_remote_provider_is_not_served_a_stale_list() -> None:
    """The cache is keyed by (kind, endpoint): a vLLM moved to another host must
    not be described by the list its predecessor returned."""
    P.invalidate_catalogs()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": request.url.host}]})

    info = P.PROVIDERS["vllm"]
    client = _client(handler)

    async def run() -> None:
        assert await P.list_models(client, info, "http://box-a:8001") == ["box-a"]
        assert await P.list_models(client, info, "http://box-b:8001") == ["box-b"]
        await client.aclose()

    asyncio.run(run())


def test_loopback_detection_covers_the_empty_endpoint() -> None:
    """`peer` without a lease has no endpoint by design; it must not be treated as
    a remote provider worth caching or warming."""
    assert P.is_loopback_endpoint("")
    assert P.is_loopback_endpoint("http://127.0.0.1:8080")
    assert P.is_loopback_endpoint("http://localhost:1234/v1")
    assert not P.is_loopback_endpoint("https://integrate.api.nvidia.com")


# --- strict tool names (NVIDIA NIM) ----------------------------------------------


def test_dotted_tool_names_are_translated_for_a_strict_provider() -> None:
    """NVIDIA validates function names against `a-zA-Z0-9_-` and rejects the whole
    request — `400 Validation: Function at index 7 has an invalid name:
    "agent.ask_peer"`, in 281ms, before any inference. Every tool here is dotted, so
    without this every turn on NIM failed identically."""
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "type": "function",
                                    "function": {
                                        "name": "library_search",
                                        "arguments": '{"q":"x"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    tools = [
        {"type": "function", "function": {"name": "library.search", "parameters": {}}},
        {"type": "function", "function": {"name": "agent.ask_peer", "parameters": {}}},
    ]
    client = _client(handler)

    async def run() -> Any:
        out = await P.chat(
            client, P.PROVIDERS["nim"], "https://nim.test", "m", [], tools
        )
        await client.aclose()
        return out

    result = asyncio.run(run())

    names = [t["function"]["name"] for t in sent[0]["tools"]]
    assert names == ["library_search", "agent_ask_peer"], "the wire must carry no dots"
    # ...and what comes back is handed on under the name the registry knows, or the
    # orchestrator reports a tool it never offered.
    assert [c.name for c in result.tool_calls] == ["library.search"]


def test_a_local_provider_keeps_its_dotted_names() -> None:
    """Only the strict providers are translated. LM Studio and llama.cpp accept dots,
    and rewriting for them would be a difference between what the model is told and
    what every other provider is told, for no reason."""
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})

    tools = [{"type": "function", "function": {"name": "library.search"}}]
    client = _client(handler)

    async def run() -> None:
        await P.chat(
            client, P.PROVIDERS["lmstudio"], "http://localhost:1234", "m", [], tools
        )
        await client.aclose()

    asyncio.run(run())
    assert sent[0]["tools"][0]["function"]["name"] == "library.search"


def test_history_tool_call_names_are_translated_too() -> None:
    """The `tools` array is not the only place a name appears. An assistant turn
    already in the transcript carries `tool_calls[].function.name`, and leaving a
    dotted one there is how this fix would half-work: the first round passes and the
    second, once a tool has been called, fails on the same validation."""
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    messages = [
        {"role": "user", "content": "search"},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "library.search", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "[]"},
    ]
    tools = [{"type": "function", "function": {"name": "library.search"}}]
    client = _client(handler)

    async def run() -> None:
        await P.chat(
            client, P.PROVIDERS["nim"], "https://nim.test", "m", messages, tools
        )
        await client.aclose()

    asyncio.run(run())
    assert (
        sent[0]["messages"][1]["tool_calls"][0]["function"]["name"] == "library_search"
    )
    # The caller's list is not mutated — it is the live transcript.
    assert messages[1]["tool_calls"][0]["function"]["name"] == "library.search"


def test_a_name_collision_is_disambiguated_not_merged() -> None:
    """`library.search` and `library_search` both sanitize to `library_search`.
    Merging them would route one tool's call to the other — a wrong action taken
    confidently, which is worse than an error."""
    tools = [
        {"type": "function", "function": {"name": "library.search"}},
        {"type": "function", "function": {"name": "library_search"}},
    ]
    out, _msgs, restore = P.sanitize_tool_names(tools, [])
    names = [t["function"]["name"] for t in out]
    assert len(set(names)) == 2, names
    assert {restore[n] for n in names} == {"library.search", "library_search"}


def test_a_provider_error_body_reaches_the_message() -> None:
    """httpx's own message is the status line and a link to MDN, so a provider that
    said exactly what was wrong is reported as an unexplained 400 — which is what
    made the NIM failure take a dig through the telemetry ring to diagnose."""
    body = (
        '{"error":{"message":"Validation: Function at index 7 has an invalid name: '
        '\\"agent.ask_peer\\".","type":"Bad Request","code":400}}'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text=body)

    client = _client(handler)

    async def run() -> str:
        try:
            await P.chat(client, P.PROVIDERS["nim"], "https://nim.test", "m", [], [])
        except httpx.HTTPStatusError as exc:
            return str(exc)
        finally:
            await client.aclose()
        return ""

    message = asyncio.run(run())
    assert "agent.ask_peer" in message
    assert "400" in message

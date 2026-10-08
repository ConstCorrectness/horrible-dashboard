"""The `browser` provider: rounds relayed to an app window's in-browser model."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from backend.modules.agent import browser_provider as B
from backend.modules.agent import providers as P


class Window:
    """The browser end of the socket: answers each generate_request with a script."""

    def __init__(self, script: list[tuple[str, dict[str, Any]]] | None = None) -> None:
        self.sent: list[dict[str, Any]] = []
        self.script = script or []

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)
        if data.get("event") == "generate_request":
            gen_id = data["data"]["genId"]

            async def reply() -> None:
                for event, payload in self.script:
                    await asyncio.sleep(0)
                    B.on_event(self, event, {"genId": gen_id, **payload})  # type: ignore[arg-type]

            asyncio.get_running_loop().create_task(reply())


@pytest.fixture(autouse=True)
def _clean() -> Any:
    B._manifests.clear()
    B._streams.clear()
    yield
    B._manifests.clear()
    B._streams.clear()


async def _sink_into(parts: list[tuple[str, str]]) -> Any:
    async def sink(reasoning: str, content: str) -> None:
        parts.append((reasoning, content))

    return sink


def _run_round(
    window: Window, **kwargs: Any
) -> tuple[P.ChatResult, list[tuple[str, str]]]:
    parts: list[tuple[str, str]] = []

    async def go() -> P.ChatResult:
        B.current_ws_conn.set(window)  # type: ignore[arg-type]
        sink = await _sink_into(parts)
        return await B.chat_stream(
            "onnx-community/Qwen3-0.6B-ONNX",
            kwargs.get("messages", [{"role": "user", "content": "hi"}]),
            kwargs.get("tools", []),
            sink,
        )

    return asyncio.run(go()), parts


# --- Hermes parsing -------------------------------------------------------------


def test_parse_hermes_extracts_calls_and_keeps_text() -> None:
    text = (
        'Let me look.\n<tool_call>\n{"name": "library.search", "arguments": {"q": "gpu"}}\n'
        "</tool_call>"
    )
    visible, calls = B.parse_hermes(text)
    assert visible == "Let me look."
    assert [(c.name, c.arguments, c.arg_error) for c in calls] == [
        ("library.search", {"q": "gpu"}, None)
    ]


def test_parse_hermes_accepts_unterminated_last_block_and_string_args() -> None:
    _, calls = B.parse_hermes(
        '<tool_call>{"name": "open_pane", "arguments": "{\\"id\\": \\"x\\"}"}'
    )
    assert calls[0].name == "open_pane"
    assert calls[0].arguments == {"id": "x"}


def test_parse_hermes_reports_bad_json_instead_of_running_it() -> None:
    _, calls = B.parse_hermes(
        '<tool_call>{"name": "files.delete", "arguments": {path: }}</tool_call>'
    )
    assert calls[0].name == "files.delete"
    assert calls[0].arguments == {}
    assert calls[0].arg_error and "not valid JSON" in calls[0].arg_error


def test_parse_hermes_leaves_plain_text_alone() -> None:
    assert B.parse_hermes("just an answer") == ("just an answer", [])


def test_hermes_filter_holds_markup_back_across_chunks() -> None:
    out: list[str] = []

    async def sink(_r: str, content: str) -> None:
        out.append(content)

    async def go() -> None:
        f = B.HermesFilter(sink)
        for chunk in [
            "Sure",
            " thing <tool",
            '_call>{"name": "x"',
            "}</tool_",
            "call> done",
            " <",
        ]:
            await f.feed(chunk)
        await f.flush()

    asyncio.run(go())
    assert "".join(out) == "Sure thing  done <"
    assert all("tool_call" not in piece for piece in out)


# --- the relay ------------------------------------------------------------------


def test_round_streams_answer_reasoning_and_tool_calls() -> None:
    window = Window(
        [
            ("generate_delta", {"text": "<think>check the library"}),
            ("generate_delta", {"text": "</think>On it."}),
            (
                "generate_delta",
                {
                    "text": '<tool_call>{"name": "library.search", "arguments": {"q": "webgpu"}}</tool_call>'
                },
            ),
            (
                "generate_done",
                {
                    "text": "ignored",
                    "usage": {"promptTokens": 120, "completionTokens": 30},
                },
            ),
        ]
    )
    result, parts = _run_round(
        window, tools=[{"type": "function", "function": {"name": "library.search"}}]
    )

    request = window.sent[0]
    assert request["event"] == "generate_request"
    assert request["data"]["model"] == "onnx-community/Qwen3-0.6B-ONNX"
    assert request["data"]["tools"][0]["function"]["name"] == "library.search"

    assert result.content == "On it."
    assert [c.name for c in result.tool_calls] == ["library.search"]
    assert (
        result.assistant_message["tool_calls"][0]["function"]["arguments"]
        == '{"q": "webgpu"}'
    )
    assert result.assistant_message["reasoning_content"] == "check the library"
    assert result.usage == P.Usage(tokens_in=120, tokens_out=30)
    streamed = "".join(c for _, c in parts)
    assert streamed == "On it."
    assert "".join(r for r, _ in parts) == "check the library"


def test_done_without_deltas_uses_the_final_text() -> None:
    window = Window([("generate_done", {"text": "Hello", "usage": {}})])
    result, _ = _run_round(window)
    assert result.content == "Hello"
    assert result.usage == P.Usage()


def test_window_error_raises_a_provider_error() -> None:
    window = Window([("generate_error", {"message": "out of GPU memory"})])
    with pytest.raises(P.ProviderStreamError, match="out of GPU memory"):
        _run_round(window)
    assert B._streams == {}


def test_no_window_means_unavailable_not_a_hang() -> None:
    async def go() -> None:
        async def sink(_r: str, _c: str) -> None:
            return None

        await B.chat_stream("m", [], [], sink)

    # A fresh context: no socket set.
    with pytest.raises(B.BrowserProviderUnavailable, match="open app window"):
        asyncio.run(go())


def test_a_window_without_webgpu_is_refused() -> None:
    window = Window()
    B.record_manifest(window, {"available": False, "reason": "no navigator.gpu"})  # type: ignore[arg-type]
    with pytest.raises(B.BrowserProviderUnavailable, match="no navigator.gpu"):
        _run_round(window)
    assert window.sent == []


def test_another_window_cannot_answer() -> None:
    other = Window()

    class Hijacker(Window):
        async def send_json(self, data: dict[str, Any]) -> None:
            self.sent.append(data)
            gen_id = data["data"]["genId"]
            B.on_event(other, "generate_done", {"genId": gen_id, "text": "pwned"})  # type: ignore[arg-type]
            B.on_event(self, "generate_done", {"genId": gen_id, "text": "mine"})  # type: ignore[arg-type]

    result, _ = _run_round(Hijacker())
    assert result.content == "mine"


def test_cancel_tells_the_window_to_stop() -> None:
    window = Window()  # never answers

    async def go() -> None:
        B.current_ws_conn.set(window)  # type: ignore[arg-type]

        async def sink(_r: str, _c: str) -> None:
            return None

        task = asyncio.create_task(B.chat_stream("m", [], [], sink))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    assert [m["event"] for m in window.sent] == ["generate_request", "generate_cancel"]
    assert B._streams == {}


def test_closing_the_window_fails_its_round() -> None:
    window = Window()

    async def go() -> None:
        B.current_ws_conn.set(window)  # type: ignore[arg-type]

        async def sink(_r: str, _c: str) -> None:
            return None

        task = asyncio.create_task(B.chat_stream("m", [], [], sink))
        await asyncio.sleep(0.01)
        B.drop(window)  # type: ignore[arg-type]
        await task

    with pytest.raises(P.ProviderStreamError, match="window closed"):
        asyncio.run(go())


def test_wire_messages_shapes_history_for_a_chat_template() -> None:
    wired = B.wire_messages(
        [
            {"role": "system", "content": [{"type": "text", "text": "be brief"}]},
            {
                "role": "assistant",
                "content": None,
                "reasoning_content": "dropped",
                "tool_calls": [
                    {
                        "id": "1",
                        "type": "function",
                        "function": {"name": "a.b", "arguments": '{"x": 1}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "1", "name": "a.b", "content": "ok"},
        ]
    )
    assert wired == [
        {"role": "system", "content": "be brief"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"type": "function", "function": {"name": "a.b", "arguments": {"x": 1}}}
            ],
        },
        {"role": "tool", "content": "ok", "name": "a.b"},
    ]


# --- reachability ----------------------------------------------------------------


def test_models_come_from_window_manifests() -> None:
    with pytest.raises(B.BrowserProviderUnavailable, match="No app window"):
        B.available_models()
    w = Window()
    B.record_manifest(w, {"available": True, "models": ["a/b", "c/d"], "loaded": "c/d"})  # type: ignore[arg-type]
    assert B.available_models() == ["c/d", "a/b"]

    async def probe() -> list[str]:
        return await P.list_models(None, P.PROVIDERS["browser"], "")  # type: ignore[arg-type]

    assert asyncio.run(probe()) == ["c/d", "a/b"]
    B.drop(w)  # type: ignore[arg-type]
    with pytest.raises(B.BrowserProviderUnavailable):
        B.available_models()


def test_generate_relays_through_the_window() -> None:
    window = Window(
        [("generate_delta", {"text": "four"}), ("generate_done", {"usage": {}})]
    )

    async def go() -> str:
        B.current_ws_conn.set(window)  # type: ignore[arg-type]
        return await P.generate(None, P.PROVIDERS["browser"], "", "m", "2+2?")  # type: ignore[arg-type]

    assert asyncio.run(go()) == "four"

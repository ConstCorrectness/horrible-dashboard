"""Thinking models under the voice agent's small reply budget.

Reasoning tokens count against ``max_tokens``, so a thinking model at its default
(Gemma 4 on LM Studio) spent the whole 180-token cap reasoning and returned an empty
``content`` -- which the pane rendered as an agent that chose to stay quiet.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from backend.modules.agent import providers as P
from backend.modules.clubhouse import voice as V
from backend.modules.clubhouse import voice_runtime as R


def _send(info: P.ProviderInfo, think: bool | None) -> dict:
    """Run one `chat` against a mock transport and return the JSON it sent."""
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        if request.url.path == "/api/chat":
            return httpx.Response(200, json={"message": {"content": "hi"}})
        return httpx.Response(
            200, json={"choices": [{"message": {"role": "assistant", "content": "hi"}}]}
        )

    async def go() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await P.chat(
                client,
                info,
                "http://model.test",
                "m",
                [{"role": "user", "content": "x"}],
                [],
                think=think,
            )

    asyncio.run(go())
    return sent[0]


def test_lmstudio_turns_thinking_off_with_reasoning_effort_none():
    """The only knob LM Studio honours -- measured: `chat_template_kwargs` and the
    nested `reasoning.effort` form were both ignored and still returned nothing."""
    body = _send(P.PROVIDERS["lmstudio"], think=False)
    assert body["reasoning_effort"] == "none"


def test_ollama_turns_thinking_off_with_think_false():
    body = _send(P.PROVIDERS["ollama"], think=False)
    assert body["think"] is False


def test_the_default_leaves_the_model_alone():
    assert "reasoning_effort" not in _send(P.PROVIDERS["lmstudio"], think=None)
    assert "think" not in _send(P.PROVIDERS["ollama"], think=None)


@pytest.fixture
def stub_provider(tmp_path, monkeypatch):
    """Point `generate_reply` at a fake provider and keep every call's kwargs."""
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    calls: list[dict] = []
    reply: dict = {"content": "Hello room.", "reasoning_content": ""}

    async def fake_chat(client, info, endpoint, model, messages, tools, **kw):
        calls.append(kw)
        msg = {"role": "assistant", **reply}
        return P.ChatResult(
            assistant_message=msg, tool_calls=[], content=msg["content"]
        )

    monkeypatch.setattr(P, "chat", fake_chat)
    monkeypatch.setattr(
        "backend.modules.agent.routes._load_config",
        lambda: SimpleNamespace(provider="lmstudio", model="google/gemma-4-e2b"),
    )
    monkeypatch.setattr(
        "backend.modules.agent.routes._endpoint_for", lambda info, cfg: "http://x"
    )
    return SimpleNamespace(calls=calls, reply=reply)


def test_the_voice_agent_asks_for_no_thinking(stub_provider):
    out = asyncio.run(
        R.generate_reply([{"role": "user", "content": "hi"}], V.VoiceConfig())
    )
    assert out.content == "Hello room."
    assert stub_provider.calls[0]["think"] is False


def test_a_reply_that_is_all_reasoning_is_an_error_not_silence(stub_provider):
    """A model that thinks regardless of the switch must be reported, not rendered
    as an agent that decided to say nothing."""
    stub_provider.reply.update(content="", reasoning_content="Thinking Process: 1.")
    with pytest.raises(RuntimeError, match="thinking"):
        asyncio.run(
            R.generate_reply([{"role": "user", "content": "hi"}], V.VoiceConfig())
        )

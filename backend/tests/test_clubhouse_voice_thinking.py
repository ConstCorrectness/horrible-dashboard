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


def _strict_template_chat(monkeypatch, seen: list[list[dict]]):
    """A provider whose chat template refuses ``user, user`` the way Mistral's does."""

    async def strict_chat(client, info, endpoint, model, messages, tools, **kw):
        seen.append(messages)
        roles = [m["role"] for m in messages if m["role"] != "system"]
        if any(a == b for a, b in zip(roles, roles[1:])):
            request = httpx.Request("POST", "http://x/v1/chat/completions")
            raise httpx.HTTPStatusError(
                "400: Conversation roles must alternate user/assistant/user/assistant",
                request=request,
                response=httpx.Response(
                    400,
                    request=request,
                    text="Error rendering prompt with jinja template: roles must alternate",
                ),
            )
        return P.ChatResult(
            assistant_message={"role": "assistant", "content": "ok"},
            tool_calls=[],
            content="ok",
        )

    monkeypatch.setattr(P, "chat", strict_chat)
    monkeypatch.setattr(R, "_FOLD_REQUIRED", set())


def _two_user_lines() -> list[dict]:
    return [
        {"role": "system", "content": "persona"},
        {"role": "user", "content": "Ada: first"},
        {"role": "user", "content": "Grace: second"},
    ]


def test_a_strict_template_gets_the_same_conversation_folded(
    stub_provider, monkeypatch
):
    """One message per utterance is the default; a model that cannot take it costs a
    single rejected request, not a lost turn."""
    seen: list[list[dict]] = []
    _strict_template_chat(monkeypatch, seen)

    out = asyncio.run(R.generate_reply(_two_user_lines(), V.VoiceConfig()))

    assert out.content == "ok"
    assert len(seen) == 2  # the rejection, then the folded resend
    assert [m["role"] for m in seen[0]] == ["system", "user", "user"]
    assert [m["role"] for m in seen[1]] == ["system", "user"]
    assert "Ada: first" in seen[1][1]["content"]
    assert "Grace: second" in seen[1][1]["content"]


def test_a_model_that_rejected_the_shape_is_folded_up_front_next_time(
    stub_provider, monkeypatch
):
    """A live room cannot spend a failed round trip on every utterance."""
    seen: list[list[dict]] = []
    _strict_template_chat(monkeypatch, seen)

    asyncio.run(R.generate_reply(_two_user_lines(), V.VoiceConfig()))
    seen.clear()
    asyncio.run(R.generate_reply(_two_user_lines(), V.VoiceConfig()))

    assert len(seen) == 1
    assert [m["role"] for m in seen[0]] == ["system", "user"]


def test_a_lenient_template_is_sent_the_conversation_as_it_is(
    stub_provider, monkeypatch
):
    monkeypatch.setattr(R, "_FOLD_REQUIRED", set())
    sent: list[list[dict]] = []

    async def lenient_chat(client, info, endpoint, model, messages, tools, **kw):
        sent.append(messages)
        return P.ChatResult(
            assistant_message={"role": "assistant", "content": "ok"},
            tool_calls=[],
            content="ok",
        )

    monkeypatch.setattr(P, "chat", lenient_chat)
    asyncio.run(R.generate_reply(_two_user_lines(), V.VoiceConfig()))
    assert [m["role"] for m in sent[0]] == ["system", "user", "user"]


def test_an_unrelated_provider_error_is_not_mistaken_for_a_role_problem(
    stub_provider, monkeypatch
):
    monkeypatch.setattr(R, "_FOLD_REQUIRED", set())

    async def failing(client, info, endpoint, model, messages, tools, **kw):
        request = httpx.Request("POST", "http://x/v1/chat/completions")
        raise httpx.HTTPStatusError(
            "400: model not loaded",
            request=request,
            response=httpx.Response(400, request=request, text="model not loaded"),
        )

    monkeypatch.setattr(P, "chat", failing)
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(R.generate_reply(_two_user_lines(), V.VoiceConfig()))
    assert not R._FOLD_REQUIRED


def test_a_reply_that_is_all_reasoning_is_an_error_not_silence(stub_provider):
    """A model that thinks regardless of the switch must be reported, not rendered
    as an agent that decided to say nothing."""
    stub_provider.reply.update(content="", reasoning_content="Thinking Process: 1.")
    with pytest.raises(RuntimeError, match="thinking"):
        asyncio.run(
            R.generate_reply([{"role": "user", "content": "hi"}], V.VoiceConfig())
        )

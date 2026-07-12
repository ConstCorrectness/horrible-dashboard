"""Model-in-loadout: the key store never leaks values, and the games model client
speaks the right dialect per provider (including the anthropic translation)."""

from __future__ import annotations

import asyncio
import json

import httpx

from backend.modules.games import model_client, model_config
from backend.modules.games.model_config import ModelConfig


def test_key_store_lists_names_only(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    model_config.set_key("anthropic-main", "sk-secret")
    assert model_config.list_key_names() == ["anthropic-main"]
    assert model_config.get_key("anthropic-main") == "sk-secret"
    model_config.delete_key("anthropic-main")
    assert model_config.list_key_names() == []


def test_model_label_and_locality() -> None:
    assert (
        model_config.model_label(ModelConfig(provider="ollama", model="llama3"))
        == "ollama/llama3 (local)"
    )
    cloud = ModelConfig(provider="anthropic", model="claude-sonnet-5")
    assert model_config.model_label(cloud) == "anthropic/claude-sonnet-5"
    assert not model_config.is_local(cloud)
    local_openai = ModelConfig(
        provider="openai", model="qwen", endpoint="http://localhost:1234"
    )
    assert model_config.is_local(local_openai)
    assert model_config.parse_model({"provider": "openai"}) is None  # no model name


def _client_with(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_openai_dialect_request_shape(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    model_config.set_key("oai", "sk-test")
    config = ModelConfig(provider="openai", model="gpt-x", api_key_name="oai")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "hi",
                            "tool_calls": [],
                        }
                    }
                ]
            },
        )

    async def go() -> None:
        headers = model_client.headers_for(config)
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), headers=headers
        ) as client:
            result = await model_client.chat(client, config, [], [])
            assert result.content == "hi"

    asyncio.run(go())
    assert seen["url"] == "https://api.openai.com/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-test"


def test_anthropic_dialect_translation(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    model_config.set_key("ant", "sk-ant")
    config = ModelConfig(provider="anthropic", model="claude-x", api_key_name="ant")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("x-api-key")
        seen["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "content": [
                    {"type": "text", "text": "let me check"},
                    {
                        "type": "tool_use",
                        "id": "tu_1",
                        "name": "game.chooseAction",
                        "input": {"action_id": "4"},
                    },
                ]
            },
        )

    messages = [
        {"role": "system", "content": "you are playing"},
        {"role": "user", "content": "your move"},
        {
            "role": "assistant",
            "content": "scan first",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "scan", "arguments": '{"depth": 2}'},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "c1",
            "name": "scan",
            "content": '{"seen": true}',
        },
    ]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "game.chooseAction",
                "description": "commit",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    async def go() -> None:
        headers = model_client.headers_for(config)
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), headers=headers
        ) as client:
            result = await model_client.chat(client, config, messages, tools)
            # Normalized back to the ChatResult shape the policy loop expects.
            assert result.content == "let me check"
            assert result.tool_calls[0].name == "game.chooseAction"
            assert result.tool_calls[0].arguments == {"action_id": "4"}
            # The assistant message stays OpenAI-shaped for the next round.
            assert result.assistant_message["tool_calls"][0]["function"]["name"] == (
                "game.chooseAction"
            )

    asyncio.run(go())
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["key"] == "sk-ant"
    payload = seen["payload"]
    assert payload["system"] == "you are playing"
    assert payload["tools"][0]["input_schema"]["type"] == "object"
    # assistant tool_calls became tool_use blocks; the tool reply a tool_result.
    roles = [m["role"] for m in payload["messages"]]
    assert roles == ["user", "assistant", "user"]
    assistant_blocks = payload["messages"][1]["content"]
    assert any(
        b["type"] == "tool_use" and b["input"] == {"depth": 2} for b in assistant_blocks
    )
    assert payload["messages"][2]["content"][0]["type"] == "tool_result"

"""Chat client for a loadout's `ModelConfig` — the games-side model dialects.

Ollama and OpenAI-compatible endpoints reuse the agent module's normalization
(`backend.modules.agent.providers.chat`); **Anthropic** is implemented here by
translating the OpenAI-style messages/tools the `AgentPolicy` loop speaks into
the Messages API and normalizing `tool_use` blocks back into a `ChatResult`.
The running message list stays OpenAI-shaped throughout — translation happens
per request, so the policy loop needs no per-provider branches.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from backend.modules.agent import providers as P
from backend.modules.games.model_config import ModelConfig, get_key

ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 2048


def headers_for(config: ModelConfig) -> dict[str, str]:
    """Auth headers for the whole client session (resolved from the key store)."""
    key = get_key(config.api_key_name)
    if config.provider == "anthropic":
        return (
            {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}
            if key
            else {"anthropic-version": ANTHROPIC_VERSION}
        )
    if config.provider == "openai" and key:
        return {"Authorization": f"Bearer {key}"}
    return {}


async def chat(
    client: httpx.AsyncClient,
    config: ModelConfig,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> P.ChatResult:
    """One non-streaming tool-calling round against the loadout's model."""
    if config.provider == "anthropic":
        return await _anthropic_chat(client, config, messages, tools)
    # `lmstudio` carries the OpenAI dialect in the provider table; ollama is native.
    info = P.provider_for("ollama" if config.provider == "ollama" else "lmstudio")
    return await P.chat(
        client, info, config.resolved_endpoint(), config.model, messages, tools
    )


# ---- anthropic dialect ---------------------------------------------------------


def _anthropic_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for tool in tools:
        fn = tool.get("function") or {}
        out.append(
            {
                "name": fn.get("name") or "",
                "description": fn.get("description") or "",
                "input_schema": fn.get("parameters")
                or {"type": "object", "properties": {}},
            }
        )
    return out


def _anthropic_messages(
    messages: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """OpenAI-style running messages → (system, anthropic messages)."""
    system_parts: list[str] = []
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        if role == "system":
            system_parts.append(str(msg.get("content") or ""))
        elif role == "user":
            out.append({"role": "user", "content": str(msg.get("content") or "")})
        elif role == "assistant":
            blocks: list[dict[str, Any]] = []
            if msg.get("content"):
                blocks.append({"type": "text", "text": str(msg["content"])})
            for call in msg.get("tool_calls") or []:
                fn = call.get("function") or {}
                raw_args = fn.get("arguments")
                if isinstance(raw_args, str):
                    try:
                        raw_args = json.loads(raw_args)
                    except ValueError:
                        raw_args = {}
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": str(call.get("id") or fn.get("name") or "call"),
                        "name": fn.get("name") or "",
                        "input": raw_args if isinstance(raw_args, dict) else {},
                    }
                )
            if blocks:
                out.append({"role": "assistant", "content": blocks})
        elif role == "tool":
            out.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": str(
                                msg.get("tool_call_id") or msg.get("name") or "call"
                            ),
                            "content": str(msg.get("content") or ""),
                        }
                    ],
                }
            )
    return "\n\n".join(p for p in system_parts if p), out


async def _anthropic_chat(
    client: httpx.AsyncClient,
    config: ModelConfig,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> P.ChatResult:
    system, converted = _anthropic_messages(messages)
    payload: dict[str, Any] = {
        "model": config.model,
        "max_tokens": DEFAULT_MAX_TOKENS,
        "messages": converted,
    }
    if system:
        payload["system"] = system
    if tools:
        payload["tools"] = _anthropic_tools(tools)
    res = await client.post(f"{config.resolved_endpoint()}/v1/messages", json=payload)
    res.raise_for_status()
    data = res.json()

    text_parts: list[str] = []
    tool_calls: list[P.ToolCall] = []
    openai_calls: list[dict[str, Any]] = []
    for block in data.get("content") or []:
        if block.get("type") == "text":
            text_parts.append(str(block.get("text") or ""))
        elif block.get("type") == "tool_use":
            call_id = str(block.get("id") or block.get("name") or "call")
            name = str(block.get("name") or "")
            args = block.get("input") if isinstance(block.get("input"), dict) else {}
            tool_calls.append(P.ToolCall(id=call_id, name=name, arguments=args))
            openai_calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
            )
    content = "".join(text_parts)
    # The assistant message stays OpenAI-shaped so the next round re-translates it.
    assistant: dict[str, Any] = {"role": "assistant", "content": content}
    if openai_calls:
        assistant["tool_calls"] = openai_calls
    return P.ChatResult(
        assistant_message=assistant, tool_calls=tool_calls, content=content
    )

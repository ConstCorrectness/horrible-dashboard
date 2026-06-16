"""Local-model provider abstraction for the agent.

Two API dialects cover every provider we support:

- **ollama** — Ollama's native API (``/api/tags``, ``/api/chat``,
  ``/api/generate``, ``/api/pull``).
- **openai** — the OpenAI-compatible API (``/v1/models``,
  ``/v1/chat/completions``) served by **LM Studio** and **vLLM**.

`PROVIDERS` maps a provider *kind* to its metadata; the dialect functions below
normalize the two wire formats so the orchestrator and routes stay
provider-agnostic. See docs/modules/agent-chat.md.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

from backend.modules.telemetry.instrument import tee_stream


@dataclass(frozen=True)
class ProviderInfo:
    kind: str  # stable id stored in AgentConfig
    label: str  # human label for the UI
    dialect: str  # "ollama" | "openai"
    default_endpoint: str
    install_url: str
    can_pull: bool  # supports `/api/pull`-style model download
    can_spawn: bool  # the backend can launch a server process for it


PROVIDERS: dict[str, ProviderInfo] = {
    "ollama": ProviderInfo(
        kind="ollama",
        label="Ollama",
        dialect="ollama",
        default_endpoint="http://localhost:11434",
        install_url="https://ollama.com",
        can_pull=True,
        can_spawn=False,
    ),
    "lmstudio": ProviderInfo(
        kind="lmstudio",
        label="LM Studio",
        dialect="openai",
        default_endpoint="http://localhost:1234",
        install_url="https://lmstudio.ai",
        can_pull=False,
        can_spawn=False,
    ),
    "vllm": ProviderInfo(
        kind="vllm",
        label="vLLM",
        dialect="openai",
        default_endpoint="http://localhost:8001",
        install_url="https://docs.vllm.ai",
        can_pull=False,
        can_spawn=True,
    ),
}

DEFAULT_PROVIDER = "ollama"


def provider_for(kind: str | None) -> ProviderInfo:
    """The provider for a kind, falling back to the default for unknown/None."""
    return PROVIDERS.get(kind or DEFAULT_PROVIDER, PROVIDERS[DEFAULT_PROVIDER])


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ChatResult:
    """One assistant turn, normalized across dialects."""

    assistant_message: dict[str, Any]  # appended verbatim to the running messages
    tool_calls: list[ToolCall]
    content: str


def _coerce_args(raw: Any) -> dict[str, Any]:
    """Tool-call arguments arrive as a dict (Ollama) or a JSON string (OpenAI,
    and some Ollama models). Normalize to a dict; bad payloads become ``{}``."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _parse_tool_calls(raw_calls: list[dict[str, Any]]) -> list[ToolCall]:
    return [
        ToolCall(
            id=str(c.get("id") or i),
            name=c.get("function", {}).get("name", ""),
            arguments=_coerce_args(c.get("function", {}).get("arguments")),
        )
        for i, c in enumerate(raw_calls)
    ]


async def list_models(
    client: httpx.AsyncClient, info: ProviderInfo, endpoint: str
) -> list[str]:
    """Reachability probe doubling as a model list. Raises httpx.HTTPError when
    the provider is down."""
    if info.dialect == "ollama":
        res = await client.get(f"{endpoint}/api/tags")
        res.raise_for_status()
        return [m["name"] for m in res.json().get("models", [])]
    res = await client.get(f"{endpoint}/v1/models")
    res.raise_for_status()
    return [m["id"] for m in res.json().get("data", [])]


async def chat(
    client: httpx.AsyncClient,
    info: ProviderInfo,
    endpoint: str,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> ChatResult:
    """One non-streaming tool-calling round, normalized to a `ChatResult`."""
    if info.dialect == "ollama":
        res = await client.post(
            f"{endpoint}/api/chat",
            json={
                "model": model,
                "messages": messages,
                "tools": tools,
                "stream": False,
            },
        )
        res.raise_for_status()
        msg = res.json().get("message", {})
    else:
        res = await client.post(
            f"{endpoint}/v1/chat/completions",
            json={
                "model": model,
                "messages": messages,
                "tools": tools,
                "stream": False,
            },
        )
        res.raise_for_status()
        choices = res.json().get("choices") or [{}]
        msg = choices[0].get("message", {})
    return ChatResult(
        assistant_message=msg,
        tool_calls=_parse_tool_calls(msg.get("tool_calls") or []),
        content=msg.get("content") or "",
    )


def tool_result_message(
    info: ProviderInfo, call: ToolCall, result: Any
) -> dict[str, Any]:
    """Format a tool result for appending to the running messages. Ollama keys on
    ``tool_name``; the OpenAI dialect keys on ``tool_call_id``."""
    content = json.dumps(result)
    if info.dialect == "ollama":
        return {"role": "tool", "content": content, "tool_name": call.name}
    return {"role": "tool", "content": content, "tool_call_id": call.id}


async def generate(
    client: httpx.AsyncClient,
    info: ProviderInfo,
    endpoint: str,
    model: str,
    prompt: str,
    max_tokens: int = 64,
) -> str:
    """One non-streaming, short completion (for editor autosuggest), normalized
    across dialects to a plain string. Token-capped to keep latency low."""
    if info.dialect == "ollama":
        res = await client.post(
            f"{endpoint}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": max_tokens},
            },
        )
        res.raise_for_status()
        return res.json().get("response", "")
    res = await client.post(
        f"{endpoint}/v1/chat/completions",
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "max_tokens": max_tokens,
        },
    )
    res.raise_for_status()
    choices = res.json().get("choices") or [{}]
    return choices[0].get("message", {}).get("content") or ""


async def generate_stream(
    client: httpx.AsyncClient,
    info: ProviderInfo,
    endpoint: str,
    model: str,
    prompt: str,
) -> AsyncIterator[str]:
    """Stream a one-shot completion as NDJSON ``{"response": <token>}`` lines,
    normalizing Ollama's ``/api/generate`` and the OpenAI ``/v1/chat/completions``
    SSE stream into the one shape the frontend already understands."""
    if info.dialect == "ollama":
        async with client.stream(
            "POST",
            f"{endpoint}/api/generate",
            json={"model": model, "prompt": prompt, "stream": True},
        ) as res:
            res.raise_for_status()
            async for line in tee_stream(res, res.aiter_lines()):
                if not line:
                    continue
                obj = json.loads(line)
                token = obj.get("response", "")
                if token:
                    yield json.dumps({"response": token}) + "\n"
                if obj.get("done"):
                    return
        return
    async with client.stream(
        "POST",
        f"{endpoint}/v1/chat/completions",
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
        },
    ) as res:
        res.raise_for_status()
        async for line in tee_stream(res, res.aiter_lines()):
            if not line or not line.startswith("data:"):
                continue
            data = line[len("data:") :].strip()
            if data == "[DONE]":
                return
            delta = (json.loads(data).get("choices") or [{}])[0].get("delta", {})
            token = delta.get("content") or ""
            if token:
                yield json.dumps({"response": token}) + "\n"

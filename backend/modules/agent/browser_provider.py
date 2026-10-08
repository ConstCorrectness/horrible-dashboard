"""The `browser` provider: a model running in an open app window, on its GPU.

The model lives in the frontend (`packages/webml`, WebGPU), but the agent loop lives
here — so a round is *relayed*: the backend sends ``generate_request`` down the
window's own `/ws` socket, the window streams ``generate_delta`` back and finishes
with ``generate_done`` (or ``generate_error``), and this module assembles that into
the same `ChatResult` every other dialect returns. The loop, permissions, tool
gating and trajectory capture are unchanged; only the model moved.

Which window? The one whose socket started the turn. `current_ws_conn` is set once
per socket (in `backend/app.py`) and every task spawned from that socket inherits it,
so a chat turn, its sub-agents and a flow's agent nodes all reach their own window.
Callers with no socket — evals, games, `/agent/complete`, synthetic data — have none,
and get `BrowserProviderUnavailable` instead of a hang.

Tool calls: browser models write them as text. The one format parsed is **Hermes**
(``<tool_call>{"name": …, "arguments": …}</tool_call>``, Qwen3/SmolLM3); the markup is
held back from the streamed answer and turned into `ToolCall`s at the end. A model
that writes no such block simply answers.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

from backend.modules.agent.providers import (
    ChatResult,
    DeltaSink,
    ProviderStreamError,
    StreamStalled,
    ThinkingExtractor,
    ToolCall,
    Usage,
    _tool_call,
)

if TYPE_CHECKING:
    from backend.modules.ws import WsConnection

#: The socket of the window this task belongs to. Set per `/ws` connection.
current_ws_conn: ContextVar[WsConnection | None] = ContextVar(
    "current_ws_conn", default=None
)

#: Before the first token the window may be downloading and compiling the model
#: (a gigabyte over a home connection), so the first wait is long. After that a
#: silent window is a stuck one.
FIRST_EVENT_S = 600.0
IDLE_S = 120.0


class BrowserProviderUnavailable(httpx.HTTPError):
    """No window can run this round. An `httpx.HTTPError` so the status probe and
    every caller that already handles "provider down" handle this the same way."""


@dataclass
class WindowManifest:
    """What a window reported about its in-browser engine (`webml_manifest`)."""

    available: bool
    reason: str = ""
    #: Model ids the window can run without a download, loaded one first.
    models: list[str] = field(default_factory=list)
    loaded: str | None = None
    at: float = field(default_factory=time.monotonic)


_manifests: dict[int, tuple[WsConnection, WindowManifest]] = {}
_streams: dict[str, tuple[WsConnection, asyncio.Queue[dict[str, Any]]]] = {}


def record_manifest(conn: WsConnection, data: dict[str, Any]) -> None:
    models = [str(m) for m in data.get("models") or [] if isinstance(m, str) and m]
    loaded = data.get("loaded")
    _manifests[id(conn)] = (
        conn,
        WindowManifest(
            available=bool(data.get("available")),
            reason=str(data.get("reason") or ""),
            models=models,
            loaded=str(loaded) if isinstance(loaded, str) and loaded else None,
        ),
    )


def available_models() -> list[str]:
    """Every model some open window can run, loaded ones first. Raises
    `BrowserProviderUnavailable` when no window has WebGPU — that is what makes the
    status probe report the provider unreachable."""
    ready = sorted(
        (m for _, m in _manifests.values() if m.available),
        key=lambda m: -m.at,
    )
    if not ready:
        reasons = {m.reason for _, m in _manifests.values() if m.reason}
        detail = f" ({'; '.join(sorted(reasons))})" if reasons else ""
        raise BrowserProviderUnavailable(
            f"No open app window has WebGPU{detail}"
            if _manifests
            else "No app window is open"
        )
    out: list[str] = []
    for m in ready:
        for model in ([m.loaded] if m.loaded else []) + m.models:
            if model not in out:
                out.append(model)
    return out


def drop(conn: WsConnection) -> None:
    """The window went away: forget its manifest and fail its live generations."""
    _manifests.pop(id(conn), None)
    for owner, queue in list(_streams.values()):
        if owner is conn:
            queue.put_nowait(
                {"event": "generate_error", "message": "the app window closed"}
            )


def on_event(conn: WsConnection, event: str, data: dict[str, Any]) -> None:
    """Route a window's `generate_*` reply to the round waiting for it."""
    entry = _streams.get(str(data.get("genId", "")))
    # Only the window that was asked may answer — a generation id is not a secret
    # on a shared socket hub.
    if entry is None or entry[0] is not conn:
        return
    entry[1].put_nowait({**data, "event": event})


# --- Hermes tool calls --------------------------------------------------------

_OPEN = "<tool_call>"
_CLOSE = "</tool_call>"
_BLOCK = re.compile(r"<tool_call>\s*(.*?)\s*(?:</tool_call>|$)", re.DOTALL)
_NAME = re.compile(r'"name"\s*:\s*"([^"]+)"')


def parse_hermes(text: str) -> tuple[str, list[ToolCall]]:
    """Split a reply into its visible text and its Hermes tool calls.

    An unterminated final block (the model hit EOS before ``</tool_call>``) still
    counts — small models do it often, and the JSON inside is usually whole. A block
    that is not a JSON object becomes a call carrying `arg_error`, so the loop tells
    the model what was wrong instead of running something with no arguments.
    """
    calls: list[ToolCall] = []
    for i, match in enumerate(_BLOCK.finditer(text)):
        body = match.group(1).strip()
        call_id = f"call_{i}"
        try:
            obj = json.loads(body)
        except ValueError as exc:
            name = _NAME.search(body)
            calls.append(
                ToolCall(
                    id=call_id,
                    name=name.group(1) if name else "",
                    arguments={},
                    arg_error=f"tool call was not valid JSON ({exc}); received: {body[:200]!r}",
                )
            )
            continue
        if not isinstance(obj, dict) or not isinstance(obj.get("name"), str):
            calls.append(
                ToolCall(
                    id=call_id,
                    name="",
                    arguments={},
                    arg_error=f'tool call needs {{"name": …, "arguments": {{…}}}}; received: {body[:200]!r}',
                )
            )
            continue
        calls.append(_tool_call(call_id, obj["name"], obj.get("arguments")))
    visible = _BLOCK.sub("", text).strip() if calls else text
    return visible, calls


class HermesFilter:
    """Streams content through, holding back anything inside ``<tool_call>`` blocks
    (and a trailing partial ``<tool_call`` that might be the start of one)."""

    def __init__(self, on_delta: DeltaSink) -> None:
        self.on_delta = on_delta
        self.buffer = ""
        self.inside = False

    async def feed(self, text: str) -> None:
        self.buffer += text
        while self.buffer:
            if self.inside:
                end = self.buffer.find(_CLOSE)
                if end == -1:
                    # Keep only what could still be the start of the closing tag.
                    self.buffer = self.buffer[-(len(_CLOSE) - 1) :]
                    return
                self.buffer = self.buffer[end + len(_CLOSE) :]
                self.inside = False
                continue
            start = self.buffer.find(_OPEN)
            if start != -1:
                if start:
                    await self.on_delta("", self.buffer[:start])
                self.buffer = self.buffer[start + len(_OPEN) :]
                self.inside = True
                continue
            hold = _partial_suffix(self.buffer, _OPEN)
            emit = self.buffer[: len(self.buffer) - hold]
            if emit:
                await self.on_delta("", emit)
            self.buffer = self.buffer[len(self.buffer) - hold :]
            return

    async def flush(self) -> None:
        if self.buffer and not self.inside:
            await self.on_delta("", self.buffer)
        self.buffer = ""


def _partial_suffix(text: str, tag: str) -> int:
    """Length of the longest suffix of `text` that is a proper prefix of `tag`."""
    for n in range(min(len(text), len(tag) - 1), 0, -1):
        if tag.startswith(text[-n:]):
            return n
    return 0


# --- the round ----------------------------------------------------------------


def _text_of(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return str(content)


def wire_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The running messages as a chat template wants them: string content, and
    tool-call arguments as objects (Qwen's template ``tojson``s them; a JSON string
    would be double-encoded)."""
    out: list[dict[str, Any]] = []
    for m in messages:
        msg: dict[str, Any] = {
            "role": m.get("role", "user"),
            "content": _text_of(m.get("content")),
        }
        calls = m.get("tool_calls")
        if calls:
            wired = []
            for c in calls:
                fn = c.get("function") or {}
                args = fn.get("arguments")
                if isinstance(args, str):
                    try:
                        args = json.loads(args) if args.strip() else {}
                    except ValueError:
                        pass
                wired.append(
                    {
                        "type": "function",
                        "function": {
                            "name": fn.get("name", ""),
                            "arguments": args or {},
                        },
                    }
                )
            msg["tool_calls"] = wired
        if m.get("role") == "tool" and m.get("name"):
            msg["name"] = m["name"]
        out.append(msg)
    return out


async def chat_stream(
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    on_delta: DeltaSink,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> ChatResult:
    """One round on the window's GPU. See the module docstring."""
    conn = current_ws_conn.get()
    if conn is None:
        raise BrowserProviderUnavailable(
            "The browser provider runs the model in an open app window, and this "
            "call did not come from one. Pick another provider for background work."
        )
    manifest = _manifests.get(id(conn))
    if manifest is not None and not manifest[1].available:
        raise BrowserProviderUnavailable(
            f"This window cannot run models: {manifest[1].reason or 'no WebGPU'}"
        )

    gen_id = uuid.uuid4().hex[:12]
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    _streams[gen_id] = (conn, queue)

    hermes = HermesFilter(on_delta)

    async def content_sink(reasoning: str, content: str) -> None:
        if reasoning:
            await on_delta(reasoning, "")
        if content:
            await hermes.feed(content)

    extractor = ThinkingExtractor(content_sink)
    usage: Usage | None = None
    got_delta = False
    try:
        await conn.send_json(
            {
                "channel": "agent",
                "event": "generate_request",
                "data": {
                    "genId": gen_id,
                    "model": model,
                    "messages": wire_messages(messages),
                    "tools": tools,
                    "temperature": temperature,
                    "maxTokens": max_tokens,
                },
            }
        )
        while True:
            wait = IDLE_S if got_delta else FIRST_EVENT_S
            try:
                item = await asyncio.wait_for(queue.get(), timeout=wait)
            except TimeoutError:
                raise StreamStalled(
                    f"the app window sent nothing for {int(wait)}s "
                    f"({'mid-reply' if got_delta else 'before the first token'})"
                ) from None
            kind = item["event"]
            if kind == "generate_delta":
                got_delta = True
                await extractor.feed_content(str(item.get("text", "")))
            elif kind == "generate_done":
                if not got_delta:
                    await extractor.feed_content(str(item.get("text", "")))
                raw = item.get("usage") or {}
                usage = Usage(
                    tokens_in=_count(raw.get("promptTokens")),
                    tokens_out=_count(raw.get("completionTokens")),
                )
                break
            elif kind == "generate_error":
                raise ProviderStreamError(
                    f"in-browser model: {item.get('message') or 'failed'}"
                )
    except asyncio.CancelledError:
        # The chat's Stop: tell the window to stop spending its GPU on this.
        try:
            await conn.send_json(
                {
                    "channel": "agent",
                    "event": "generate_cancel",
                    "data": {"genId": gen_id},
                }
            )
        except Exception:  # noqa: BLE001 — the socket may be what went away
            pass
        raise
    finally:
        _streams.pop(gen_id, None)

    reasoning, content = await extractor.flush()
    await hermes.flush()
    visible, calls = parse_hermes(content)
    assistant: dict[str, Any] = {"role": "assistant", "content": visible}
    if reasoning:
        assistant["reasoning_content"] = reasoning
    if calls:
        assistant["tool_calls"] = [
            {
                "id": c.id,
                "type": "function",
                "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
            }
            for c in calls
        ]
    return ChatResult(assistant, calls, visible, usage)


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None

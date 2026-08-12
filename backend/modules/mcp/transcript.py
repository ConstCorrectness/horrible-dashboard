"""A bounded JSON-RPC transcript per MCP server.

MCP is a protocol, and the single most useful thing when a server misbehaves is seeing
what was actually said. "The tool returned nothing" is three different bugs — the
request never went out, the server answered an error, or the answer came back in a
shape the bridge flattened away — and only the wire distinguishes them.

**Recorded by teeing the streams, not the transport.** `ClientSession` is handed a
`(read, write)` pair of anyio memory object streams and neither knows nor cares what
produced them, so wrapping *that* pair records stdio, streamable-http and SSE with one
mechanism. Instrumenting `transport.popen_stdio_client` instead would have been simpler
and would have covered stdio only — and "the transcript is empty" would then mean
"wrong transport" rather than "nothing was sent", which is exactly the ambiguity this
module exists to remove.

The ring is small and in-process on purpose. This is a debugging view, not an audit
log: a long-running server would otherwise accumulate megabytes of tool results nobody
reads, and tool arguments frequently carry the user's own text.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Messages kept per server. Enough to cover a handshake plus a working session's worth
# of calls; old entries fall off the front.
MAX_MESSAGES = 200

# Per-message payload cap. A `resources/read` of a large file would otherwise pin
# megabytes in memory for a pane nobody has open.
MAX_PAYLOAD_CHARS = 4000


@dataclass
class WireMessage:
    """One JSON-RPC message as it crossed the boundary."""

    at: float
    direction: str  # "out" (to the server) or "in" (from it)
    method: str
    id: str
    payload: str
    truncated: bool = False

    def public(self) -> dict[str, Any]:
        return {
            "at": self.at,
            "direction": self.direction,
            "method": self.method,
            "id": self.id,
            "payload": self.payload,
            "truncated": self.truncated,
        }


class Transcript:
    """The ring for one server."""

    def __init__(self) -> None:
        self._messages: deque[WireMessage] = deque(maxlen=MAX_MESSAGES)

    def record(self, direction: str, message: Any) -> None:
        """Append one message. Never raises — a transcript must not break a session."""
        try:
            self._messages.append(_describe(direction, message))
        except Exception:  # noqa: BLE001
            logger.debug("mcp: couldn't record a wire message", exc_info=True)

    def clear(self) -> None:
        self._messages.clear()

    def public(self) -> list[dict[str, Any]]:
        return [m.public() for m in self._messages]


def _describe(direction: str, message: Any) -> WireMessage:
    """Flatten a `SessionMessage` (or a stream error) into a recordable row."""
    if isinstance(message, Exception):
        return WireMessage(
            at=time.time(),
            direction=direction,
            method="<parse error>",
            id="",
            payload=f"{type(message).__name__}: {message}",
        )

    root = getattr(getattr(message, "message", None), "root", None)
    method = str(getattr(root, "method", "") or "")
    ident = getattr(root, "id", None)
    if not method:
        # A response carries no method — name it by what it is, so a transcript reads
        # as a conversation rather than a column of blanks.
        method = "<error>" if getattr(root, "error", None) is not None else "<result>"

    try:
        payload = root.model_dump_json(by_alias=True, exclude_none=True)
    except Exception:  # noqa: BLE001
        payload = repr(root)

    truncated = len(payload) > MAX_PAYLOAD_CHARS
    return WireMessage(
        at=time.time(),
        direction=direction,
        method=method,
        id="" if ident is None else str(ident),
        payload=payload[:MAX_PAYLOAD_CHARS],
        truncated=truncated,
    )


class _TeeReceive:
    """A receive stream that records everything it hands on.

    Implements only what `ClientSession._receive_loop` uses — `async with` and
    `async for` — rather than the whole anyio protocol. Anything else the SDK might
    reach for falls through to the wrapped stream by `__getattr__`, so a future SDK
    that calls `receive()` directly keeps working (and keeps being recorded).
    """

    def __init__(self, inner: Any, transcript: Transcript) -> None:
        self._inner = inner
        self._transcript = transcript

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def __aenter__(self) -> _TeeReceive:
        await self._inner.__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> Any:
        return await self._inner.__aexit__(*exc)

    def __aiter__(self) -> _TeeReceive:
        return self

    async def __anext__(self) -> Any:
        message = await self._inner.__anext__()
        self._transcript.record("in", message)
        return message

    async def receive(self) -> Any:
        message = await self._inner.receive()
        self._transcript.record("in", message)
        return message


class _TeeSend:
    """A send stream that records everything passing through it."""

    def __init__(self, inner: Any, transcript: Transcript) -> None:
        self._inner = inner
        self._transcript = transcript

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def __aenter__(self) -> _TeeSend:
        await self._inner.__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> Any:
        return await self._inner.__aexit__(*exc)

    async def send(self, message: Any) -> None:
        # Recorded *before* the send, so a message that fails to go out still appears —
        # a transcript that silently omits the failed call is worse than none.
        self._transcript.record("out", message)
        await self._inner.send(message)


def tee(read: Any, write: Any, transcript: Transcript) -> tuple[Any, Any]:
    """Wrap a transport's `(read, write)` pair so both directions are recorded."""
    return _TeeReceive(read, transcript), _TeeSend(write, transcript)


# One ring per server id, surviving reconnects: the handshake of the *failed* attempt
# is usually the thing you want to read, and dropping it on retry would delete the
# evidence at the exact moment the user goes looking for it.
_transcripts: dict[str, Transcript] = {}


def for_server(server_id: str) -> Transcript:
    return _transcripts.setdefault(server_id, Transcript())


def forget(server_id: str) -> None:
    _transcripts.pop(server_id, None)

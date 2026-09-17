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
reads, and tool arguments frequently carry the user's own text. What *is* durable is a
per-call summary, in `calls.py`.

## Which agent turn a message belongs to — the two directions differ

The two tees run in **different tasks**, and that decides everything. `_TeeSend.send`
runs in the task that made the call, so the turn contextvar (`telemetry/turn.py`) is
the caller's and correct. `_TeeReceive.__anext__` runs inside `ClientSession`'s own
receive loop, a long-lived task started when the session *connected* — there the
contextvar holds whatever was current at connect time: usually nothing, occasionally a
stale turn. Stamping inbound messages from it would produce confidently wrong data.

So only outbound messages are stamped from the contextvar, and an inbound message
inherits the stamp of the outbound request carrying the same JSON-RPC `id`.

The same property is what `capture()` rides on: a call opens a capture in its own task,
and every request id sent from that task lands in it. That is exact under concurrency —
two simultaneous calls on one session each see only their own ids — where scanning the
ring for "ids sent since I started" would hand each call the other's.
"""

from __future__ import annotations

import contextvars
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
    #: The agent turn this message belongs to, or None. See the module docstring for
    #: why inbound messages get this by id-match rather than from the contextvar.
    turn_id: str | None = None
    round: int | None = None
    #: Which connection carried this message. JSON-RPC ids restart on every connect
    #: and this ring survives reconnects, so `(session, id)` is the identity of an
    #: exchange and `id` alone is not.
    session: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "at": self.at,
            "direction": self.direction,
            "method": self.method,
            "id": self.id,
            "payload": self.payload,
            "truncated": self.truncated,
            "turn_id": self.turn_id,
            "round": self.round,
            "session": self.session,
        }


#: Request ids sent from the current task, while a `capture()` is open. None means no
#: capture is open, which is every send except the ones inside `McpSession.call_tool`.
_captured: contextvars.ContextVar[list[str] | None] = contextvars.ContextVar(
    "mcp_captured_rpc_ids", default=None
)


def begin_capture() -> contextvars.Token:
    """Start collecting the JSON-RPC ids this task sends. Pair with `end_capture`."""
    return _captured.set([])


def end_capture(token: contextvars.Token) -> list[str]:
    """Stop collecting and return the ids sent since `begin_capture`."""
    ids = _captured.get() or []
    _captured.reset(token)
    return list(ids)


def _current_turn() -> tuple[str, int] | None:
    try:
        from backend.modules.telemetry import turn

        return turn.current()
    except Exception:  # noqa: BLE001 - a missing stamp is fine; a crash is not
        return None


class Transcript:
    """The ring for one server."""

    def __init__(self) -> None:
        self._messages: deque[WireMessage] = deque(maxlen=MAX_MESSAGES)

    def record(self, direction: str, message: Any, session: str = "") -> None:
        """Append one message. Never raises — a transcript must not break a session."""
        try:
            wire = _describe(direction, message)
            wire.session = session
            if direction == "out":
                mark = _current_turn()
                if mark is not None:
                    wire.turn_id, wire.round = mark[0], mark[1]
            elif wire.id:
                request = self._request_for(wire.id, session)
                if request is not None:
                    wire.turn_id, wire.round = request.turn_id, request.round
            self._messages.append(wire)
        except Exception:  # noqa: BLE001
            logger.debug("mcp: couldn't record a wire message", exc_info=True)

    def _request_for(self, ident: str, session: str) -> WireMessage | None:
        """The outbound message this response answers: same id, same connection."""
        for wire in reversed(self._messages):
            if wire.direction == "out" and wire.id == ident and wire.session == session:
                return wire
        return None

    def by_ids(
        self, ids: list[str], *, session: str | None = None
    ) -> list[WireMessage]:
        """Both halves of the given request ids, in the order they crossed the wire.

        **Pass the session.** JSON-RPC ids restart at every reconnect and this ring
        deliberately survives reconnects, so id `4` names a different exchange in each
        session. Matched on id alone, an old call's wire came back with later sessions'
        unrelated exchanges appended — found by reconnecting the fixture server in the
        running app. A time window was tried first and is not enough: a quick reconnect
        puts two sessions' id-4 calls within a second of each other. The connection is
        the exact disambiguator.
        """
        wanted = set(ids)
        return [
            m
            for m in self._messages
            if m.id in wanted and (session is None or m.session == session)
        ]

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

    def __init__(self, inner: Any, transcript: Transcript, session: str = "") -> None:
        self._inner = inner
        self._transcript = transcript
        self._session = session

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
        self._transcript.record("in", message, self._session)
        return message

    async def receive(self) -> Any:
        message = await self._inner.receive()
        self._transcript.record("in", message, self._session)
        return message


class _TeeSend:
    """A send stream that records everything passing through it."""

    def __init__(self, inner: Any, transcript: Transcript, session: str = "") -> None:
        self._inner = inner
        self._transcript = transcript
        self._session = session

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
        self._transcript.record("out", message, self._session)
        captured = _captured.get()
        if captured is not None:
            ident = getattr(
                getattr(getattr(message, "message", None), "root", None), "id", None
            )
            if ident is not None:
                captured.append(str(ident))
        await self._inner.send(message)


def tee(
    read: Any, write: Any, transcript: Transcript, session: str = ""
) -> tuple[Any, Any]:
    """Wrap a transport's `(read, write)` pair so both directions are recorded.

    `session` names this connection. Every message it carries is stamped with it,
    because JSON-RPC ids restart per connection — see `Transcript.by_ids`.
    """
    return _TeeReceive(read, transcript, session), _TeeSend(write, transcript, session)


# One ring per server id, surviving reconnects: the handshake of the *failed* attempt
# is usually the thing you want to read, and dropping it on retry would delete the
# evidence at the exact moment the user goes looking for it.
_transcripts: dict[str, Transcript] = {}


def for_server(server_id: str) -> Transcript:
    return _transcripts.setdefault(server_id, Transcript())


def forget(server_id: str) -> None:
    _transcripts.pop(server_id, None)

"""Instrumentation seams: inbound HTTP, outbound httpx, and `/ws` frames.

This is a **local introspection tool** (the observability panel is the app's
built-in Wireshark): traffic is captured **raw** — full headers and bodies, no
masking — and held in an in-memory ring buffer. Treat that buffer as sensitive;
it can contain credentials, tokens, and personal data flowing through the app.
Bodies are only *size*-capped (truncated past the user-configured
``observability.maxBodyChars``, and never reading past ``_MAX_CAPTURE_BYTES``).
Wiring these once means every module's traffic is observed without per-module
logging — see docs/modules/observability.md.
"""

import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable

import httpx
from starlette.requests import Request
from starlette.responses import Response

from backend.modules.settings import get_value
from backend.modules.telemetry.recorder import recorder

# Don't observe the telemetry endpoints (noise) or the websocket itself.
_SKIP_PREFIXES = ("/api/telemetry",)

# Default body-truncation cap (characters) and the hard ceiling on how many bytes
# are ever read from a body. Generous enough to inspect real payloads; the ceiling
# bounds memory (inbound bodies are buffered to replay them downstream).
_MAX_BODY_CHARS = 16384
_MAX_CAPTURE_BYTES = 1_048_576  # 1 MB

# Content types whose bodies stream incrementally (Ollama NDJSON, OpenAI SSE).
# Reading these in the response hook would consume the stream before the caller
# can iterate it, so their response bodies are tee'd later, not read here.
_STREAMING_CONTENT_TYPES = ("text/event-stream", "application/x-ndjson")

# `/ws` channels whose frames are not recorded: `telemetry` is the push channel
# that carries these very events — recording it would feed back on itself.
_SKIP_WS_CHANNELS = ("telemetry",)


def capture_headers(headers: object) -> dict[str, str]:
    """Lowercased header map, values captured raw (no redaction)."""
    out: dict[str, str] = {}
    for name, value in dict(headers).items():  # type: ignore[call-overload]
        out[str(name).lower()] = str(value)
    return out


def safe_body(raw: bytes | None, *, max_chars: int = _MAX_BODY_CHARS) -> str | None:
    """Body as text for the event detail: captured raw, decoded leniently, and
    truncated to ``max_chars`` (callers pass the user-configured cap via
    ``_max_body_chars()``). Never reads past ``_MAX_CAPTURE_BYTES``."""
    if not raw:
        return None
    text = raw[:_MAX_CAPTURE_BYTES].decode("utf-8", errors="replace")
    if len(text) > max_chars:
        return text[:max_chars] + "… [truncated]"
    return text


def _max_body_chars() -> int:
    """User-configured body-truncation cap (``observability.maxBodyChars``),
    falling back to ``_MAX_BODY_CHARS`` and clamped to the hard capture ceiling —
    nothing past ``_MAX_CAPTURE_BYTES`` is ever read, so it can't be shown. The
    default mirrors the frontend declaration in the observability module."""
    value = get_value("observability.maxBodyChars", _MAX_BODY_CHARS)
    try:
        return max(0, min(int(value), _MAX_CAPTURE_BYTES))
    except (TypeError, ValueError):
        return _MAX_BODY_CHARS


def _is_streaming_response(response: httpx.Response) -> bool:
    ctype = response.headers.get("content-type", "")
    return any(marker in ctype for marker in _STREAMING_CONTENT_TYPES)


async def _capture_response(response: httpx.Response) -> tuple[str | None, int | None]:
    """Read a *non-streaming* outbound response body for the event detail,
    truncated like request bodies. Streaming responses are skipped (see
    _is_streaming_response): reading them here would buffer them into memory and
    consume the stream before the caller can. The body read is cached by httpx, so
    the caller's subsequent ``.json()`` still works. Returns
    ``(body_text, byte_count)``."""
    if _is_streaming_response(response):
        return None, None
    content_length = int(response.headers.get("content-length") or 0)
    if content_length > _MAX_CAPTURE_BYTES:
        return None, content_length
    try:
        raw = await response.aread()
    except (httpx.StreamConsumed, httpx.StreamClosed, httpx.ResponseNotRead):
        return None, None
    return safe_body(raw, max_chars=_max_body_chars()), len(raw)


async def telemetry_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    path = request.url.path
    if path == "/ws" or path.startswith(_SKIP_PREFIXES):
        return await call_next(request)

    # Read the body up front: Starlette's middleware request replays it to the
    # downstream handler. Skip huge payloads rather than buffering them twice.
    content_length = int(request.headers.get("content-length") or 0)
    raw_body = (
        await request.body() if 0 < content_length <= _MAX_CAPTURE_BYTES else None
    )

    start = time.perf_counter()
    status: int | None = None
    error: str | None = None
    response_headers: dict[str, str] | None = None
    try:
        response = await call_next(request)
        status = response.status_code
        response_headers = capture_headers(response.headers)
        return response
    except Exception as exc:  # record then re-raise
        error = type(exc).__name__
        raise
    finally:
        recorder.record(
            source="inbound",
            method=request.method,
            target=path,
            status=status,
            duration_ms=(time.perf_counter() - start) * 1000,
            error=error,
            request_headers=capture_headers(request.headers),
            response_headers=response_headers,
            request_body=safe_body(raw_body, max_chars=_max_body_chars()),
            # Response bodies are not captured inbound: responses stream, and the
            # client-side event for the same round-trip already records them.
        )


# Outbound timing: stamp on the request hook, read on the response hook.
_starts: dict[int, float] = {}
# Streaming responses recorded with no body yet, keyed by id(request) → event id,
# so `tee_stream` can fill the body in once the stream finishes (see _on_response).
_stream_events: dict[int, int] = {}


async def _on_request(request: httpx.Request) -> None:
    _starts[id(request)] = time.perf_counter()


async def _on_response(response: httpx.Response) -> None:
    request = response.request
    start = _starts.pop(id(request), None)
    # Strip query/fragment so secrets in URLs (if any) aren't recorded.
    target = f"{request.url.scheme}://{request.url.host}{request.url.path}"
    # Non-streaming response bodies (e.g. the agent's stream:false /api/chat round)
    # are captured here; streaming ones (Ollama chat/pull NDJSON, OpenAI SSE) can't
    # be read here without consuming the stream — `tee_stream` amends them later.
    response_body, response_bytes = await _capture_response(response)
    event = recorder.record(
        source="outbound",
        method=request.method,
        target=target,
        status=response.status_code,
        duration_ms=(time.perf_counter() - start) * 1000 if start is not None else None,
        request_bytes=len(request.content) if request.content else None,
        response_bytes=response_bytes,
        request_headers=capture_headers(request.headers),
        response_headers=capture_headers(response.headers),
        request_body=safe_body(request.content, max_chars=_max_body_chars()),
        response_body=response_body,
    )
    if _is_streaming_response(response):
        _stream_events[id(request)] = event.id


async def tee_stream(
    response: httpx.Response, lines: AsyncIterator[str]
) -> AsyncIterator[str]:
    """Pass a streaming outbound response's lines through unchanged while
    accumulating a capped copy of the body, then amend the I/O event the response
    hook already recorded (which has no body yet — see _on_response). This is the
    only safe way to observe a stream: the hook can't read it without consuming it.
    Accumulation stops at _MAX_CAPTURE_BYTES so a long generation can't grow
    unbounded. Wrap the two streaming call sites (providers.generate_stream,
    routes._proxy_ndjson) with this; a non-instrumented stream just passes through."""
    event_id = _stream_events.pop(id(response.request), None)
    if event_id is None:
        async for line in lines:
            yield line
        return
    captured: list[str] = []
    size = 0
    try:
        async for line in lines:
            if size < _MAX_CAPTURE_BYTES:
                captured.append(line)
                size += len(line.encode("utf-8")) + 1
            yield line
    finally:
        body = safe_body(
            "\n".join(captured).encode("utf-8"), max_chars=_max_body_chars()
        )
        recorder.amend(event_id, response_body=body, response_bytes=size or None)


def record_ws_frame(direction: str, data: object) -> None:
    """Record one `/ws` frame as an I/O event so the observability panel can show
    the multiplexed socket traffic Wireshark-style. ``direction`` is ``"in"``
    (browser → backend) or ``"out"`` (backend → browser). Frames on the telemetry
    push channel are skipped to avoid observing our own output (a feedback loop).
    Must never raise into the socket path — callers wrap it defensively too."""
    if not isinstance(data, dict):
        return
    channel = str(data.get("channel", "?"))
    if channel in _SKIP_WS_CHANNELS:
        return
    event_name = data.get("event") or data.get("type")
    target = f"{channel}/{event_name}" if event_name else channel
    try:
        compact = json.dumps(data, separators=(",", ":"), default=str)
        pretty = json.dumps(data, indent=2, default=str)
    except (TypeError, ValueError):
        compact = pretty = str(data)
    recorder.record(
        source="ws",
        method="recv" if direction == "in" else "send",
        target=target,
        request_bytes=len(compact.encode("utf-8")),
        request_body=safe_body(pretty.encode("utf-8"), max_chars=_max_body_chars()),
    )


def instrumented_client(**kwargs: object) -> httpx.AsyncClient:
    """An httpx.AsyncClient whose calls are recorded as outbound I/O events."""
    hooks = dict(kwargs.pop("event_hooks", {}) or {})  # type: ignore[arg-type]
    hooks["request"] = [*hooks.get("request", []), _on_request]
    hooks["response"] = [*hooks.get("response", []), _on_response]
    return httpx.AsyncClient(event_hooks=hooks, **kwargs)  # type: ignore[arg-type]

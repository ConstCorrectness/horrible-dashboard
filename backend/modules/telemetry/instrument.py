"""Instrumentation seams: one for inbound HTTP, one for outbound httpx.

Both feed the recorder with metadata plus **redacted** detail: headers with
credential-bearing values masked, bodies truncated, and bodies suppressed
entirely on sensitive routes (Clubhouse auth carries phone numbers, SMS codes,
and tokens). Wiring these once means every module's traffic is observed without
per-module logging — see docs/modules/observability.md.
"""

import time
from collections.abc import AsyncIterator, Awaitable, Callable

import httpx
from starlette.requests import Request
from starlette.responses import Response

from backend.modules.settings import get_value
from backend.modules.telemetry.recorder import recorder

# Don't observe the telemetry endpoints (noise) or the websocket itself.
_SKIP_PREFIXES = ("/api/telemetry",)

# Routes whose bodies must never be recorded (credentials, phone numbers, SMS
# codes). Headers are still captured — redaction below masks the dangerous ones.
_SENSITIVE_PATH_PREFIXES = ("/api/clubhouse",)
_SENSITIVE_HOST_MARKERS = ("clubhouse",)

# Header names (lowercased) whose values are masked; substring matches catch
# vendor variants like x-api-key, ch-session-token, x-auth-secret.
_REDACTED_HEADER_MARKERS = (
    "authorization",
    "cookie",
    "token",
    "secret",
    "api-key",
    "session",
)

_MAX_BODY_CHARS = 2048
_MAX_CAPTURE_BYTES = 65536

# Content types whose bodies stream incrementally (Ollama NDJSON, OpenAI SSE).
# Reading these in the response hook would consume the stream before the caller
# can iterate it, so their response bodies are never captured.
_STREAMING_CONTENT_TYPES = ("text/event-stream", "application/x-ndjson")

REDACTED = "[redacted]"
SENSITIVE_REDACTED = "[redacted — sensitive route]"


def redact_headers(headers: object) -> dict[str, str]:
    """Lowercased header map with values captured raw (no redaction)."""
    out: dict[str, str] = {}
    for name, value in dict(headers).items():  # type: ignore[call-overload]
        key = str(name).lower()
        out[key] = str(value)
    return out


def safe_body(
    raw: bytes | None, *, sensitive: bool, max_chars: int = _MAX_BODY_CHARS
) -> str | None:
    """Body as text for the event detail: captured raw (no redaction),
    decoded leniently, truncated to ``max_chars`` (default ``_MAX_BODY_CHARS``;
    callers pass the user-configured cap via ``_max_body_chars()``)."""
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


def _inbound_sensitive(path: str) -> bool:
    return path.startswith(_SENSITIVE_PATH_PREFIXES)


def _outbound_sensitive(url: httpx.URL) -> bool:
    return any(marker in url.host for marker in _SENSITIVE_HOST_MARKERS)


def _is_streaming_response(response: httpx.Response) -> bool:
    ctype = response.headers.get("content-type", "")
    return any(marker in ctype for marker in _STREAMING_CONTENT_TYPES)


async def _capture_response(
    response: httpx.Response, *, sensitive: bool
) -> tuple[str | None, int | None]:
    """Read a *non-streaming* outbound response body for the event detail —
    redacted and truncated like request bodies. Streaming responses are skipped
    (see _is_streaming_response): reading them here would buffer them into memory
    and consume the stream before the caller can. The body read is cached by
    httpx, so the caller's subsequent ``.json()`` still works. Returns
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
    return safe_body(raw, sensitive=sensitive, max_chars=_max_body_chars()), len(raw)


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
        response_headers = redact_headers(response.headers)
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
            request_headers=redact_headers(request.headers),
            response_headers=response_headers,
            request_body=safe_body(
                raw_body,
                sensitive=_inbound_sensitive(path),
                max_chars=_max_body_chars(),
            ),
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
    sensitive = _outbound_sensitive(request.url)
    # Non-streaming response bodies (e.g. the agent's stream:false /api/chat round)
    # are captured here; streaming ones (Ollama chat/pull NDJSON, OpenAI SSE) can't
    # be read here without consuming the stream — `tee_stream` amends them later.
    response_body, response_bytes = await _capture_response(
        response, sensitive=sensitive
    )
    event = recorder.record(
        source="outbound",
        method=request.method,
        target=target,
        status=response.status_code,
        duration_ms=(time.perf_counter() - start) * 1000 if start is not None else None,
        request_bytes=len(request.content) if request.content else None,
        response_bytes=response_bytes,
        request_headers=redact_headers(request.headers),
        response_headers=redact_headers(response.headers),
        request_body=safe_body(
            request.content, sensitive=sensitive, max_chars=_max_body_chars()
        ),
        response_body=response_body,
    )
    if _is_streaming_response(response):
        _stream_events[id(request)] = event.id


async def tee_stream(
    response: httpx.Response, lines: AsyncIterator[str]
) -> AsyncIterator[str]:
    """Pass a streaming outbound response's lines through unchanged while
    accumulating a capped, redacted copy of the body, then amend the I/O event the
    response hook already recorded (which has no body yet — see _on_response). This
    is the only safe way to observe a stream: the hook can't read it without
    consuming it. Accumulation stops at _MAX_CAPTURE_BYTES so a long generation
    can't grow unbounded. Wrap the two streaming call sites (providers.generate_stream,
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
            "\n".join(captured).encode("utf-8"),
            sensitive=False,
            max_chars=_max_body_chars(),
        )
        recorder.amend(event_id, response_body=body, response_bytes=size or None)


def instrumented_client(**kwargs: object) -> httpx.AsyncClient:
    """An httpx.AsyncClient whose calls are recorded as outbound I/O events."""
    hooks = dict(kwargs.pop("event_hooks", {}) or {})  # type: ignore[arg-type]
    hooks["request"] = [*hooks.get("request", []), _on_request]
    hooks["response"] = [*hooks.get("response", []), _on_response]
    return httpx.AsyncClient(event_hooks=hooks, **kwargs)  # type: ignore[arg-type]

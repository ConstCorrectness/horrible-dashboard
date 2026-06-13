"""Instrumentation seams: one for inbound HTTP, one for outbound httpx.

Both feed the recorder with metadata plus **redacted** detail: headers with
credential-bearing values masked, bodies truncated, and bodies suppressed
entirely on sensitive routes (Clubhouse auth carries phone numbers, SMS codes,
and tokens). Wiring these once means every module's traffic is observed without
per-module logging — see docs/modules/observability.md.
"""

import time
from collections.abc import Awaitable, Callable

import httpx
from starlette.requests import Request
from starlette.responses import Response

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

REDACTED = "[redacted]"


def redact_headers(headers: object) -> dict[str, str]:
    """Lowercased header map with credential-bearing values masked."""
    out: dict[str, str] = {}
    for name, value in dict(headers).items():  # type: ignore[call-overload]
        key = str(name).lower()
        marked = any(marker in key for marker in _REDACTED_HEADER_MARKERS)
        out[key] = REDACTED if marked else str(value)
    return out


def safe_body(raw: bytes | None, *, sensitive: bool) -> str | None:
    """Body as text for the event detail: suppressed on sensitive routes,
    decoded leniently, truncated."""
    if not raw:
        return None
    if sensitive:
        return "[redacted — sensitive route]"
    text = raw[:_MAX_CAPTURE_BYTES].decode("utf-8", errors="replace")
    if len(text) > _MAX_BODY_CHARS:
        return text[:_MAX_BODY_CHARS] + "… [truncated]"
    return text


def _inbound_sensitive(path: str) -> bool:
    return path.startswith(_SENSITIVE_PATH_PREFIXES)


def _outbound_sensitive(url: httpx.URL) -> bool:
    return any(marker in url.host for marker in _SENSITIVE_HOST_MARKERS)


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
            request_body=safe_body(raw_body, sensitive=_inbound_sensitive(path)),
            # Response bodies are not captured inbound: responses stream, and the
            # client-side event for the same round-trip already records them.
        )


# Outbound timing: stamp on the request hook, read on the response hook.
_starts: dict[int, float] = {}


async def _on_request(request: httpx.Request) -> None:
    _starts[id(request)] = time.perf_counter()


async def _on_response(response: httpx.Response) -> None:
    request = response.request
    start = _starts.pop(id(request), None)
    # Strip query/fragment so secrets in URLs (if any) aren't recorded.
    target = f"{request.url.scheme}://{request.url.host}{request.url.path}"
    sensitive = _outbound_sensitive(request.url)
    recorder.record(
        source="outbound",
        method=request.method,
        target=target,
        status=response.status_code,
        duration_ms=(time.perf_counter() - start) * 1000 if start is not None else None,
        request_bytes=len(request.content) if request.content else None,
        request_headers=redact_headers(request.headers),
        response_headers=redact_headers(response.headers),
        request_body=safe_body(request.content, sensitive=sensitive),
        # Response bodies are not captured outbound: the hook fires before the
        # body is read, and reading here would buffer streaming responses
        # (Ollama chat/pull) into memory.
    )


def instrumented_client(**kwargs: object) -> httpx.AsyncClient:
    """An httpx.AsyncClient whose calls are recorded as outbound I/O events."""
    hooks = dict(kwargs.pop("event_hooks", {}) or {})  # type: ignore[arg-type]
    hooks["request"] = [*hooks.get("request", []), _on_request]
    hooks["response"] = [*hooks.get("response", []), _on_response]
    return httpx.AsyncClient(event_hooks=hooks, **kwargs)  # type: ignore[arg-type]

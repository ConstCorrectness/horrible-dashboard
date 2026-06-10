"""Instrumentation seams: one for inbound HTTP, one for outbound httpx.

Both feed the recorder with metadata only. Wiring these once means every
module's traffic is observed without per-module logging — see
docs/modules/observability.md.
"""

import time
from collections.abc import Awaitable, Callable

import httpx
from starlette.requests import Request
from starlette.responses import Response

from backend.modules.telemetry.recorder import recorder

# Don't observe the telemetry endpoints (noise) or the websocket itself.
_SKIP_PREFIXES = ("/api/telemetry",)


async def telemetry_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    path = request.url.path
    if path == "/ws" or path.startswith(_SKIP_PREFIXES):
        return await call_next(request)

    start = time.perf_counter()
    status: int | None = None
    error: str | None = None
    try:
        response = await call_next(request)
        status = response.status_code
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
    recorder.record(
        source="outbound",
        method=request.method,
        target=target,
        status=response.status_code,
        duration_ms=(time.perf_counter() - start) * 1000 if start is not None else None,
        request_bytes=len(request.content) if request.content else None,
    )


def instrumented_client(**kwargs: object) -> httpx.AsyncClient:
    """An httpx.AsyncClient whose calls are recorded as outbound I/O events."""
    hooks = dict(kwargs.pop("event_hooks", {}) or {})  # type: ignore[arg-type]
    hooks["request"] = [*hooks.get("request", []), _on_request]
    hooks["response"] = [*hooks.get("response", []), _on_response]
    return httpx.AsyncClient(event_hooks=hooks, **kwargs)  # type: ignore[arg-type]

"""Bearer-token auth for the exported MCP server.

Deliberately minimal and deliberately *not* reused from anywhere else: this guards one
mounted ASGI app whose entire surface is read-only introspection. It is not a general
auth system for the app, and it should not grow into one.

The comparison is constant-time. The token protects trajectories and telemetry on a
port that other local processes can reach, and a naive `==` on a secret is the kind of
detail that is free to get right now and awkward to retrofit.
"""

from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class BearerAuthMiddleware:
    """Reject any request without `Authorization: Bearer <token>`."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        token: str,
        allow_paths: tuple[str, ...] = (),
    ) -> None:
        self.app = app
        self._token = token
        self._allow_paths = allow_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if any(path.startswith(p) for p in self._allow_paths):
            await self.app(scope, receive, send)
            return
        if self._authorized(scope):
            await self.app(scope, receive, send)
            return
        response = JSONResponse(
            {"error": "unauthorized"},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
        await response(scope, receive, send)

    def _authorized(self, scope: Scope) -> bool:
        for name, value in scope.get("headers") or []:
            if name.lower() != b"authorization":
                continue
            try:
                header = value.decode("latin-1")
            except UnicodeDecodeError:
                return False
            scheme, _, presented = header.partition(" ")
            if scheme.lower() != "bearer":
                return False
            return hmac.compare_digest(presented.strip(), self._token)
        return False


# Kept for callers that want the check without the middleware wrapper (tests, and any
# future non-ASGI entry point).
def check_token(presented: str | None, expected: str) -> bool:
    if not presented or not expected:
        return False
    return hmac.compare_digest(presented, expected)


AsgiHandler = Callable[[Scope, Receive, Send], Awaitable[Any]]

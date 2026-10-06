"""Reverse proxy from a signed-in browser to that user's instance.

HTTP is streamed both ways (uploads, downloads and any long response pass through
without buffering). The WebSocket is a two-way frame pump. On the way in the
browser's cookies — the hub session — are dropped, and the instance token plus
the user's public identity are added; the instance trusts nothing else
(`backend/hosted.py`).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

import httpx
from fastapi import Request, WebSocket
from starlette.background import BackgroundTask
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.websockets import WebSocketDisconnect, WebSocketState
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed

from backend.hub.spawner.base import Instance
from backend.hub.store import User

if TYPE_CHECKING:
    from backend.hub.app import Hub

logger = logging.getLogger(__name__)

TOKEN_HEADER = "x-horrible-instance-token"
USER_HEADER = "x-horrible-user"

#: Hop-by-hop headers (RFC 9110 §7.6.1) plus the ones the proxy owns.
_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "trailers",
    "transfer-encoding",
    "upgrade",
}
_DROP_REQUEST = _HOP | {"host", "content-length", "cookie", TOKEN_HEADER, USER_HEADER}
#: The instance has no business setting cookies on the hub's origin — one named
#: like the session cookie would sign the user out.
_DROP_RESPONSE = _HOP | {"set-cookie"}

#: Prefixes the browser may not reach through the proxy: hub → instance control.
BLOCKED_PREFIXES = ("/api/hosted/",)


#: Strong references to in-flight upstream closes (asyncio keeps only weak ones).
_closing: set[asyncio.Task[None]] = set()


def _close_later(upstream: Any) -> None:
    task = asyncio.get_running_loop().create_task(upstream.close())
    _closing.add(task)
    task.add_done_callback(_closing.discard)


def identity_headers(instance: Instance, user: User) -> list[tuple[str, str]]:
    return [
        (TOKEN_HEADER, instance.token),
        (USER_HEADER, json.dumps(user.public(), separators=(",", ":"))),
    ]


async def proxy_http(
    hub: "Hub", request: Request, user: User, instance: Instance
) -> Response:
    path = request.url.path
    if path.startswith(BLOCKED_PREFIXES):
        return JSONResponse({"detail": "not found"}, status_code=404)
    url = instance.base_url + path
    if request.url.query:
        url += "?" + request.url.query
    headers = [
        (k, v) for k, v in request.headers.items() if k.lower() not in _DROP_REQUEST
    ]
    headers += identity_headers(instance, user)
    upstream = hub.http.build_request(
        request.method,
        url,
        headers=headers,
        content=request.stream(),
        # No read timeout: a long-poll or a slow download is the instance's call.
        timeout=httpx.Timeout(None, connect=5.0),
    )
    try:
        res = await hub.http.send(upstream, stream=True)
    except httpx.ConnectError:
        return JSONResponse({"starting": True}, status_code=503)
    except httpx.HTTPError as exc:
        logger.warning("proxy to %s failed: %s", url, exc)
        return JSONResponse({"detail": "instance unavailable"}, status_code=502)
    out = StreamingResponse(
        res.aiter_raw(),
        status_code=res.status_code,
        background=BackgroundTask(res.aclose),
    )
    out.raw_headers = [
        (k, v)
        for k, v in res.headers.raw
        if k.decode("latin-1").lower() not in _DROP_RESPONSE
    ]
    return out


async def proxy_ws(
    hub: "Hub", websocket: WebSocket, user: User, instance: Instance
) -> None:
    """Accept the browser's socket, open the instance's, and pump frames both ways
    until either side closes; the other side is then closed with the same code."""
    url = instance.base_url.replace("http", "ws", 1) + websocket.url.path
    try:
        upstream = await ws_connect(
            url,
            additional_headers=identity_headers(instance, user),
            max_size=None,
            open_timeout=10,
        )
    except (OSError, ConnectionClosed, asyncio.TimeoutError) as exc:
        logger.info("instance socket for %s unavailable: %s", user.id, exc)
        await websocket.close(code=1013)  # try again later
        return

    await websocket.accept()

    async def browser_to_instance() -> None:
        while True:
            msg = await websocket.receive()
            if msg["type"] == "websocket.disconnect":
                return
            if msg.get("text") is not None:
                await upstream.send(msg["text"])
            elif msg.get("bytes") is not None:
                await upstream.send(msg["bytes"])

    async def instance_to_browser() -> None:
        async for frame in upstream:
            hub.touch(user.id)
            if isinstance(frame, str):
                await websocket.send_text(frame)
            else:
                await websocket.send_bytes(frame)

    hub.open_socket(user.id)
    tasks = [
        asyncio.create_task(browser_to_instance()),
        asyncio.create_task(instance_to_browser()),
    ]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        # Cancelled, not awaited. Awaiting a `gather` here is how a cancellation of
        # this handler (the server dropping it as the browser leaves) gets replaced
        # by a CancelledError of gather's own, which the server's task group does
        # not recognise as the one it issued — it surfaced as an intermittent
        # failure of the WS test. Neither pump holds anything that needs a wait.
        for task in tasks:
            task.cancel()
        hub.close_socket(user.id)
        code = upstream.close_code or 1000
        # The instance-side close handshake runs on its own: holding the browser's
        # handler open for a round trip to the instance buys nothing, and a server
        # that cancels a handler right after the browser leaves would cut it short.
        _close_later(upstream)
        if websocket.application_state == WebSocketState.CONNECTED:
            try:
                await websocket.close(code=code if code != 1006 else 1011)
            except (RuntimeError, WebSocketDisconnect):
                pass

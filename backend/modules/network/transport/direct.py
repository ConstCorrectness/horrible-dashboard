"""Direct peer-to-peer transport over WebSocket.

Outbound: `dial(ws://host:port/peer-ws)` opens a `websockets` client connection.
Inbound: the FastAPI `/peer-ws` endpoint accepts a socket and wraps it in a
`ServerPeerLink` handed to `PeerHub.accept_link`. Both directions speak the same
signed `PeerEnvelope` protocol, so either end can be dialer or acceptor.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed

from backend.modules.network import protocol
from backend.modules.network.models import PeerEnvelope
from backend.modules.network.transport.base import LinkClosed, PeerLink, Transport

if TYPE_CHECKING:
    from backend.modules.network.hub import PeerHub


class ClientPeerLink(PeerLink):
    """Outbound link wrapping a `websockets` client connection."""

    transport_name = "direct"

    def __init__(self, ws: Any, address: str) -> None:
        self._ws = ws
        self.address = address

    async def send(self, env: PeerEnvelope) -> None:
        try:
            await self._ws.send(protocol.encode(env))
        except ConnectionClosed as exc:
            raise LinkClosed from exc

    async def recv(self) -> PeerEnvelope:
        try:
            raw = await self._ws.recv()
        except ConnectionClosed as exc:
            raise LinkClosed from exc
        return protocol.decode(raw if isinstance(raw, str) else raw.decode("utf-8"))

    async def close(self) -> None:
        await self._ws.close()


class ServerPeerLink(PeerLink):
    """Inbound link wrapping an already-accepted Starlette `WebSocket` (from the
    `/peer-ws` endpoint)."""

    transport_name = "direct"

    def __init__(self, ws: Any) -> None:
        self._ws = ws
        peer = getattr(ws, "client", None)
        self.address = f"{peer.host}:{peer.port}" if peer else None

    async def send(self, env: PeerEnvelope) -> None:
        try:
            await self._ws.send_text(protocol.encode(env))
        except Exception as exc:  # WebSocketDisconnect or transport error
            raise LinkClosed from exc

    async def recv(self) -> PeerEnvelope:
        try:
            raw = await self._ws.receive_text()
        except Exception as exc:  # WebSocketDisconnect when the peer goes away
            raise LinkClosed from exc
        return protocol.decode(raw)

    async def close(self) -> None:
        try:
            await self._ws.close()
        except Exception:
            pass


class DirectWsTransport(Transport):
    name = "direct"

    async def start(self, hub: PeerHub) -> None:
        # Inbound is served by the `/peer-ws` route (see backend/app.py); nothing to
        # start here.
        return None

    async def dial(self, address: str) -> PeerLink:
        ws = await ws_connect(address)
        return ClientPeerLink(ws, address)

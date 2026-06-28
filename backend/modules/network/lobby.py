"""Node-side **lobby client**: connect to a lobby server for discovery + rooms, and
turn a room join into a live peer link (direct P2P first, relay fallback).

One `LobbyClient` per node (a process-global singleton, like `peer_hub`). It owns the
outbound WebSocket to `network.lobbyUrl`, keeps the room/directory snapshot the
frontend renders (fanned out over the `/ws` `lobby` channel), and on a join dials the
host over `/peer-ws` — falling back to the lobby host's relay if the direct dial
fails. Signaling frames are forwarded for future ICE; NAT hole-punching is stubbed.

See docs/architecture/network-protocol.mdx (the lobby system) and docs/modules/network.mdx.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from urllib.parse import urlparse, urlunparse

from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed

from backend.modules.network import identity, trust
from backend.modules.network.hub import peer_hub
from backend.modules.network.transport.relay import RelayTransport
from backend.modules.settings.routes import get_value

logger = logging.getLogger(__name__)


def _relay_url_for(lobby_url: str) -> str:
    """The bundled relay endpoint on the same lobby host (…/lobby-ws → …/relay-ws)."""
    parsed = urlparse(lobby_url)
    path = parsed.path.rsplit("/", 1)[0] + "/relay-ws"
    return urlunparse(parsed._replace(path=path))


class LobbyClient:
    def __init__(self) -> None:
        self.url: str | None = None
        self.connected = False
        self.rooms: list[dict[str, Any]] = []
        self.directory: list[dict[str, Any]] = []
        self._ws: Any = None
        self._reader: asyncio.Task[None] | None = None
        self._subscribers: set[Any] = set()

    # ---- frontend fanout ----------------------------------------------------------

    def subscribe(self, cb: Any) -> Any:
        self._subscribers.add(cb)
        return lambda: self._subscribers.discard(cb)

    def _emit(self, event: str, data: dict[str, Any]) -> None:
        for cb in list(self._subscribers):
            try:
                cb(event, data)
            except Exception:
                logger.exception("lobby subscriber failed")

    def snapshot(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "url": self.url,
            "rooms": self.rooms,
            "directory": self.directory,
            "self": peer_hub.identity().model_dump(),
        }

    # ---- lifecycle ----------------------------------------------------------------

    async def start(self) -> None:
        url = str(get_value("network.lobbyUrl", "") or "").strip()
        if url:
            await self.connect(url)

    async def connect(self, url: str) -> None:
        await self.disconnect()
        self.url = url
        # Make sure a relay transport to this lobby host exists for the fallback path.
        await peer_hub.add_transport(RelayTransport(_relay_url_for(url)))
        try:
            self._ws = await ws_connect(url)
        except Exception as exc:
            logger.info("lobby connect failed: %s", exc)
            self._emit("error", {"message": f"lobby connect failed: {exc}"})
            return
        await self._send(self._register_message())
        self._reader = asyncio.ensure_future(self._read_loop())

    async def disconnect(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
            self._reader = None
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        self.connected = False

    def _register_message(self) -> dict[str, Any]:
        me = peer_hub.signer
        return {
            "type": "register",
            "node_id": me.node_id,
            "public_key": me.public_key,
            "node_name": identity.node_name(),
            "addresses": [trust.advertised_address()],
            "capabilities": peer_hub.capabilities(),
            # Proof of key ownership for the node_id we claim.
            "sig": me.sign(me.node_id.encode()),
        }

    async def _send(self, message: dict[str, Any]) -> None:
        if self._ws is None:
            return
        try:
            await self._ws.send(json.dumps(message))
        except ConnectionClosed:
            self.connected = False

    # ---- room operations (driven by the browser) ----------------------------------

    async def list_rooms(self) -> None:
        await self._send({"type": "list_rooms"})

    async def create_room(
        self,
        name: str,
        visibility: str = "public",
        join_policy: str = "open",
        token: str | None = None,
    ) -> None:
        await self._send(
            {
                "type": "create_room",
                "name": name,
                "visibility": visibility,
                "joinPolicy": join_policy,
                "token": token,
            }
        )

    async def join_room(self, room_id: str, token: str | None = None) -> None:
        await self._send({"type": "join_room", "roomId": room_id, "token": token})

    async def leave_room(self, room_id: str) -> None:
        await self._send({"type": "leave_room", "roomId": room_id})

    # ---- inbound ------------------------------------------------------------------

    async def _read_loop(self) -> None:
        try:
            while True:
                raw = await self._ws.recv()
                try:
                    msg = json.loads(raw if isinstance(raw, str) else raw.decode())
                except ValueError:
                    continue
                await self._dispatch(msg)
        except (ConnectionClosed, asyncio.CancelledError):
            pass
        except Exception:
            logger.exception("lobby read loop failed")
        finally:
            self.connected = False
            self._emit("state", self.snapshot())

    async def _dispatch(self, msg: dict[str, Any]) -> None:
        mtype = msg.get("type")
        if mtype == "registered":
            self.connected = True
            self.directory = msg.get("presence") or []
            self.rooms = msg.get("rooms") or []
            self._emit("state", self.snapshot())
        elif mtype == "rooms":
            self.rooms = msg.get("rooms") or []
            self._emit("rooms", {"rooms": self.rooms})
        elif mtype == "presence":
            self._merge_presence(msg.get("node") or {})
            self._emit("directory", {"directory": self.directory})
        elif mtype == "room_created":
            self._emit("room_created", {"room": msg.get("room") or {}})
        elif mtype == "room_info":
            await self._connect_to_host(msg)
        elif mtype == "peer_joining":
            # The guest dials us; nothing required here beyond surfacing it.
            self._emit(
                "peer_joining",
                {"guest": msg.get("guest") or {}, "roomId": msg.get("roomId")},
            )
        elif mtype == "signal":
            # Future ICE: candidates would be applied here. For now, plumbing only.
            logger.info("lobby signal from %s", msg.get("from"))
        elif mtype == "error":
            self._emit("error", {"message": msg.get("message", "lobby error")})

    def _merge_presence(self, node: dict[str, Any]) -> None:
        node_id = node.get("node_id")
        if not node_id:
            return
        self.directory = [n for n in self.directory if n.get("node_id") != node_id]
        if node.get("status") != "offline":
            self.directory.append(node)

    async def _connect_to_host(self, msg: dict[str, Any]) -> None:
        """A join was authorized — establish the peer link: direct first, then relay."""
        host = msg.get("host") or {}
        host_id = host.get("node_id")
        addresses = host.get("addresses") or []
        if not host_id:
            return
        if host_id in peer_hub.peers:
            self._emit("joined", {"roomId": msg.get("roomId"), "peer": host_id})
            return
        # 1) direct P2P to any advertised address.
        for address in addresses:
            try:
                info = await peer_hub.connect(address, "direct")
                self._emit(
                    "joined", {"roomId": msg.get("roomId"), "peer": info.node_id}
                )
                return
            except Exception as exc:
                logger.info("lobby direct dial %s failed: %s", address, exc)
        # 2) relay fallback through the lobby host.
        try:
            info = await peer_hub.connect(host_id, "relay")
            self._emit(
                "joined",
                {"roomId": msg.get("roomId"), "peer": info.node_id, "via": "relay"},
            )
        except Exception as exc:
            logger.info("lobby relay fallback to %s failed: %s", host_id, exc)
            self._emit("error", {"message": f"could not reach host {host_id}"})


lobby_client = LobbyClient()


# ---- /ws `lobby` channel bridge ---------------------------------------------------


def _evt(event: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"channel": "lobby", "event": event, "data": data}


def subscribe_lobby_conn(conn: Any) -> Any:
    """Fan lobby state/room events out to one browser connection. Returns the
    unsubscribe handle the `/ws` loop calls on disconnect."""

    def cb(event: str, data: dict[str, Any]) -> None:
        asyncio.ensure_future(conn.send_json(_evt(event, data)))

    return lobby_client.subscribe(cb)


async def handle_lobby_message(conn: Any, msg: dict[str, Any]) -> None:
    """Route an inbound `lobby`-channel message from the browser to the client."""
    event = msg.get("event")
    data = msg.get("data") or {}
    if event == "state":
        await conn.send_json(_evt("state", lobby_client.snapshot()))
    elif event == "connect":
        url = str(data.get("url") or get_value("network.lobbyUrl", "") or "").strip()
        if url:
            asyncio.create_task(lobby_client.connect(url))
    elif event == "disconnect":
        asyncio.create_task(lobby_client.disconnect())
    elif event == "list_rooms":
        await lobby_client.list_rooms()
    elif event == "create_room":
        await lobby_client.create_room(
            str(data.get("name") or ""),
            str(data.get("visibility") or "public"),
            str(data.get("joinPolicy") or "open"),
            data.get("token"),
        )
    elif event == "join_room":
        await lobby_client.join_room(str(data.get("roomId") or ""), data.get("token"))
    elif event == "leave_room":
        await lobby_client.leave_room(str(data.get("roomId") or ""))

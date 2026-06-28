"""Standalone **lobby** server: discovery + rooms + signaling, with the relay
broker bundled in for the fallback path.

The lobby is the "official intermediary" of the peer fabric. Unlike the dumb relay
(which only forwards by `dst`), the lobby keeps a **directory** of online nodes and a
registry of **rooms** (named, hostable sessions), and brokers the **signaling**
(address exchange) that bootstraps a direct P2P link. Bulk traffic still prefers
node-to-node `/peer-ws`; the lobby relays (over the reused `/relay-ws`) only when a
direct dial fails.

Run separately from a node's own backend:

    uv run uvicorn backend.modules.network.lobby_server:app --port 9000

See docs/architecture/network-protocol.mdx (the lobby system).
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from backend.modules.network import identity, relay_broker

logger = logging.getLogger(__name__)

app = FastAPI(title="horrible-dashboard lobby")

# Reuse the relay broker verbatim for the fallback path — same /relay-ws protocol,
# so a node's existing RelayTransport works against this host with no changes.
app.add_api_websocket_route("/relay-ws", relay_broker.relay_ws)


class _Node:
    """A connected node's lobby session."""

    def __init__(self, ws: WebSocket, info: dict[str, Any]) -> None:
        self.ws = ws
        self.node_id: str = info["node_id"]
        self.node_name: str = info.get("node_name") or self.node_id
        self.public_key: str = info["public_key"]
        self.addresses: list[str] = info.get("addresses") or []
        self.capabilities: list[str] = info.get("capabilities") or []
        self.status: str = "online"

    def directory_entry(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "node_name": self.node_name,
            "public_key": self.public_key,
            "capabilities": self.capabilities,
            "status": self.status,
        }


class _Room:
    def __init__(
        self,
        name: str,
        host: _Node,
        visibility: str,
        join_policy: str,
        token: str | None,
    ) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.name = name
        self.host = host
        self.visibility = visibility  # "public" | "unlisted"
        self.join_policy = join_policy  # "open" | "token" | "directory"
        self.token = token
        self.members: set[str] = {host.node_id}
        self.created = time.time()

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "host": self.host.node_id,
            "host_name": self.host.node_name,
            "members": len(self.members),
            "visibility": self.visibility,
            "locked": self.join_policy != "open",
        }


_nodes: dict[str, _Node] = {}
_rooms: dict[str, _Room] = {}


@app.get("/health")
def health() -> dict[str, object]:
    return {"status": "ok", "nodes": len(_nodes), "rooms": len(_rooms)}


async def _send(node: _Node, message: dict[str, Any]) -> None:
    try:
        await node.ws.send_text(json.dumps(message))
    except Exception:
        pass


async def _broadcast_rooms() -> None:
    listing = [r.summary() for r in _rooms.values() if r.visibility == "public"]
    for node in list(_nodes.values()):
        await _send(node, {"type": "rooms", "rooms": listing})


async def _broadcast_presence(entry: dict[str, Any]) -> None:
    for node in list(_nodes.values()):
        await _send(node, {"type": "presence", "node": entry})


def _verify_register(msg: dict[str, Any]) -> bool:
    """A node proves it holds the key for the node_id it claims (fingerprint match +
    signature over the node_id). Replay is possible without a server nonce, but it
    can't be leveraged — peer envelopes still require the private key — so directory
    spoofing is low-harm. A nonce challenge is a future hardening."""
    node_id = msg.get("node_id")
    public_key = msg.get("public_key")
    sig = msg.get("sig")
    if not (
        isinstance(node_id, str)
        and isinstance(public_key, str)
        and isinstance(sig, str)
    ):
        return False
    if identity.fingerprint(public_key) != node_id:
        return False
    return identity.verify(public_key, node_id.encode(), sig)


@app.websocket("/lobby-ws")
async def lobby_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    node: _Node | None = None
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            mtype = msg.get("type")

            if mtype == "register":
                if not _verify_register(msg):
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "error",
                                "code": "auth",
                                "message": "register verification failed",
                            }
                        )
                    )
                    continue
                node = _Node(websocket, msg)
                _nodes[node.node_id] = node
                await _send(
                    node,
                    {
                        "type": "registered",
                        "session": uuid.uuid4().hex[:8],
                        "presence": [n.directory_entry() for n in _nodes.values()],
                        "rooms": [
                            r.summary()
                            for r in _rooms.values()
                            if r.visibility == "public"
                        ],
                    },
                )
                await _broadcast_presence(node.directory_entry())
                continue

            if node is None:
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "error",
                            "code": "unregistered",
                            "message": "register first",
                        }
                    )
                )
                continue

            await _handle(node, mtype, msg)
    except WebSocketDisconnect:
        pass
    finally:
        if node is not None:
            _drop_node(node)
            await _broadcast_rooms()
            await _broadcast_presence({**node.directory_entry(), "status": "offline"})


async def _handle(node: _Node, mtype: str | None, msg: dict[str, Any]) -> None:
    if mtype == "presence":
        node.status = str(msg.get("status", node.status))
        caps = msg.get("capabilities")
        if isinstance(caps, list):
            node.capabilities = caps
        await _broadcast_presence(node.directory_entry())

    elif mtype == "list_rooms":
        await _send(
            node,
            {
                "type": "rooms",
                "rooms": [
                    r.summary() for r in _rooms.values() if r.visibility == "public"
                ],
            },
        )

    elif mtype == "create_room":
        room = _Room(
            name=str(msg.get("name") or f"{node.node_name}'s room"),
            host=node,
            visibility=str(msg.get("visibility", "public")),
            join_policy=str(msg.get("joinPolicy", "open")),
            token=msg.get("token"),
        )
        _rooms[room.id] = room
        await _send(node, {"type": "room_created", "room": room.summary()})
        await _broadcast_rooms()

    elif mtype == "join_room":
        await _join_room(node, msg)

    elif mtype == "leave_room":
        room = _rooms.get(str(msg.get("roomId", "")))
        if room is not None:
            room.members.discard(node.node_id)
            if node.node_id == room.host.node_id:
                _rooms.pop(room.id, None)  # host left → room closes
            await _broadcast_rooms()

    elif mtype in ("signal", "relay"):
        # Forward to the target node, tagging the sender.
        target = _nodes.get(str(msg.get("to", "")))
        if target is not None:
            await _send(target, {**msg, "from": node.node_id})


async def _join_room(node: _Node, msg: dict[str, Any]) -> None:
    room = _rooms.get(str(msg.get("roomId", "")))
    if room is None:
        await _send(
            node, {"type": "error", "code": "no_room", "message": "no such room"}
        )
        return
    # Authorize per join policy.
    if room.join_policy == "token" and msg.get("token") != room.token:
        await _send(
            node, {"type": "error", "code": "denied", "message": "invalid room token"}
        )
        return
    room.members.add(node.node_id)
    # Hand the joiner the host's reachable candidates; tell the host who's joining.
    await _send(
        node,
        {
            "type": "room_info",
            "roomId": room.id,
            "host": {
                "node_id": room.host.node_id,
                "node_name": room.host.node_name,
                "public_key": room.host.public_key,
                "addresses": room.host.addresses,
            },
        },
    )
    await _send(
        room.host,
        {
            "type": "peer_joining",
            "roomId": room.id,
            "guest": {
                "node_id": node.node_id,
                "node_name": node.node_name,
                "public_key": node.public_key,
                "addresses": node.addresses,
            },
        },
    )
    await _broadcast_rooms()


def _drop_node(node: _Node) -> None:
    if _nodes.get(node.node_id) is node:
        del _nodes[node.node_id]
    # Close rooms this node hosts; drop it from rooms it joined.
    for room_id in list(_rooms):
        room = _rooms[room_id]
        if room.host.node_id == node.node_id:
            del _rooms[room_id]
        else:
            room.members.discard(node.node_id)

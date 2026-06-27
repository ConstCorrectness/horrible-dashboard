"""Peer chat: direct 1:1 messaging between this node and a connected peer.

A browser opens a conversation with a peer over the `/ws` `peerchat` channel; the
backend relays the message to that peer over the signed peer wire (`peer_chat`
envelope) and mirrors it to this node's own browser tabs. Inbound peer messages are
fanned out to every local browser. History is held per-peer in memory so a reopened
panel gets the recent backlog.

This is the conversational counterpart to the `collab` channel (shared editable
state): `peerchat` is an append-only message log, `collab` is a synced document.
See docs/modules/network.mdx (Peer Chat).
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict, deque
from typing import TYPE_CHECKING, Any

from backend.modules.network import protocol

if TYPE_CHECKING:
    from backend.modules.network.hub import PeerHub, PeerSession
    from backend.modules.network.models import PeerEnvelope
    from backend.modules.ws import WsConnection

logger = logging.getLogger(__name__)

# Cap retained history per peer (older messages drop off).
HISTORY_LIMIT = 200


def _evt(event: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"channel": "peerchat", "event": event, "data": data}


class ChatManager:
    """Process-global registry of peer conversations and subscribed browser tabs."""

    def __init__(self) -> None:
        # node_id -> recent messages (each: id, from, text, ts, direction).
        self._history: dict[str, deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=HISTORY_LIMIT)
        )
        self._members: set[WsConnection] = set()

    def _record(self, node_id: str, message: dict[str, Any]) -> None:
        self._history[node_id].append(message)

    async def _fan_out(self, message: dict[str, Any]) -> None:
        payload = _evt("message", message)
        for conn in list(self._members):
            try:
                await conn.send_json(payload)
            except Exception:
                self._members.discard(conn)

    async def handle(self, conn: WsConnection, msg: dict[str, Any]) -> None:
        event = msg.get("event")
        data = msg.get("data") or {}
        if event == "open":
            # The panel subscribes and asks for the backlog of one conversation.
            self._members.add(conn)
            node_id = str(data.get("nodeId", ""))
            await conn.send_json(
                _evt(
                    "history",
                    {
                        "nodeId": node_id,
                        "messages": list(self._history.get(node_id, [])),
                    },
                )
            )
        elif event == "send":
            await self._send(conn, data)
        elif event == "close":
            self._members.discard(conn)

    async def _send(self, conn: WsConnection, data: dict[str, Any]) -> None:
        from backend.modules.network.hub import peer_hub

        node_id = str(data.get("nodeId", ""))
        text = str(data.get("text", "")).strip()
        if not node_id or not text:
            return
        me = peer_hub.identity().node_name
        message = {
            "id": uuid.uuid4().hex,
            "nodeId": node_id,
            "from": me,
            "text": text,
            "ts": time.time(),
            "direction": "out",
        }
        try:
            await peer_hub.send_to(
                node_id, protocol.PEER_CHAT, {"text": text, "from_name": me}
            )
        except KeyError:
            await conn.send_json(
                _evt("error", {"nodeId": node_id, "message": "peer not connected"})
            )
            return
        self._record(node_id, message)
        await self._fan_out(message)

    async def apply_peer_chat(self, env: PeerEnvelope) -> None:
        """A chat message arrived from a peer — record it and fan out to browsers."""
        node_id = env.src
        text = str(env.data.get("text", ""))
        from_name = str(env.data.get("from_name", node_id))
        if not text:
            return
        message = {
            "id": env.msg_id,
            "nodeId": node_id,
            "from": from_name,
            "text": text,
            "ts": env.ts,
            "direction": "in",
        }
        self._record(node_id, message)
        await self._fan_out(message)

    def drop(self, conn: WsConnection) -> None:
        self._members.discard(conn)


chat_manager = ChatManager()


async def handle_chat_message(conn: WsConnection, msg: dict[str, Any]) -> None:
    await chat_manager.handle(conn, msg)


async def handle_peer_chat(
    hub: PeerHub, session: PeerSession, env: PeerEnvelope
) -> None:
    await chat_manager.apply_peer_chat(env)

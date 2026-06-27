"""Collaborative shared panes (groundwork).

A `CollabRoom` (keyed by an opaque `pane_key`) holds the authoritative text + a
monotonic revision. Local browser members sync through it with a rev check —
last-writer-wins, but no *lost* updates: an op whose `base_rev` is stale is rejected
and the writer rebases onto the authoritative state. Accepted ops are also forwarded
to connected peers (and inbound peer ops applied as authoritative by rev), so a pane
can be shared across users on different nodes.

This is deliberately not a CRDT — it's the seam a real one would slot into later.
The reference consumer is the scratch panel's "Share" affordance. See
docs/modules/network.mdx (collab) and docs/modules/scratch.mdx.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from backend.modules.network import protocol

if TYPE_CHECKING:
    from backend.modules.network.hub import PeerHub, PeerSession
    from backend.modules.network.models import PeerEnvelope
    from backend.modules.ws import WsConnection

logger = logging.getLogger(__name__)


def _evt(event: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"channel": "collab", "event": event, "data": data}


class CollabRoom:
    def __init__(self, key: str) -> None:
        self.key = key
        self.text = ""
        self.rev = 0
        self.members: set[WsConnection] = set()


class CollabManager:
    """Process-global registry of shared-pane rooms."""

    def __init__(self) -> None:
        self.rooms: dict[str, CollabRoom] = {}

    def _room(self, key: str) -> CollabRoom:
        room = self.rooms.get(key)
        if room is None:
            room = CollabRoom(key)
            self.rooms[key] = room
        return room

    async def handle(self, conn: WsConnection, msg: dict[str, Any]) -> None:
        event = msg.get("event")
        data = msg.get("data") or {}
        key = str(data.get("paneKey", ""))
        if not key:
            return
        if event == "join":
            room = self._room(key)
            room.members.add(conn)
            await conn.send_json(
                _evt(
                    "state",
                    {
                        "paneKey": key,
                        "rev": room.rev,
                        "text": room.text,
                        "members": len(room.members),
                    },
                )
            )
        elif event == "leave":
            room = self.rooms.get(key)
            if room is not None:
                room.members.discard(conn)
        elif event == "op":
            await self._local_op(conn, key, data)

    async def _local_op(
        self, conn: WsConnection, key: str, data: dict[str, Any]
    ) -> None:
        room = self._room(key)
        base_rev = int(data.get("baseRev", -1))
        text = str(data.get("text", ""))
        if base_rev != room.rev:
            # Stale write — hand back the authoritative state so the writer rebases.
            await conn.send_json(
                _evt("rejected", {"paneKey": key, "rev": room.rev, "text": room.text})
            )
            return
        room.text = text
        room.rev += 1
        # Echo to every member (including the sender) so each tracks the new rev for
        # its next op; the sender's text is unchanged, so its editor doesn't jump.
        await self._broadcast(room, exclude=None, frm="local")
        await self._forward_to_peers(room)

    async def _broadcast(
        self, room: CollabRoom, *, exclude: WsConnection | None, frm: str
    ) -> None:
        payload = _evt(
            "op", {"paneKey": room.key, "rev": room.rev, "text": room.text, "from": frm}
        )
        for member in list(room.members):
            if member is exclude:
                continue
            try:
                await member.send_json(payload)
            except Exception:
                room.members.discard(member)

    async def _forward_to_peers(self, room: CollabRoom) -> None:
        from backend.modules.network.hub import peer_hub

        for node_id in list(peer_hub.peers):
            try:
                await peer_hub.send_to(
                    node_id,
                    protocol.COLLAB_OP,
                    {"paneKey": room.key, "rev": room.rev, "text": room.text},
                )
            except Exception:
                pass

    async def apply_peer_op(self, env: PeerEnvelope) -> None:
        """An op arrived from a peer. Adopt it as authoritative by revision (LWW) and
        rebroadcast to local members. Not re-forwarded to peers — that would loop."""
        data = env.data
        key = str(data.get("paneKey", ""))
        if not key:
            return
        rev = int(data.get("rev", 0))
        room = self._room(key)
        if rev < room.rev:
            return  # an older revision; ignore
        room.text = str(data.get("text", ""))
        room.rev = rev
        await self._broadcast(room, exclude=None, frm=env.src)

    def drop(self, conn: WsConnection) -> None:
        for room in self.rooms.values():
            room.members.discard(conn)


collab_manager = CollabManager()


async def handle_collab_message(conn: WsConnection, msg: dict[str, Any]) -> None:
    await collab_manager.handle(conn, msg)


async def handle_peer_collab_op(
    hub: PeerHub, session: PeerSession, env: PeerEnvelope
) -> None:
    await collab_manager.apply_peer_op(env)

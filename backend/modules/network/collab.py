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

import asyncio
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
        #: The **nodes** this pane is actually shared with. Membership is explicit:
        #: a node lands here when you share the pane with its owner, or when an op
        #: for this pane arrives from it (which only happens if they shared with
        #: you). Without this set, forwarding fell back to "every peer connected",
        #: so every scratch pane's full text went to everyone you had a link with.
        self.peers: set[str] = set()


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
            # Tell everyone else the occupancy changed so presence stays live.
            await self._broadcast_presence(room, exclude=conn)
        elif event == "leave":
            room = self.rooms.get(key)
            if room is not None:
                room.members.discard(conn)
                await self._broadcast_presence(room, exclude=None)
        elif event == "op":
            await self._local_op(conn, key, data)
        elif event == "share":
            await self._share(conn, key, str(data.get("personId", "")))
        elif event == "unshare":
            await self._unshare(conn, key, str(data.get("personId", "")))

    # ---- sharing ------------------------------------------------------------
    #
    # Addressed to a **person**, never to a node. You share a pane with Andrew and
    # the fabric picks whichever of his machines is up; asking a human which of
    # their own computers to send something to is a question with no good answer.

    async def _share(self, conn: WsConnection, key: str, person_id: str) -> None:
        from backend.modules.social import roster

        if not person_id:
            return
        nodes = roster.reachable_nodes(person_id)
        if not nodes:
            await conn.send_json(
                _evt(
                    "error",
                    {"paneKey": key, "message": "none of their machines is online"},
                )
            )
            return
        room = self._room(key)
        room.peers.update(nodes)
        # Push the current state so they open on what you are actually looking at,
        # rather than an empty pane that fills in on your next keystroke.
        await self._forward_to_peers(room, only=set(nodes))
        await self._broadcast_shared(room)

    async def _unshare(self, conn: WsConnection, key: str, person_id: str) -> None:
        from backend.modules.social import store as social_store

        room = self.rooms.get(key)
        if room is None or not person_id:
            return
        for device in social_store.list_devices(person_id):
            room.peers.discard(str(device["node_id"]))
        await self._broadcast_shared(room)

    async def _broadcast_shared(self, room: CollabRoom) -> None:
        """Who this pane is shared with, by person — so the pane can say so.

        Resolved to people here rather than in the browser: the node holds the
        device→person map, and a pane that listed node ids would be exactly the
        machine-shaped UI the roster exists to replace.
        """
        from backend.modules.social import store as social_store

        people: dict[str, str] = {}
        for node_id in room.peers:
            person_id = social_store.person_for_node(node_id)
            if person_id is None:
                continue
            row = social_store.get_friend_row(person_id)
            people[person_id] = str(row["display_name"]) if row else person_id
        payload = _evt(
            "shared",
            {
                "paneKey": room.key,
                "people": [{"personId": p, "name": n} for p, n in people.items()],
            },
        )
        for member in list(room.members):
            try:
                await member.send_json(payload)
            except Exception:
                room.members.discard(member)

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

    async def _broadcast_presence(
        self, room: CollabRoom, *, exclude: WsConnection | None
    ) -> None:
        """Tell a room's members how many are now present, so each pane can show a
        live peer-presence count (this node's local browsers; peers are remote)."""
        payload = _evt("presence", {"paneKey": room.key, "members": len(room.members)})
        for member in list(room.members):
            if member is exclude:
                continue
            try:
                await member.send_json(payload)
            except Exception:
                room.members.discard(member)

    async def _forward_to_peers(
        self, room: CollabRoom, *, only: set[str] | None = None
    ) -> None:
        """Send an op to the nodes **this room is shared with**, and nobody else.

        This used to iterate `peer_hub.peers` — every node with a live session —
        so the full text of every shared pane was delivered to everyone you were
        connected to, whether or not they had any part in it. Sharing was a global
        broadcast wearing the word "room".
        """
        from backend.modules.network.hub import peer_hub

        targets = (room.peers & set(peer_hub.peers)) if only is None else only
        for node_id in targets:
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
        # They sent us an op for this pane, so they are in it — which is how the
        # far side of a share learns to route our edits back to them.
        room.peers.add(env.src)
        if rev < room.rev:
            return  # an older revision; ignore
        room.text = str(data.get("text", ""))
        room.rev = rev
        await self._broadcast(room, exclude=None, frm=env.src)

    def drop(self, conn: WsConnection) -> None:
        for room in self.rooms.values():
            if conn in room.members:
                room.members.discard(conn)
                # Schedule a presence refresh (drop is called synchronously from the
                # /ws teardown, but a loop is running, so fire-and-forget is fine).
                asyncio.ensure_future(self._broadcast_presence(room, exclude=None))


collab_manager = CollabManager()


async def handle_collab_message(conn: WsConnection, msg: dict[str, Any]) -> None:
    await collab_manager.handle(conn, msg)


async def handle_peer_collab_op(
    hub: PeerHub, session: PeerSession, env: PeerEnvelope
) -> None:
    # Trust is the gate, not knowledge of a pane key. An untrusted peer that
    # guessed or overheard a key could otherwise both read the pane (we would
    # start forwarding to them) and rewrite it, since ops are last-writer-wins.
    # Same rule the hassault fabric applies to inbound match traffic.
    if not getattr(session.info, "trusted", False):
        logger.warning("ignoring a collab op from untrusted peer %s", env.src)
        return
    await collab_manager.apply_peer_op(env)

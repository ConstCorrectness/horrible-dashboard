"""Live co-editing of a page: a relay for Yjs between browsers and machines.

A **host** — the machine whose site has the page — opens a live session from the page
pane and invites people. Their machines receive the invitation over the peer fabric;
joining opens the page in a live pane on their side. Every keystroke travels as a Yjs
update (a CRDT), so concurrent edits merge instead of one overwriting the other — the
network module's `collab` rooms are last-writer-wins, which is fine for a scratch note
and wrong for two people writing the same paragraph.

This module does not understand Yjs. It relays opaque messages (base64 of y-protocols
sync and awareness frames, built by `live/provider.ts`) and enforces who may send what:

- **Star through the host.** A guest's machine talks only to the host's; the host's
  relays each message to its own browsers and to every other guest. The host's browser
  holds the document that gets saved: the page on disk stays the one truth, written by
  the host's ordinary Save.
- **Invited by person, delivered by node** (as `network/collab.py` does): an invite goes
  to the person's machines that are online, and only those nodes are in the room.
- **Trust is the gate, then membership.** A fabric message from an untrusted peer is
  dropped; a trusted peer's message for a room is accepted only if its node is in that
  room — on the host, an invited node; on a guest, the host. Knowing a room key is not
  permission. Room keys are random besides.
- **Bounded frames.** The fabric severs a link over 1 MiB, so a message over
  `MAX_MESSAGE_CHARS` is refused with an error to its sender instead.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from backend.modules.network.hub import PeerHub, PeerSession
    from backend.modules.network.models import PeerEnvelope
    from backend.modules.ws import WsConnection

logger = logging.getLogger(__name__)

CHANNEL = "scrive-live"
#: The fabric message kind.
PEER_KIND = "scrive_live"
#: Base64 characters per relayed message (≈ 600 KB of Yjs), well under the fabric's
#: 1 MiB frame limit after the signed envelope around it.
MAX_MESSAGE_CHARS = 800_000
#: How long a host room outlives its last browser: a socket that reconnects (a
#: reload, a sleeping laptop waking) rejoins the same session instead of ending it.
HOST_GRACE_S = 30.0


@dataclass
class Room:
    key: str
    role: Literal["host", "guest"]
    site: str
    path: str
    title: str
    #: The host's node id (guest rooms) — the only node a guest room talks to.
    host_node: str = ""
    host_name: str = ""
    members: set[WsConnection] = field(default_factory=set)
    #: Host rooms: the invited nodes. Guest rooms: just the host.
    peers: set[str] = field(default_factory=set)

    def summary(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "role": self.role,
            "site": self.site,
            "path": self.path,
            "title": self.title,
            "host": self.host_name,
        }


def _evt(event: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"channel": CHANNEL, "event": event, "data": data}


async def _send(conn: WsConnection, event: str, data: dict[str, Any]) -> bool:
    try:
        await conn.send_json(_evt(event, data))
        return True
    except Exception:  # noqa: BLE001 — a dead socket is cleaned up by the /ws loop
        return False


class LiveRelay:
    def __init__(self) -> None:
        self.rooms: dict[str, Room] = {}

    # --- browsers ---------------------------------------------------------------

    async def handle(self, conn: WsConnection, msg: dict[str, Any]) -> None:
        event = str(msg.get("event") or "")
        data = msg.get("data") or {}
        if event == "host":
            await self._host(conn, data)
            return
        if event == "invitations":
            await _send(
                conn,
                "invitations",
                {
                    "rooms": [
                        r.summary() for r in self.rooms.values() if r.role == "guest"
                    ]
                },
            )
            return
        key = str(data.get("key") or "")
        room = self.rooms.get(key)
        if room is None:
            await _send(
                conn, "error", {"key": key, "message": "That live session has ended."}
            )
            return
        if event == "join":
            room.members.add(conn)
            await _send(
                conn, "joined", {**room.summary(), "people": self._people(room)}
            )
        elif event == "leave":
            await self._leave(room, conn)
        elif event == "msg":
            await self._local_message(room, conn, str(data.get("data") or ""))
        elif event == "invite" and room.role == "host":
            await self._invite(room, conn, str(data.get("personId") or ""))
        elif event == "uninvite" and room.role == "host":
            await self._uninvite(room, str(data.get("personId") or ""))
        elif event == "end" and room.role == "host":
            await self._end(room)

    async def _host(self, conn: WsConnection, data: dict[str, Any]) -> None:
        site = str(data.get("site") or "")
        path = str(data.get("path") or "")
        if not site or not path:
            return
        # One live session per page on this machine.
        for room in self.rooms.values():
            if room.role == "host" and room.site == site and room.path == path:
                # Another pane here holds the document: join it (`existing`), and
                # sync from that pane. Nobody here holds it (a reload inside the
                # grace period): this pane seeds a fresh document from the page, and
                # the guests drop theirs and resync (`reset`) — two documents seeded
                # separately would merge into the page twice over.
                existing = bool(room.members)
                room.members.add(conn)
                if not existing:
                    await self._to_peers(room, room.peers, {"op": "reset"})
                await _send(
                    conn,
                    "hosted",
                    {
                        **room.summary(),
                        "people": self._people(room),
                        "existing": existing,
                    },
                )
                return
        room = Room(
            key=secrets.token_urlsafe(16),
            role="host",
            site=site,
            path=path,
            title=str(data.get("title") or path)[:200],
        )
        room.members.add(conn)
        self.rooms[room.key] = room
        await _send(conn, "hosted", {**room.summary(), "people": [], "existing": False})

    async def _local_message(
        self, room: Room, conn: WsConnection, payload: str
    ) -> None:
        if not payload:
            return
        if len(payload) > MAX_MESSAGE_CHARS:
            await _send(
                conn,
                "error",
                {"key": room.key, "message": "That change is too large to send live."},
            )
            return
        await self._to_members(room, payload, exclude=conn)
        targets = room.peers if room.role == "host" else {room.host_node}
        await self._to_peers(room, targets, {"op": "msg", "data": payload})

    async def _to_members(
        self, room: Room, payload: str, *, exclude: WsConnection | None
    ) -> None:
        for member in list(room.members):
            if member is exclude:
                continue
            if not await _send(member, "msg", {"key": room.key, "data": payload}):
                room.members.discard(member)

    async def _to_peers(
        self, room: Room, nodes: set[str], data: dict[str, Any]
    ) -> None:
        from backend.modules.network.hub import peer_hub

        for node in nodes & set(peer_hub.peers):
            try:
                await peer_hub.send_to(node, PEER_KIND, {"key": room.key, **data})
            except Exception:  # noqa: BLE001 — a dropped link is the fabric's to report
                logger.debug("live: could not reach %s", node, exc_info=True)

    def _people(self, room: Room) -> list[dict[str, str]]:
        """Who the room is shared with, as people (the node knows device → person)."""
        if room.role == "guest":
            return [{"personId": "", "name": room.host_name}] if room.host_name else []
        from backend.modules.social import store as social_store

        people: dict[str, str] = {}
        for node in room.peers:
            person = social_store.person_for_node(node)
            if person is None:
                continue
            row = social_store.get_friend_row(person)
            people[person] = str(row["display_name"]) if row else person
        return [{"personId": p, "name": n} for p, n in people.items()]

    async def _announce_people(self, room: Room) -> None:
        payload = {"key": room.key, "people": self._people(room)}
        for member in list(room.members):
            await _send(member, "people", payload)

    async def _invite(self, room: Room, conn: WsConnection, person_id: str) -> None:
        from backend.modules.social import roster

        nodes = roster.reachable_nodes(person_id) if person_id else []
        if not nodes:
            await _send(
                conn,
                "error",
                {"key": room.key, "message": "None of their machines is online."},
            )
            return
        room.peers.update(nodes)
        host_name = roster.self_profile().display_name
        await self._to_peers(
            room,
            set(nodes),
            {
                "op": "invite",
                "site": room.site,
                "path": room.path,
                "title": room.title,
                "host": host_name,
            },
        )
        await self._announce_people(room)

    async def _uninvite(self, room: Room, person_id: str) -> None:
        from backend.modules.social import store as social_store

        nodes = {str(d["node_id"]) for d in social_store.list_devices(person_id)}
        gone = room.peers & nodes
        room.peers -= gone
        await self._to_peers(room, gone, {"op": "end"})
        await self._announce_people(room)

    async def _leave(
        self, room: Room, conn: WsConnection, *, grace: bool = False
    ) -> None:
        room.members.discard(conn)
        if room.role != "host" or room.members:
            return
        # The host's last pane closed: nobody holds the document to save it. A
        # dropped socket gets a moment to come back first.
        if grace:
            import asyncio

            await asyncio.sleep(HOST_GRACE_S)
            if self.rooms.get(room.key) is not room or room.members:
                return
        await self._end(room)

    async def _end(self, room: Room) -> None:
        self.rooms.pop(room.key, None)
        if room.role == "host":
            await self._to_peers(room, room.peers, {"op": "end"})
        for member in list(room.members):
            await _send(member, "ended", {"key": room.key})

    def drop(self, conn: WsConnection) -> None:
        """A browser socket closed (called synchronously from the /ws teardown)."""
        import asyncio

        for room in list(self.rooms.values()):
            if conn in room.members:
                asyncio.ensure_future(self._leave(room, conn, grace=True))

    # --- the fabric -------------------------------------------------------------

    async def from_peer(self, src: str, data: dict[str, Any]) -> None:
        op = str(data.get("op") or "")
        key = str(data.get("key") or "")
        if not key:
            return
        room = self.rooms.get(key)
        if op == "invite":
            if room is not None and room.role == "host":
                return  # our own key echoed back: never a guest of ourselves
            room = room or Room(
                key=key,
                role="guest",
                site=str(data.get("site") or "")[:200],
                path=str(data.get("path") or "")[:500],
                title=str(data.get("title") or "")[:200],
            )
            if room.host_node and room.host_node != src:
                return  # a second node claiming someone else's room
            room.host_node = src
            room.host_name = str(data.get("host") or "")[:100]
            room.peers = {src}
            self.rooms[key] = room
            from backend.modules.ws import broadcast_event

            await broadcast_event("scrive", "live.invited", room.summary())
            return
        if room is None or src not in room.peers:
            return  # not a room this node shares with them
        if op == "msg":
            payload = str(data.get("data") or "")
            if not payload or len(payload) > MAX_MESSAGE_CHARS:
                return
            await self._to_members(room, payload, exclude=None)
            if room.role == "host":
                await self._to_peers(
                    room, room.peers - {src}, {"op": "msg", "data": payload}
                )
        elif op == "reset" and room.role == "guest":
            for member in list(room.members):
                await _send(member, "reset", {"key": key})
        elif op == "end" and room.role == "guest":
            self.rooms.pop(key, None)
            for member in list(room.members):
                await _send(member, "ended", {"key": key})
            from backend.modules.ws import broadcast_event

            await broadcast_event("scrive", "live.ended", {"key": key})


relay = LiveRelay()


async def handle_live_message(conn: WsConnection, msg: dict[str, Any]) -> None:
    await relay.handle(conn, msg)


async def handle_peer_live(
    hub: PeerHub, session: PeerSession, env: PeerEnvelope
) -> None:
    if not getattr(session.info, "trusted", False):
        logger.warning(
            "ignoring a live-editing message from untrusted peer %s", env.src
        )
        return
    await relay.from_peer(env.src, env.data or {})


def register() -> None:
    """Teach the peer fabric this module's message kind. Serial: two Yjs updates
    applied out of order still converge, but relaying them in order keeps every
    guest's view of the host's typing smooth."""
    from backend.modules.network.hub import peer_hub

    peer_hub.register_handler(PEER_KIND, handle_peer_live, mode="serial")

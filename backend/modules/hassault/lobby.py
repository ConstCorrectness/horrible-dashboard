"""Lobbies: a party you gather *before* a match, on the host's node.

Inviting a friend used to host a match on the spot and invite them into it, so the
inviter was dropped into a live map, alone, while the invite was still in flight.
A lobby is the room in front of that: the host picks a map, friends join and chat
and mark themselves ready, and **Start** opens a match and sends everybody into
it together. The lobby outlives the match — leaving the match puts you back in the
lobby with the same people, which is what "queue together" means for a party.

It follows the karaoke session's shape, because it is the same problem: the lobby
is **process-global on the host** and every mutation pushes the *whole* state to
every member, stamped with a `revision`. A member is a renderer, not an owner, so
a browser that missed a message only has to wait for the next one.

Members are either a local browser socket or a `PeerLobbyConn` — a friend's
browser proxied by their own backend over the peer fabric, exactly as
`fabric.PeerPlayerConn` stands in for a remote *player*. `LobbyServer` only ever
calls `send_json`, so it never learns which members are remote.

Trust is the fabric's: every inbound peer message is gated on
`session.info.trusted` in `fabric.handle_lobby`, the same gate as a match join.

See docs/modules/hassault.mdx.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Protocol

from backend.modules.hassault.match import CHANNEL, MAX_PLAYERS, match_server

logger = logging.getLogger(__name__)

#: Chat lines a lobby keeps. Sent whole in every state push, so it is short on
#: purpose: a lobby is a waiting room, not a message archive.
CHAT_HISTORY = 50
MAX_CHAT_LEN = 200
MAX_NAME_LEN = 24
#: WebRTC handshake frames the lobby will pass between members, and the most one
#: may weigh. An audio-only SDP is a few KB; anything far larger is not a handshake.
SIGNAL_KINDS = frozenset({"offer", "answer", "ice", "bye"})
MAX_SIGNAL_BYTES = 32_768


class Sink(Protocol):
    async def send_json(self, data: dict[str, Any]) -> None: ...


@dataclass
class LobbyMember:
    id: str
    name: str
    conn: Sink
    #: '' for a browser on this node, else the guest's node id. Carried so the
    #: state can say who is remote, and so a dropped peer takes its members with it.
    node: str = ""
    ready: bool = False
    #: In the lobby's voice channel, and whether their mic is muted. Published so
    #: every member's pane knows whom to connect audio to — the audio itself is
    #: browser to browser, never through here (see `signal`).
    voice: bool = False
    muted: bool = False
    joined_at: float = field(default_factory=time.time)

    def public(self, host_id: str) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "ready": self.ready,
            "host": self.id == host_id,
            "remote": bool(self.node),
            "voice": self.voice,
            "muted": self.muted,
        }


@dataclass
class Lobby:
    id: str
    host_id: str
    map_name: str
    mode: str | None
    members: dict[str, LobbyMember] = field(default_factory=dict)
    chat: deque[dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=CHAT_HISTORY)
    )
    #: The match this lobby most recently started, '' before the first start.
    #: Kept so a member who returns to the menu can rejoin a match still running.
    room: str = ""
    revision: int = 0
    created_at: float = field(default_factory=time.time)

    def state(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "map": self.map_name,
            "mode": self.mode or "",
            "room": self.room if match_server.get(self.room) else "",
            "revision": self.revision,
            "maxPlayers": MAX_PLAYERS,
            "members": [
                m.public(self.host_id)
                for m in sorted(self.members.values(), key=lambda m: m.joined_at)
            ],
            "chat": list(self.chat),
        }


def _evt(event: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"channel": CHANNEL, "event": event, "data": data}


class LobbyError(Exception):
    """A refusal to show the member, never a crash."""


class LobbyServer:
    def __init__(self) -> None:
        self.lobbies: dict[str, Lobby] = {}
        #: id(conn) -> (lobby id, member id). A socket is in at most one lobby.
        self.membership: dict[int, tuple[str, str]] = {}

    # -- lookup -------------------------------------------------------------

    def get(self, lobby_id: str) -> Lobby | None:
        return self.lobbies.get(lobby_id)

    def lobby_for(self, conn: Sink) -> tuple[Lobby, LobbyMember] | None:
        entry = self.membership.get(id(conn))
        if entry is None:
            return None
        lobby = self.lobbies.get(entry[0])
        member = lobby.members.get(entry[1]) if lobby else None
        return (lobby, member) if lobby and member else None

    # -- membership ---------------------------------------------------------

    async def create(
        self, conn: Sink, name: str, map_name: str, mode: str | None = None
    ) -> Lobby:
        """Open a lobby hosted by `conn`, leaving any lobby it was in first."""
        await self.leave(conn)
        member = LobbyMember(uuid.uuid4().hex[:8], name[:MAX_NAME_LEN] or "host", conn)
        lobby = Lobby(uuid.uuid4().hex[:8], member.id, map_name, mode or None)
        lobby.members[member.id] = member
        self.lobbies[lobby.id] = lobby
        self.membership[id(conn)] = (lobby.id, member.id)
        await self.broadcast(lobby)
        return lobby

    async def ensure(
        self, conn: Sink, name: str, map_name: str, mode: str | None = None
    ) -> Lobby:
        """The lobby `conn` is in, or a new one it hosts."""
        entry = self.lobby_for(conn)
        return entry[0] if entry else await self.create(conn, name, map_name, mode)

    async def join(self, conn: Sink, lobby_id: str, name: str, node: str = "") -> Lobby:
        lobby = self.lobbies.get(lobby_id)
        if lobby is None:
            raise LobbyError("that lobby has closed")
        entry = self.lobby_for(conn)
        if entry and entry[0].id == lobby_id:
            # Accepting the same invite twice is a no-op, not a second seat.
            await self.send_state(lobby, entry[1])
            return lobby
        if len(lobby.members) >= MAX_PLAYERS:
            raise LobbyError("that lobby is full")
        await self.leave(conn)
        member = LobbyMember(
            uuid.uuid4().hex[:8], name[:MAX_NAME_LEN] or "guest", conn, node=node
        )
        lobby.members[member.id] = member
        self.membership[id(conn)] = (lobby.id, member.id)
        self._system(lobby, f"{member.name} joined")
        await self.broadcast(lobby)
        return lobby

    async def leave(self, conn: Sink) -> None:
        """Take `conn` out of its lobby. The host leaving **closes** it.

        Not handed over to a guest: the lobby lives on the host's node, and the
        match it starts is simulated there, so a lobby whose host has gone has
        nowhere to start anything.
        """
        entry = self.membership.pop(id(conn), None)
        if entry is None:
            return
        lobby = self.lobbies.get(entry[0])
        if lobby is None:
            return
        member = lobby.members.pop(entry[1], None)
        if member is None:
            return
        if member.id == lobby.host_id:
            await self.close(lobby, f"{member.name} closed the lobby")
            return
        self._system(lobby, f"{member.name} left")
        await self.broadcast(lobby)

    async def close(self, lobby: Lobby, reason: str) -> None:
        self.lobbies.pop(lobby.id, None)
        for member in list(lobby.members.values()):
            self.membership.pop(id(member.conn), None)
            await self._send(
                member, _evt("lobby_closed", {"id": lobby.id, "reason": reason})
            )
        lobby.members.clear()

    # -- actions ------------------------------------------------------------

    async def chat(self, conn: Sink, text: str) -> None:
        entry = self._require(conn)
        lobby, member = entry
        text = text.strip()[:MAX_CHAT_LEN]
        if not text:
            return
        lobby.chat.append(
            {
                "from": member.name,
                "memberId": member.id,
                "text": text,
                "ts": time.time(),
            }
        )
        await self.broadcast(lobby)

    async def set_ready(self, conn: Sink, ready: bool) -> None:
        lobby, member = self._require(conn)
        if member.ready != ready:
            member.ready = ready
            await self.broadcast(lobby)

    async def set_voice(self, conn: Sink, on: bool, muted: bool) -> None:
        lobby, member = self._require(conn)
        if (member.voice, member.muted) != (on, muted and on):
            member.voice = on
            member.muted = muted and on
            await self.broadcast(lobby)

    async def signal(self, conn: Sink, to: str, payload: Any) -> None:
        """Pass one WebRTC handshake frame (SDP or ICE) to another member.

        The lobby is only the *postman*: voice is a mesh of browser-to-browser
        connections, and this carries their offers, answers and candidates. The
        sender is stamped from the socket it arrived on — never from the frame —
        so nobody can make a frame look like it came from someone else, and both
        ends must be members of this lobby.
        """
        lobby, member = self._require(conn)
        target = lobby.members.get(to)
        if target is None or target.id == member.id:
            return
        if not isinstance(payload, dict) or payload.get("kind") not in SIGNAL_KINDS:
            return
        if len(json.dumps(payload)) > MAX_SIGNAL_BYTES:
            return
        await self._send(
            target, _evt("lobby_signal", {"from": member.id, "signal": payload})
        )

    async def configure(self, conn: Sink, map_name: str, mode: str | None) -> None:
        """Host only. Changing the map un-readies everyone: a "ready" given for
        one map is not consent to another."""
        lobby, member = self._require(conn)
        if member.id != lobby.host_id:
            raise LobbyError("only the host can change the map")
        if map_name and map_name != lobby.map_name:
            lobby.map_name = map_name
            for other in lobby.members.values():
                other.ready = False
            self._system(lobby, f"map set to {map_name}")
        if mode is not None:
            lobby.mode = mode or None
        await self.broadcast(lobby)

    async def start(self, conn: Sink) -> str:
        """Host only: open a match on the lobby's map and send everyone into it.

        The room is created *empty* and each member joins it themselves — the
        host's browser like any other. Joining is what binds a socket (or a peer's
        proxy) to a player, and doing it for them from here would mean a second
        join path. An empty room survives `EMPTY_GRACE`, which is ample for a
        member's pane to act on `lobby_start`.

        A match the lobby started that is still running is reused rather than
        replaced, so pressing Start again sends latecomers into the same game.
        """
        lobby, member = self._require(conn)
        if member.id != lobby.host_id:
            raise LobbyError("only the host can start the match")
        room = match_server.get(lobby.room) if lobby.room else None
        if room is None:
            try:
                room = match_server.create(lobby.map_name, mode=lobby.mode)
            except (LookupError, ValueError) as exc:
                raise LobbyError(str(exc)) from exc
            lobby.room = room.id
        for other in lobby.members.values():
            other.ready = False
        self._system(lobby, "match started")
        await self.broadcast(lobby)
        start = {
            "id": lobby.id,
            "room": room.id,
            "map": room.map_name,
            "mode": room.mode.id,
        }
        for other in list(lobby.members.values()):
            await self._send(other, _evt("lobby_start", start))
        return room.id

    # -- fan-out ------------------------------------------------------------

    async def broadcast(self, lobby: Lobby) -> None:
        lobby.revision += 1
        for member in list(lobby.members.values()):
            await self.send_state(lobby, member)

    async def send_state(self, lobby: Lobby, member: LobbyMember) -> None:
        data = lobby.state()
        # Per recipient, so a pane knows which row is itself and whether it holds
        # the host's controls — without trusting a name comparison.
        data["you"] = member.id
        data["isHost"] = member.id == lobby.host_id
        await self._send(member, _evt("lobby", data))

    async def _send(self, member: LobbyMember, message: dict[str, Any]) -> None:
        try:
            await member.conn.send_json(message)
        except Exception:
            # A closed socket is swept by its own disconnect path; one dead member
            # must not stop the rest of the lobby hearing about it.
            logger.debug("lobby send to %s failed", member.name, exc_info=True)

    def _require(self, conn: Sink) -> tuple[Lobby, LobbyMember]:
        entry = self.lobby_for(conn)
        if entry is None:
            raise LobbyError("you are not in a lobby")
        return entry

    def _system(self, lobby: Lobby, text: str) -> None:
        lobby.chat.append({"from": "", "memberId": "", "text": text, "ts": time.time()})

    async def drop_node(self, node_id: str) -> None:
        """Remove every member a departed peer was proxying."""
        for lobby in list(self.lobbies.values()):
            for member in [m for m in lobby.members.values() if m.node == node_id]:
                await self.leave(member.conn)


lobby_server = LobbyServer()

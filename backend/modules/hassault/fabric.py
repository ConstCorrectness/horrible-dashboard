"""Matches across the peer fabric: playing in a friend's match on their node.

The match server is authoritative and lives on **one** node — the host. A guest's
browser cannot reach it: the host may be behind NAT, on another network, or
simply not something a browser is allowed to open a socket to. So the guest's own
backend proxies for them:

```
guest browser --/ws--> guest backend --peer fabric--> host backend (MatchRoom)
```

The fabric already solves the hard parts (NAT traversal, relays, LAN discovery,
Ed25519 identity, trust), so this module only has to carry four kinds of message
and keep two mappings straight.

The trick that keeps the host simple: a remote player is represented by a
`PeerPlayerConn`, which has the one method the match server actually uses —
`send_json`. The host's `MatchRoom` never learns that some of its players are not
browsers.

Trust is the fabric's, not ours. `session.info.trusted` is what accepting a friend
grants, and it is the gate on every inbound message here — friendship means
reachability, so a stranger's node cannot join your match by knowing its id.

See docs/modules/hassault.mdx.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from backend.modules.hassault.match import CHANNEL, MAX_PLAYERS, match_server
from backend.modules.ws import WsConnection

if TYPE_CHECKING:
    from backend.modules.network.hub import PeerHub, PeerSession
    from backend.modules.network.models import PeerEnvelope

logger = logging.getLogger(__name__)

# Peer-wire message types. Declared here rather than in `network/protocol.py`,
# following the training-ads precedent: a module owns its own wire vocabulary and
# registers handlers for it, so adding a feature never edits the fabric core.
HASSAULT_INVITE = "hassault_invite"
HASSAULT_JOIN = "hassault_join"
HASSAULT_INPUT = "hassault_input"
HASSAULT_LEAVE = "hassault_leave"
HASSAULT_FRAME = "hassault_frame"

# The capability a node advertises when it can host or join matches. Used to
# offer only friends who could actually accept.
CAPABILITY = "hassault"

# Invites received from friends, newest first, keyed by room id so a repeated
# invite refreshes rather than stacking up.
_invites: dict[str, dict[str, Any]] = {}
INVITE_TTL = 300.0


# ---------------------------------------------------------------------------
# Host side: a peer's player, wearing a browser socket's clothes
# ---------------------------------------------------------------------------


class PeerPlayerConn:
    """Stands in for a browser socket for a player who is on another node.

    `MatchServer` keys membership by `id(conn)` and only ever calls `send_json`,
    so a remote player needs nothing more than this — and the match server needs
    no notion of "remote" at all.
    """

    def __init__(self, hub: PeerHub, node_id: str, client: str) -> None:
        self.hub = hub
        self.node_id = node_id
        # Which browser on the guest node this player belongs to. One machine can
        # have two tabs in the same match, and without this their snapshots would
        # both go to whichever arrived last.
        self.client = client

    async def send_json(self, data: dict[str, Any]) -> None:
        payload = dict(data)
        payload["client"] = self.client
        try:
            await self.hub.send_to(self.node_id, HASSAULT_FRAME, payload)
        except KeyError:
            # The peer went away; the host's own disconnect sweep will notice.
            pass


# Remote players hosted here, keyed by (node_id, client) so a leaving guest can
# be found, and so a dropped peer takes all its players with it.
_hosted: dict[tuple[str, str], PeerPlayerConn] = {}


def hosted_count() -> int:
    return len(_hosted)


async def handle_join(hub: PeerHub, session: PeerSession, env: PeerEnvelope) -> None:
    """A friend's node asking to put one of its browsers into a match here."""
    if not session.info.trusted:
        await _reply_error(hub, session.info.node_id, env, "not a trusted peer")
        return
    data = env.data or {}
    client = str(data.get("client") or "")
    room_id = str(data.get("room") or "")
    if not client:
        return
    name = str(data.get("name") or "guest")[:24]

    room = match_server.get(room_id)
    if room is None:
        await _reply_error(hub, session.info.node_id, env, f"no match {room_id!r}")
        return
    if len(room.players) >= MAX_PLAYERS:
        await _reply_error(hub, session.info.node_id, env, "that match is full")
        return

    key = (session.info.node_id, client)
    await _drop_hosted(key)
    conn = PeerPlayerConn(hub, session.info.node_id, client)
    _hosted[key] = conn

    player = room.add(name, conn)
    match_server.membership[id(conn)] = (room.id, player.id)
    match_server.ensure_running()

    payload = room.state_payload()
    payload["playerId"] = player.id
    payload["client"] = client
    payload["event"] = "welcome"
    await hub.send_to(session.info.node_id, HASSAULT_FRAME, payload)
    await match_server.broadcast_event(
        room,
        "joined",
        {"room": room.id, "player": player.snapshot(time.monotonic())},
        player.id,
    )


async def handle_input(hub: PeerHub, session: PeerSession, env: PeerEnvelope) -> None:
    """Input from a remote player. Validated exactly as a browser's would be —
    a peer is another user's machine, not a trusted extension of ours."""
    if not session.info.trusted:
        return
    data = env.data or {}
    conn = _hosted.get((session.info.node_id, str(data.get("client") or "")))
    if conn is None:
        return
    entry = match_server.player_for(conn)  # type: ignore[arg-type]
    if entry is None:
        return
    room, player = entry
    if data.get("respawn"):
        room.respawn(player)
    commands = data.get("commands")
    if not isinstance(commands, list):
        return
    # Reuses the browser channel's parser rather than a second one: two
    # validators for one wire format is how a gap appears in the stricter half.
    from backend.modules.hassault.channel import (
        MAX_COMMANDS_PER_MESSAGE,
        _parse_command,
    )

    for raw in commands[:MAX_COMMANDS_PER_MESSAGE]:
        command = _parse_command(raw)
        if command is not None:
            room.enqueue(player, command)
    rtt = data.get("rtt")
    if isinstance(rtt, (int, float)):
        player.rtt_ms = max(0.0, min(60_000.0, float(rtt)))


async def handle_leave(hub: PeerHub, session: PeerSession, env: PeerEnvelope) -> None:
    data = env.data or {}
    await _drop_hosted((session.info.node_id, str(data.get("client") or "")))


async def drop_peer(node_id: str) -> None:
    """Remove every player a departed peer was proxying.

    Without this a dropped connection leaves bodies standing in the match that
    nothing can ever remove — they are not attached to a browser socket, so the
    `/ws` teardown path will never see them.
    """
    for key in [k for k in _hosted if k[0] == node_id]:
        await _drop_hosted(key)


async def _drop_hosted(key: tuple[str, str]) -> None:
    conn = _hosted.pop(key, None)
    if conn is not None:
        await match_server.leave(conn)  # type: ignore[arg-type]


async def _reply_error(
    hub: PeerHub, node_id: str, env: PeerEnvelope, message: str
) -> None:
    client = str((env.data or {}).get("client") or "")
    try:
        await hub.send_to(
            node_id,
            HASSAULT_FRAME,
            {"event": "error", "client": client, "data": {"message": message}},
        )
    except KeyError:
        pass


# ---------------------------------------------------------------------------
# Guest side: our browsers playing somewhere else
# ---------------------------------------------------------------------------


class RemoteMatch:
    """One local browser's membership in a match hosted on another node."""

    def __init__(
        self, conn: WsConnection, host_node: str, room: str, client: str
    ) -> None:
        self.conn = conn
        self.host_node = host_node
        self.room = room
        self.client = client


# Local browsers playing remotely, keyed by connection id.
_remote: dict[int, RemoteMatch] = {}


def remote_for(conn: WsConnection) -> RemoteMatch | None:
    return _remote.get(id(conn))


def bind_remote(
    conn: WsConnection, host_node: str, room: str, client: str
) -> RemoteMatch:
    binding = RemoteMatch(conn, host_node, room, client)
    _remote[id(conn)] = binding
    return binding


def unbind_remote(conn: WsConnection) -> RemoteMatch | None:
    return _remote.pop(id(conn), None)


async def handle_frame(hub: PeerHub, session: PeerSession, env: PeerEnvelope) -> None:
    """A snapshot or event from the host, relayed to the browser it belongs to."""
    if not session.info.trusted:
        return
    data = dict(env.data or {})
    client = str(data.pop("client", ""))
    binding = next(
        (
            b
            for b in _remote.values()
            if b.client == client and b.host_node == session.info.node_id
        ),
        None,
    )
    if binding is None:
        return
    # A `welcome` arrives as a flat state payload with `event` inside it; a
    # snapshot arrives already shaped as a channel message. Normalising here
    # keeps the browser's handler identical for local and remote matches.
    if data.get("event") == "welcome":
        data.pop("event", None)
        binding.room = str(data.get("room") or binding.room)
        message = {
            "channel": CHANNEL,
            "event": "welcome",
            "data": {**data, "host": binding.host_node},
        }
    elif data.get("channel") == CHANNEL:
        message = data
    else:
        message = {
            "channel": CHANNEL,
            "event": str(data.get("event") or "error"),
            "data": data.get("data") or {},
        }
    try:
        await binding.conn.send_json(message)
    except Exception:
        pass


async def send_remote_input(
    binding: RemoteMatch, commands: list[Any], rtt: Any, respawn: bool = False
) -> None:
    """Forward input to the host. Respawn rides along rather than getting its own
    message type — it is a flag on a frame of input, and a separate message would
    be one more thing to keep ordered against it."""
    from backend.modules.network.hub import peer_hub

    try:
        await peer_hub.send_to(
            binding.host_node,
            HASSAULT_INPUT,
            {
                "client": binding.client,
                "commands": commands,
                "rtt": rtt,
                "respawn": respawn,
            },
        )
    except KeyError:
        pass


async def send_remote_join(binding: RemoteMatch, name: str) -> None:
    from backend.modules.network.hub import peer_hub

    await peer_hub.send_to(
        binding.host_node,
        HASSAULT_JOIN,
        {"client": binding.client, "room": binding.room, "name": name},
    )


async def send_remote_leave(binding: RemoteMatch) -> None:
    from backend.modules.network.hub import peer_hub

    try:
        await peer_hub.send_to(
            binding.host_node,
            HASSAULT_LEAVE,
            {"client": binding.client, "room": binding.room},
        )
    except KeyError:
        pass


# ---------------------------------------------------------------------------
# Invites
# ---------------------------------------------------------------------------


def live_invites() -> list[dict[str, Any]]:
    """Invites that have not aged out. Pruned on read rather than on a timer —
    nothing else needs waking up to keep a short list tidy."""
    now = time.time()
    for room, invite in list(_invites.items()):
        if now - float(invite.get("ts", 0)) > INVITE_TTL:
            _invites.pop(room, None)
    return sorted(_invites.values(), key=lambda i: i["ts"], reverse=True)


async def handle_invite(hub: PeerHub, session: PeerSession, env: PeerEnvelope) -> None:
    """A friend asking us to join their match."""
    if not session.info.trusted:
        return
    data = env.data or {}
    room = str(data.get("room") or "")
    if not room:
        return
    invite = {
        "room": room,
        "map": str(data.get("map") or ""),
        "host": session.info.node_id,
        # The peer's *claimed* name is only a label. Identity is the node id,
        # which the fabric authenticated; this is never used to make a decision.
        "hostName": str(data.get("hostName") or session.info.node_name or "a friend"),
        "ts": time.time(),
    }
    _invites[room] = invite
    from backend.modules.ws import broadcast_event

    await broadcast_event(CHANNEL, "invite", invite)


async def send_invite(node_id: str, room: str, map_name: str) -> None:
    from backend.modules.network import identity as node_identity
    from backend.modules.network.hub import peer_hub

    await peer_hub.send_to(
        node_id,
        HASSAULT_INVITE,
        {"room": room, "map": map_name, "hostName": node_identity.node_name()},
    )


def _on_peer_event(event: str, data: dict[str, Any]) -> None:
    """Sweep a departed peer's players out of every match hosted here.

    A remote player has no browser socket, so nothing else will ever notice they
    are gone — without this they stand in the match forever.
    """
    if event != "peer_update":
        return
    peer = data.get("peer") or {}
    if peer.get("status") == "disconnected":
        node_id = str(peer.get("node_id") or "")
        if node_id:
            asyncio.ensure_future(drop_peer(node_id))


def register(hub: PeerHub) -> None:
    """Register the fabric handlers and agent tools. Called from `network/setup.py`."""
    from backend.modules.hassault.agent_tools import register_hassault_tools

    register_hassault_tools()
    hub.register_handler(HASSAULT_INVITE, handle_invite)
    hub.register_handler(HASSAULT_JOIN, handle_join)
    hub.register_handler(HASSAULT_INPUT, handle_input)
    hub.register_handler(HASSAULT_LEAVE, handle_leave)
    hub.register_handler(HASSAULT_FRAME, handle_frame)
    hub.subscribe(_on_peer_event)

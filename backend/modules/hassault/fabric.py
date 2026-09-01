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
    from backend.modules.network.models import PeerCapability, PeerEnvelope

logger = logging.getLogger(__name__)

# Peer-wire message types. Declared here rather than in `network/protocol.py`,
# following the training-ads precedent: a module owns its own wire vocabulary and
# registers handlers for it, so adding a feature never edits the fabric core.
HASSAULT_INVITE = "hassault_invite"
HASSAULT_JOIN = "hassault_join"
HASSAULT_INPUT = "hassault_input"
HASSAULT_LEAVE = "hassault_leave"
HASSAULT_FRAME = "hassault_frame"
HASSAULT_BROWSE = "hassault_browse"

# The capability a node advertises when it can host or join matches. Used to
# offer only friends who could actually accept.
CAPABILITY = "hassault"

# Invites received from friends, newest first, keyed by room id so a repeated
# invite refreshes rather than stacking up.
_invites: dict[str, dict[str, Any]] = {}
INVITE_TTL = 300.0

#: Invites we could not deliver because the target's machine was not connected,
#: keyed by node id. `peer_hub.send_to` is a *direct* node-to-node send — there is
#: no relay in the path — so an offline friend does not mean a queued invite
#: somewhere, it means nothing was sent at all and nobody ever finds out. This is
#: the sender holding it instead, which is the only place that can: the receiver
#: cannot store what never arrived.
#:
#: Deliberately not persisted. It expires with `INVITE_TTL` and a room does not
#: outlive the process hosting it, so an invite that survived a restart would
#: point at a match that no longer exists.
_pending: dict[str, list[dict[str, Any]]] = {}


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


def hosted_rooms() -> dict[str, str]:
    """Which room each remote node's players are standing in.

    The only place that can be known: a remote player is a `PeerPlayerConn`, so
    the node id is the one thing tying a body in a room here to a person on the
    roster. First room wins if one node has two browsers in two matches — the
    server browser is showing "where is this friend", not a list of their tabs.
    """
    out: dict[str, str] = {}
    for (node_id, _client), conn in _hosted.items():
        entry = match_server.membership.get(id(conn))
        if entry is not None:
            out.setdefault(node_id, entry[0])
    return out


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

    # The peer's *claimed* username is only a label — the same rule `handle_invite`
    # states below. Identity here is the node id, which the fabric authenticated;
    # `name` is whatever the guest's backend put on the wire, so a trusted friend
    # could otherwise send a nameplate reading as somebody else's account. It is
    # used for display and nothing else: no lookup, no decision, no account.
    #
    # Verifying a remote player's account for real would mean forwarding their
    # game-server JWT for this host to `resolve_token`, which drags the central
    # server into every join and breaks matches on an offline LAN. Deliberately not
    # done — see the limitation noted in docs/modules/hassault.mdx.
    label = str(data.get("name") or "guest")[:24]
    # Tagged so a nameplate can never be mistaken for a local account's username.
    name = f"{label}@{session.info.node_id[:6]}"[:24]

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
    # Reuses the one validator rather than a second one: two implementations of
    # one wire format is how a gap appears in the stricter half. It lives in
    # `match` — next to the `Command` it produces — precisely because it has
    # three callers now (this, the browser channel, and the game server's rated
    # rooms) and none of them may be the lax one.
    from backend.modules.hassault.channel import MAX_COMMANDS_PER_MESSAGE
    from backend.modules.hassault.match import parse_command

    for raw in commands[:MAX_COMMANDS_PER_MESSAGE]:
        command = parse_command(raw)
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
# The server browser
# ---------------------------------------------------------------------------


async def handle_browse(hub: PeerHub, session: PeerSession, env: PeerEnvelope) -> None:
    """A friend asking what is running here.

    The reply is exactly `match_server.listing()` — the same rows `GET /matches`
    serves — because a friend's node is not a lesser client: they can already join
    any of these rooms, so there is nothing to withhold. Untrusted peers get
    silence rather than an error: the roster is not a thing to confirm the shape of
    to a stranger.
    """
    if not session.info.trusted:
        return
    from backend.modules.network import identity as node_identity

    await hub.send_to(
        session.info.node_id,
        HASSAULT_BROWSE,
        {"matches": match_server.listing(), "hostName": node_identity.node_name()},
        re=env.msg_id,
    )


async def browse_peers(timeout: float = 2.0) -> tuple[list[dict[str, Any]], int, int]:
    """Ask every hassault-capable friend what they are hosting.

    Fanned out concurrently and answered on a deadline, because a browser refresh
    cannot be as slow as the slowest peer — a node that has gone quiet without its
    TCP connection noticing would otherwise stall the whole list. A peer that
    misses the deadline contributes nothing and is simply absent, which is also
    what it looks like to the player: not there right now.

    Only *trusted* peers are asked. Discovery is not the point of this — the
    fabric already decides who we can reach, and a stranger's match is not
    joinable anyway (`handle_join` gates on the same flag).

    Returns the rows plus how many peers were asked and how many answered, so the
    pane can say the list is partial instead of quietly showing less than there is.
    """
    from backend.modules.network.hub import peer_hub

    targets = [
        peer
        for peer in peer_hub.list_peers()
        if peer.trusted and CAPABILITY in (peer.capabilities or [])
    ]
    if not targets:
        return [], 0, 0

    async def ask(node_id: str) -> tuple[str, dict[str, Any] | None]:
        try:
            env = await peer_hub.request(node_id, HASSAULT_BROWSE, {}, timeout=timeout)
        except Exception:
            # Gone, silent, or on a build with no browse handler. All three mean the
            # same thing to the player — not there right now — and none is worth a
            # 500 on a list that is mostly other people's rooms. Cancellation is
            # deliberately not caught: that is our request being abandoned.
            return node_id, None
        return node_id, dict(env.data or {})

    replies = await asyncio.gather(*(ask(p.node_id) for p in targets))

    out: list[dict[str, Any]] = []
    answered = 0
    by_node = {p.node_id: p for p in targets}
    for node_id, data in replies:
        if data is None:
            continue
        answered += 1
        rows = data.get("matches")
        if not isinstance(rows, list):
            continue
        peer = by_node.get(node_id)
        # The peer's claimed name is a label only, exactly as in `handle_invite`:
        # identity is the node id the fabric authenticated. The roster's own name
        # for them wins when it has one, since that is the name the player chose.
        claimed = str(data.get("hostName") or "")[:32]
        label = (peer.node_name if peer and peer.node_name else claimed) or "a friend"
        for row in rows:
            if not isinstance(row, dict):
                continue
            out.append({**row, "host": node_id, "hostName": label})
    return out, len(targets), answered


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


def _invite_display_name(
    session: PeerSession, claimed_username: str
) -> tuple[str, str]:
    """What to call whoever sent an invite: `(name, person_id)`.

    The old answer was `node_identity.node_name()` off the wire, which is a
    **machine** name — so an invite from a friend read "horribleComputer invited
    you", naming a box rather than a person. Two identity authorities meet here:
    the fabric knows a node and a person key, and the game server is the authority
    for the username. The invite is assembled on the fabric side, so the username
    had to be carried across deliberately or it is simply not in scope.

    Precedence, most trustworthy first:

    1. **The roster's** username for the person this node belongs to. We already
       stored it (`social_friends.handle`), we resolved it ourselves, and it costs
       no lookup. This is the only one that is more than a claim.
    2. The **sender's stamped** username. A claim — but a claim from an
       authenticated friend, and cheap. Resolving it against the game-server
       directory instead would mean a network round-trip per invite for an answer
       that is *also* only a claim (the directory vouches, it does not prove).
    3. The device name, then a generic. Last, because it names the wrong thing.

    Same shape as the precedence `browse_peers` already applies to `node_name`,
    and it leaves the existing rule untouched: the authenticated node id is the
    authority, everything here is a label.
    """
    from backend.modules.social import store

    person_id = ""
    try:
        person_id = store.person_for_node(session.info.node_id) or ""
        if person_id:
            row = store.get_friend_row(person_id) or {}
            if row.get("handle"):
                return f"@{row['handle']}", person_id
    except Exception:  # pragma: no cover - roster is best-effort here
        logger.debug(
            "could not resolve an inviting peer against the roster", exc_info=True
        )

    if claimed_username:
        return f"@{claimed_username}", person_id
    return (session.info.node_name or "a friend"), person_id


async def handle_invite(hub: PeerHub, session: PeerSession, env: PeerEnvelope) -> None:
    """A friend asking us to join their match."""
    if not session.info.trusted:
        return
    data = env.data or {}
    room = str(data.get("room") or "")
    if not room:
        return

    claimed = str(data.get("fromUsername") or "")[:20]
    name, person_id = _invite_display_name(session, claimed)
    device = str(data.get("fromDeviceName") or session.info.node_name or "")[:32]
    now = time.time()
    invite = {
        "room": room,
        "map": str(data.get("map") or ""),
        "host": session.info.node_id,
        # The peer's *claimed* name is only a label. Identity is the node id,
        # which the fabric authenticated; this is never used to make a decision.
        "hostName": name,
        # Which of their machines it came from. Secondary information, not the
        # headline — but worth keeping, because an invite fans out to every device
        # a person has online and knowing which one answered is useful.
        "hostDevice": device,
        "personId": person_id,
        "ts": now,
        "expiresAt": now + INVITE_TTL,
    }
    _invites[room] = invite
    from backend.modules.ws import broadcast_event

    await broadcast_event(CHANNEL, "invite", invite)
    await _notify_invite(invite)


async def _notify_invite(invite: dict[str, Any]) -> None:
    """Put the invite in front of the person, wherever they happen to be.

    The `hassault` broadcast above reaches the game pane — and **only** the game
    pane, because that is the one thing subscribed to that channel. A pane
    unmounts on a workspace switch and when its tab is inactive, so for most of
    the day an invite arrived, updated a dict, and was seen by nobody. That is the
    bug; this is the fix, and it is one call rather than a new subsystem because
    `notify()` already fans out to the shell toast, the notification feed and the
    OS notification, with mute rules enforced at the producer.

    `dedupe` ties all of those surfaces to one invite so accepting on any of them
    clears the rest instead of leaving three stale copies.
    """
    from backend.modules.notifications import service

    await service.notify(
        "invite",
        f"{invite['hostName']} invited you to a match",
        (
            f"{invite['map']} · HorribleAssault"
            + (f" · on {invite['hostDevice']}" if invite["hostDevice"] else "")
        ),
        person_id=invite["personId"] or None,
        kind="info",
        data={
            "dedupe": f"hassault-invite:{invite['host']}:{invite['room']}",
            "action": "hassault.joinInvite",
            "invite": invite,
            "expires_at": invite["expiresAt"],
        },
    )


def _invite_payload(room: str, map_name: str) -> dict[str, Any]:
    """What we put on the wire, stamped with **our username** rather than only our
    machine name.

    The sender is the one place that knows its own username without asking anyone:
    it is in the signed-in account already. Leaving the receiver to resolve it
    would mean a game-server directory lookup per invite, on the receiving path,
    for an answer that is no more authoritative than this stamp.
    """
    from backend.modules.games import server_auth
    from backend.modules.network import identity as node_identity

    return {
        "room": room,
        "map": map_name,
        "fromUsername": server_auth.signed_in_username() or "",
        "fromDeviceName": node_identity.node_name(),
        # Kept for nodes running an older build, which read this as the display
        # name. Dropping it would make an invite from a new node render as
        # "a friend" over there rather than as anything.
        "hostName": server_auth.signed_in_username() or node_identity.node_name(),
    }


async def send_invite(node_id: str, room: str, map_name: str) -> None:
    """Invite one machine. Raises `KeyError` when that node is not connected."""
    from backend.modules.network.hub import peer_hub

    await peer_hub.send_to(node_id, HASSAULT_INVITE, _invite_payload(room, map_name))


def queue_invite(node_id: str, room: str, map_name: str) -> None:
    """Hold an invite for a machine that is not connected, to send when it is.

    Not a durable queue and not meant to be one: it lives as long as the process
    and expires with `INVITE_TTL`, because the thing being invited to is a room in
    this process's memory. Delivering a two-hour-old invite to a match that ended
    an hour ago would be worse than not delivering it.
    """
    now = time.time()
    pending = [
        item for item in _pending.get(node_id, []) if now - item["ts"] <= INVITE_TTL
    ]
    # Keyed by room, so inviting the same person to the same match twice while
    # they are offline queues one invite, not two.
    pending = [item for item in pending if item["room"] != room]
    pending.append({"room": room, "map": map_name, "ts": now})
    _pending[node_id] = pending


async def flush_pending(node_id: str) -> None:
    """Send whatever we were holding for a node that just came online."""
    now = time.time()
    pending = _pending.pop(node_id, [])
    for item in pending:
        if now - item["ts"] > INVITE_TTL:
            continue
        try:
            await send_invite(node_id, item["room"], item["map"])
        except KeyError:
            # Gone again between the event and this send. Put it back rather than
            # dropping it — it has not expired yet.
            queue_invite(node_id, item["room"], item["map"])
        except Exception:
            logger.exception("could not flush a queued invite to %s", node_id)


def _on_peer_event(event: str, data: dict[str, Any]) -> None:
    """Sweep a departed peer's players out of every match hosted here.

    A remote player has no browser socket, so nothing else will ever notice they
    are gone — without this they stand in the match forever.
    """
    if event != "peer_update":
        return
    peer = data.get("peer") or {}
    node_id = str(peer.get("node_id") or "")
    if not node_id:
        return
    if peer.get("status") == "disconnected":
        asyncio.ensure_future(drop_peer(node_id))
    elif _pending.get(node_id):
        # They were offline when somebody invited them and they are here now.
        asyncio.ensure_future(flush_pending(node_id))


def register(hub: PeerHub) -> None:
    """Register the fabric handlers and agent tools. Called from `network/setup.py`."""
    from backend.modules.hassault.agent_tools import register_hassault_tools

    register_hassault_tools()
    hub.register_handler(HASSAULT_INVITE, handle_invite)
    hub.register_handler(HASSAULT_JOIN, handle_join)
    hub.register_handler(HASSAULT_INPUT, handle_input)
    hub.register_handler(HASSAULT_LEAVE, handle_leave)
    hub.register_handler(HASSAULT_FRAME, handle_frame)
    # Detached to close a latent deadlock by construction: `browse_peers` fans out
    # with `hub.request`, and those replies arrive on this very pump. Today it is
    # only ever called from a route, so the deadlock is unreachable -- but nothing
    # stopped a future caller from reaching it from inside a handler.
    hub.register_handler(HASSAULT_BROWSE, handle_browse, mode="detach")
    # Upgrade the statically-registered `hassault` capability to one that reports
    # how many matches are actually open.
    from backend.modules.network import capabilities

    capabilities.register(CAPABILITY, _capability)
    hub.subscribe(_on_peer_event)


def _capability() -> "PeerCapability":
    """Advertise how many matches are actually open here, not just that this node
    can play. It is what lets `browse_peers` skip a friend hosting nothing instead
    of fanning out to everyone and waiting on the deadline."""
    # Imported here, like `node_identity` in `handle_browse`: this module keeps its
    # network imports local so the two packages can be loaded in either order.
    from backend.modules.network.models import PeerCapability

    rooms = match_server.listing()
    return PeerCapability(
        id=CAPABILITY,
        attrs={
            "openMatches": sum(
                1 for room in rooms if room.get("players", 0) < MAX_PLAYERS
            ),
            "matches": len(rooms),
            "maxPlayers": MAX_PLAYERS,
        },
    )

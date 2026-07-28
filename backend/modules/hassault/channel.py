"""The `hassault` `/ws` channel: joins, input, and snapshots.

Rides the shell's existing shared socket rather than opening a second one. That
is a deliberate revision of the "binary WebSocket first" note in the roadmap, and
the reason is arithmetic: a snapshot of eight players is about 500 bytes of JSON,
so 20 Hz costs ~10 KB/s — nothing on a LAN, which is the only place this runs.
Against that, JSON on the shared socket is readable in the observability panel,
needs no second connection to manage or reconnect, and can be debugged by reading
it. Binary framing is worth reaching for when player counts or tick rates make
the bandwidth real; today it would buy nothing and cost legibility.

Input is **batched**: the client sends one message carrying every command since
its last send, rather than one message per rendered frame. At 60 fps that is the
difference between 60 and ~30 messages a second for the same information.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from backend.modules.hassault import bots, fabric
from backend.modules.hassault.cgz import CgzError
from backend.modules.hassault.match import (
    CHANNEL,
    MAX_PLAYERS,
    Command,
    match_server,
)
from backend.modules.hassault.weapons import WEAPONS
from backend.modules.ws import WsConnection

logger = logging.getLogger(__name__)

# Most commands one message may carry. A client that has been stalled for a while
# legitimately has a backlog; one claiming hundreds is either broken or trying
# something, and either way the match server's time budget would throttle it.
MAX_COMMANDS_PER_MESSAGE = 64


def _evt(event: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"channel": CHANNEL, "event": event, "data": data}


def _num(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    # NaN and infinities survive JSON and poison every downstream comparison, so
    # they are rejected here rather than at the first surprising position.
    return out if out == out and abs(out) != float("inf") else default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _parse_command(raw: Any) -> Command | None:
    if not isinstance(raw, dict):
        return None
    seq = raw.get("seq")
    if not isinstance(seq, int) or seq <= 0:
        return None
    weapon = raw.get("weapon")
    view_t = raw.get("viewT")
    return Command(
        seq=seq,
        # Clamped rather than trusted: the analogue axes are the obvious place to
        # ask for a value of 50 and move fifty times as fast.
        forward=_clamp(_num(raw.get("forward")), -1.0, 1.0),
        strafe=_clamp(_num(raw.get("strafe")), -1.0, 1.0),
        jump=bool(raw.get("jump")),
        yaw=_num(raw.get("yaw")),
        pitch=_clamp(_num(raw.get("pitch")), -1.5708, 1.5708),
        dt=_clamp(_num(raw.get("dt")), 0.0, 0.25),
        fire=bool(raw.get("fire")),
        reload=bool(raw.get("reload")),
        # `-1` means "no change", so an absent or nonsensical slot leaves the
        # weapon alone rather than silently arming the knife.
        weapon=(
            int(_clamp(_num(weapon, -1.0), -1.0, float(len(WEAPONS) - 1)))
            if isinstance(weapon, (int, float))
            else -1
        ),
        # Left as `None` when absent: the shot is then judged live, which is the
        # right answer for a client that did not say what it was looking at.
        # Range-checking is `PositionHistory.clamp`'s job — it is the only place
        # that knows the current time, and it is the security boundary.
        view_t=_num(view_t) if isinstance(view_t, (int, float)) else None,
    )


async def handle(conn: WsConnection, msg: dict[str, Any]) -> None:
    event = msg.get("event")
    data = msg.get("data") or {}
    if not isinstance(data, dict):
        return

    if event == "join":
        map_name = str(data.get("map") or "")
        name = str(data.get("name") or "player")
        room_id = str(data.get("room") or "") or None
        host = str(data.get("host") or "")
        # Leaving whatever we were in first is what makes "join" idempotent from
        # the browser's point of view, local or remote.
        await _leave_any(conn)

        if host:
            # A match on a friend's node: our backend proxies for this browser.
            # The welcome arrives later, over the fabric.
            if not room_id:
                await conn.send_json(
                    _evt("error", {"message": "a remote match needs a room id"})
                )
                return
            binding = fabric.bind_remote(conn, host, room_id, uuid.uuid4().hex[:12])
            try:
                await fabric.send_remote_join(binding, name)
            except KeyError:
                fabric.unbind_remote(conn)
                await conn.send_json(
                    _evt("error", {"message": "that friend's machine is not connected"})
                )
            return

        try:
            room, player = await match_server.join(conn, map_name, name, room_id)
        except (LookupError, ValueError, CgzError) as exc:
            await conn.send_json(_evt("error", {"message": str(exc)}))
            return
        payload = room.state_payload()
        payload["playerId"] = player.id
        await conn.send_json(_evt("welcome", payload))

    elif event == "leave":
        await _leave_any(conn)
        await conn.send_json(_evt("left_ok", {}))

    elif event == "input":
        binding = fabric.remote_for(conn)
        if binding is not None:
            # Forwarded verbatim: the host validates, because the host is the one
            # being lied to. Re-validating here would be a second implementation
            # of the same rules with no authority behind it.
            commands = data.get("commands")
            if isinstance(commands, list):
                await fabric.send_remote_input(
                    binding, commands[:MAX_COMMANDS_PER_MESSAGE], data.get("rtt")
                )
            return
        entry = match_server.player_for(conn)
        if entry is None:
            return
        room, player = entry
        commands = data.get("commands")
        if not isinstance(commands, list):
            return
        for raw in commands[:MAX_COMMANDS_PER_MESSAGE]:
            command = _parse_command(raw)
            if command is not None:
                room.enqueue(player, command)
        rtt = data.get("rtt")
        if isinstance(rtt, (int, float)):
            player.rtt_ms = _clamp(_num(rtt), 0.0, 60_000.0)

    elif event == "respawn":
        binding = fabric.remote_for(conn)
        if binding is not None:
            await fabric.send_remote_input(binding, [], None, respawn=True)
            return
        entry = match_server.player_for(conn)
        if entry is not None:
            room, player = entry
            room.respawn(player)

    elif event in ("add_bot", "remove_bot"):
        # Host-only, deliberately. A guest's socket is bound to a `RemoteMatch`,
        # not to a local room, and there is no fabric message for "change the
        # roster of someone else's match" — adding one would mean a friend could
        # reshape a match you are hosting from a pane you cannot see.
        if fabric.remote_for(conn) is not None:
            await conn.send_json(
                _evt("error", {"message": "only the host can add or remove bots"})
            )
            return
        entry = match_server.player_for(conn)
        if entry is None:
            return
        room, _ = entry
        if event == "add_bot":
            count = int(_clamp(_num(data.get("count"), 1.0), 1.0, float(MAX_PLAYERS)))
            skill = str(data.get("skill") or bots.DEFAULT_SKILL)
            added = bots.add_bots(room, count, skill)
            await match_server.broadcast_event(
                room, "roster", {"room": room.id, "added": [b.name for b in added]}
            )
        else:
            who = str(data.get("id") or "")
            if who and who in room.players and room.players[who].is_bot:
                room.remove(who)
                removed = 1
            else:
                removed = room.remove_bots(
                    int(_clamp(_num(data.get("count"), 1.0), 1.0, float(MAX_PLAYERS)))
                )
            await match_server.broadcast_event(
                room, "roster", {"room": room.id, "removed": removed}
            )

    elif event == "invite":
        # Invite a friend to a match hosted here. `who` is a person — a name or
        # friend code — because nobody remembers a 16-character node id.
        who = str(data.get("who") or "")
        room_id = str(data.get("room") or "")
        result = await invite_friend(who, room_id)
        await conn.send_json(
            _evt("invite_sent" if result.get("ok") else "error", result)
        )

    elif event == "invites":
        await conn.send_json(_evt("invites", {"invites": fabric.live_invites()}))

    elif event == "ping":
        # Echo the client's own clock reading back untouched: it measures the
        # round trip against it, and reinterpreting it here would only add error.
        await conn.send_json(
            _evt("pong", {"t": data.get("t"), "serverT": round(time.time() * 1000)})
        )

    elif event == "list":
        await conn.send_json(_evt("matches", {"matches": match_server.listing()}))


async def _leave_any(conn: WsConnection) -> None:
    """Leave whichever kind of match this socket is in, local or remote."""
    binding = fabric.unbind_remote(conn)
    if binding is not None:
        await fabric.send_remote_leave(binding)
    await match_server.leave(conn)


async def invite_friend(who: str, room_id: str) -> dict[str, Any]:
    """Invite a person to a match hosted here, resolving them to a live machine.

    Reaches into the social roster deliberately: "invite Rob" has to become "send
    to this node id", and the roster is the only thing that knows the mapping.
    The dependency is one-way and backend-side, so the frontend module boundary
    is untouched — the pane only ever talks to `/api/hassault`.
    """
    from backend.modules.social import roster, store
    from backend.modules.social.agent_tools import _resolve

    # Idempotent, and it makes the dependency safe rather than ordered: inviting
    # is reachable from an agent tool, which can run before the peer fabric has
    # started and created these tables.
    store.init_social_db()
    room = match_server.get(room_id)
    if room is None:
        return {"error": f"no match {room_id!r}"}
    row = _resolve(who)
    if row is None:
        return {"error": f"no friend matching {who!r}"}
    if row["status"] != "accepted":
        return {"error": f"{row['display_name']} is not an accepted friend yet"}
    nodes = roster.reachable_nodes(row["person_id"])
    if not nodes:
        return {"error": f"{row['display_name']} has no machine online right now"}

    sent: list[str] = []
    # Every device they have online, because you invite a person and they choose
    # which machine to answer on.
    for node_id in nodes:
        try:
            await fabric.send_invite(node_id, room.id, room.map_name)
            sent.append(node_id)
        except KeyError:
            continue
    if not sent:
        return {"error": f"could not reach {row['display_name']}"}
    return {"ok": True, "invited": row["display_name"], "room": room.id, "nodes": sent}


async def on_disconnect(conn: WsConnection) -> None:
    """Drop this socket's player. Called from the `/ws` teardown path, because a
    player who closed the tab must leave the match, not stand there forever."""
    try:
        await _leave_any(conn)
    except Exception:
        logger.exception("hassault leave on disconnect failed")

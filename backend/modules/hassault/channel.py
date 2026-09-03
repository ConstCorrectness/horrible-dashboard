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

from backend.modules.hassault import bots, fabric, ranked
from backend.modules.hassault.cgz import CgzError
from backend.modules.hassault.match import (
    _clamp,
    _num,
    parse_command,
    CHANNEL,
    MAX_PLAYERS,
    match_server,
)
from backend.modules.ws import WsConnection

logger = logging.getLogger(__name__)

# Most commands one message may carry. A client that has been stalled for a while
# legitimately has a backlog; one claiming hundreds is either broken or trying
# something, and either way the match server's time budget would throttle it.
MAX_COMMANDS_PER_MESSAGE = 64


def _evt(event: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"channel": CHANNEL, "event": event, "data": data}


async def handle(conn: WsConnection, msg: dict[str, Any]) -> None:
    event = msg.get("event")
    data = msg.get("data") or {}
    if not isinstance(data, dict):
        return

    if event == "join":
        map_name = str(data.get("map") or "")
        room_id = str(data.get("room") or "") or None
        mode = str(data.get("mode") or "") or None
        host = str(data.get("host") or "")

        # Identity is the account's username, never `data["name"]` — a name the
        # client picks is a label anyone can type, and this is the one place that
        # decides who a player *is*. The check sits above both branches on purpose:
        # the remote branch below returns before `match_server.join` is ever
        # reached, so a gate placed there would leave cross-node play wide open.
        # It also sits above `_leave_any`, so a refused join can't evict someone
        # from the match they are already in.
        name = _signed_in_username()
        if name is None:
            await conn.send_json(
                _evt(
                    "error",
                    {
                        "message": "sign in and choose a username to play",
                        "code": "not_signed_in",
                    },
                )
            )
            return

        # Leaving whatever we were in first is what makes "join" idempotent from
        # the browser's point of view, local or remote.
        await _leave_any(conn)

        if bool(data.get("ranked")):
            # **A rated match, simulated by the game server.** The room is not on
            # this node at all — see `ranked.py` for why the node proxies rather
            # than handing the client its token. The welcome arrives later, from
            # the server, over the same relay.
            #
            # Checked before `host`: a join cannot be both, and "ranked on a
            # friend's node" is not a thing — a friend's node has no more
            # authority over a result than this one does.
            await ranked.join(conn, map_name)
            return

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
            room, player = await match_server.join(conn, map_name, name, room_id, mode)
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
        if ranked.session_for(conn) is not None:
            # Forwarded untouched, like the fabric's: the *server* validates,
            # because the server is the one being lied to. A second check here
            # would be a copy of the rules with no authority behind it.
            await ranked.relay_input(conn, data if isinstance(data, dict) else {})
            return
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
            command = parse_command(raw)
            if command is not None:
                room.enqueue(player, command)
        rtt = data.get("rtt")
        if isinstance(rtt, (int, float)):
            player.rtt_ms = _clamp(_num(rtt), 0.0, 60_000.0)

    elif event == "respawn":
        if ranked.session_for(conn) is not None:
            await ranked.relay_respawn(conn)
            return
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
        if fabric.remote_for(conn) is not None or ranked.session_for(conn) is not None:
            # A ranked room has no host to ask, which is the point of it: a match
            # whose roster a player could reshape is not one their result should
            # count for.
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

    elif event == "console_exec":
        from backend.modules.hassault.console import (
            ConsoleExecRequest,
            console_registry,
        )

        cmd = str(data.get("command") or "")
        req_id = data.get("reqId")
        entry = match_server.player_for(conn)
        room_id = entry[0].id if entry else str(data.get("room") or "")
        player_id = entry[1].id if entry else None

        req = ConsoleExecRequest(
            command=cmd,
            room_id=room_id or None,
            player_id=player_id,
            client_context=data.get("context") or {},
        )
        res = await console_registry.execute(req)
        await conn.send_json(
            _evt(
                "console_res",
                {
                    "reqId": req_id,
                    "ok": res.ok,
                    "command": res.command,
                    "output": res.output,
                    "error": res.error,
                    "affectedCvars": res.affected_cvars,
                    "resultData": res.result_data,
                },
            )
        )


def _signed_in_username() -> str | None:
    """This node's player identity, or None when signed out / not yet enlisted.

    Reaches into the games module deliberately, and for the same reason
    `invite_friend` below reaches into social: the account lives there — the node
    holds one game-server JWT and one username, shared by the ladder and by this
    game — and duplicating that custody here would mean two accounts to sign into.
    The dependency is one-way and backend-side, so the frontend module boundary is
    untouched: the pane only ever talks to `/api/hassault`.

    Imported inside the function, not at module scope, so a games-module import
    error can never stop this channel from loading.
    """
    from backend.modules.games import server_auth

    return server_auth.signed_in_username()


async def _leave_any(conn: WsConnection) -> None:
    """Leave whichever kind of match this socket is in, local or remote.

    **This is where a match becomes history.** Leaving is the only moment the
    per-player counters are complete and still reachable, so the result is
    written here — from the simulation's own numbers, not from the client's word
    for them, and not (as it was) invented by a watchdog with `random.randint`
    once the process exited.
    """
    if ranked.session_for(conn) is not None:
        # The server writes this one down and tells us; `ranked` records it under
        # `authority="server"`. Nothing local to file.
        await ranked.leave(conn)
        return
    binding = fabric.unbind_remote(conn)
    if binding is not None:
        await fabric.send_remote_leave(binding)
    result = await match_server.leave(conn)
    if result is not None:
        _record_result(result)


def _record_result(result: dict[str, Any]) -> None:
    """File a finished match under the signed-in account.

    A node is one account, so there is nothing to disambiguate — and if it is
    signed into none, there is nobody to file it under. Swallowing the failure is
    deliberate: a disconnect handler that raised on a database write would leave
    the room holding a player who is not there.
    """
    from backend.modules.games import server_auth
    from backend.modules.hassault import results

    # Not every leave is a match. Opening the pane, deploying and pressing Menu
    # used to write a row — which then showed as a VICTORY, because a lone player
    # outscores `default=-1`. The gate is `results.is_recordable` rather than a
    # condition spelled out here, so the rule that decides whether a row exists
    # is the same one `result_for` used to decide `won`.
    if not results.is_recordable(result):
        logger.debug("hassault: nothing to record for this session")
        return

    account = server_auth.signed_in_account()
    account_id = str((account or {}).get("account_id") or "local_player")
    try:
        results.record(account_id, result)
    except Exception:
        logger.exception("hassault: could not record the match result")


async def invite_friend(who: str, room_id: str) -> dict[str, Any]:
    """Invite a person to a match hosted here, resolving them to a live machine.

    Reaches into the social roster deliberately: "invite Rob" has to become "send
    to this node id", and the roster is the only thing that knows the mapping.
    The dependency is one-way and backend-side, so the frontend module boundary
    is untouched — the pane only ever talks to `/api/hassault`.
    """
    from backend.modules.social import roster, store
    from backend.modules.social.agent_tools import resolve_row

    # Idempotent, and it makes the dependency safe rather than ordered: inviting
    # is reachable from an agent tool, which can run before the peer fabric has
    # started and created these tables.
    store.init_social_db()
    room = match_server.get(room_id)
    if room is None:
        return {"error": f"no match {room_id!r}"}
    row = await resolve_row(who)
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

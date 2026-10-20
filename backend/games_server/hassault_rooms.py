"""Server-hosted HorribleAssault matches: the referee that makes a stat mean something.

### Why this exists

A hassault match is simulated by a `MatchRoom`, and until now that room always
lived inside **a player's own node** — so when you hosted a match, your machine
decided how many kills you had. Storing that in a central database would not fix
it; it would file a self-reported number somewhere more official-looking, which is
worse, because a central leaderboard reads as authoritative. **Storage is not the
trust boundary; simulation is.**

So the same simulation runs *here*, on the machine nobody playing controls, and
the result is written by the referee — exactly the shape every other game on this
server already has (`hub.py` calls `store.record_result` on `game_over`; no client
ever posts a result).

### What that buys, and what it costs

Matches hosted here are **rated**: their result carries `authority: server` back
to the node, which is the only kind a ladder may ever count. Matches hosted on a
node stay casual and remain a personal record. That split is the whole design, and
it is why `results.record` on the node side takes an `authority` it never invents
for itself.

The cost is that a rated match can only use a **bundled map**. `assets.load_map`
resolves an AssaultCube install's maps too, and this server has no such install —
which is the right answer rather than a limitation to work around: a map that
exists only on one player's disk cannot be adjudicated by anybody else. The
refusal is explicit here rather than left to a `LookupError`, so the reason is
legible.

### Reusing the node's simulation, deliberately

This module owns no physics. It holds a `MatchServer` — the very class the node
runs — and feeds it connections. That is the same trick `hassault/fabric.py` uses
to let a remote peer's player look like a browser: `MatchRoom` never learns what
kind of thing is on the other end of `send_json`. A second implementation of the
simulation living on the server is the one thing that would guarantee the server
and the clients disagree.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

from backend.modules.hassault import mapsource, modes
from backend.modules.hassault.match import (
    MAX_NAME_LEN,
    MAX_PLAYERS,
    MatchServer,
    parse_command,
)

#: Matches the browser channel's cap: an input message is one frame's worth of
#: commands plus whatever a lossy link made it re-send, never an unbounded batch.
MAX_COMMANDS_PER_MESSAGE = 64

logger = logging.getLogger(__name__)

#: Where duels are fought, in order of preference: small, symmetric, bundled.
DUEL_MAPS = ("hd_pit", "hd_atrium", "hd_aim")
#: The duel queue's rating window: ±this to start, +step every step_s waited.
DUEL_WINDOW = 100.0
DUEL_WINDOW_STEP = 100.0
DUEL_WINDOW_STEP_S = 10.0
#: How often a waiting queue is re-tried and told where it stands.
DUEL_SWEEP_S = 2.0

#: The id these matches are logged and rated under, in `results` and `ratings`.
GAME_ID = "hassault"


#: Practice aids the room's host may switch on for everyone in it, and their
#: defaults. Display only — none of them changes what the simulation decides —
#: but they are the room's call, not each player's: a hitbox overlay one side
#: sees and the other doesn't is an unequal match. Never on in a ranked room
#: is the client's to enforce for the grenade arc, the one that is an aim aid.
ROOM_SETTINGS: dict[str, bool] = {
    "show_hitboxes": False,
    "grenade_trajectory": False,
}


def max_rooms() -> int:
    """How many rooms this server will run at once (`HASSAULT_MAX_ROOMS`).

    The public web client connects as a guest with no account, so nothing else
    bounds how many rooms strangers can open — and every room with a human in it
    simulates at 20 Hz on a single shared CPU. Joining a room that already exists
    is never refused by this; only opening one more is.
    """
    try:
        return max(1, int(os.environ.get("HASSAULT_MAX_ROOMS", "12")))
    except ValueError:
        return 12


class SeatConn:
    """One player's socket, in the shape `MatchRoom` expects.

    The simulation only ever calls `send_json`, so this is the entire contract —
    plus the account id, which is *ours* rather than the simulation's: the match
    knows a display name, and the ladder needs an account. Keeping the mapping
    here is what stops a player naming themselves into somebody else's row.
    """

    def __init__(
        self,
        websocket: Any,
        account_id: str,
        display_name: str,
        is_guest: bool = False,
    ) -> None:
        self.websocket = websocket
        self.account_id = account_id
        self.display_name = display_name[:MAX_NAME_LEN] or "player"
        self.is_guest = is_guest
        #: Set once the socket is gone, so a broadcast mid-teardown is a no-op
        #: rather than an exception inside the tick loop.
        self.closed = False

    async def send_json(self, payload: dict[str, Any]) -> None:
        if self.closed:
            return
        try:
            await self.websocket.send_text(json.dumps(payload))
        except Exception:
            # A dropped client must never take the tick with it: every other
            # player in that room is mid-frame.
            self.closed = True


class HassaultReferee:
    """The rooms this server is running, and the results it stands behind."""

    def __init__(self) -> None:
        # No replays here: a replay is a file on this machine that no player can
        # reach, and on a server that never restarts its rooms it is a file per
        # room forever.
        self.server = MatchServer(record_replays=False)
        # An AFK removal is a leave like any other here: recorded by the
        # referee, and the result sent so the player's node files it.
        self.server.kick_handler = self._kick
        #: Room id → the settings its host has changed from `ROOM_SETTINGS`.
        self._settings: dict[str, dict[str, bool]] = {}
        #: Seats waiting for a duel, oldest first: `(conn, since, rating)`.
        self._duel_queue: list[tuple[SeatConn, float, float]] = []
        self._duel_sweeper: asyncio.Task[None] | None = None

    # -- maps ---------------------------------------------------------------

    @staticmethod
    def playable(map_name: str) -> bool:
        """Whether a rated match may be played on this map.

        Bundled only. An install's maps are on one player's disk, and a result
        adjudicated against geometry nobody else has is not a result anybody else
        can trust — quite apart from the server not having the file.
        """
        return map_name in mapsource.bundled_names()

    def maps(self) -> list[str]:
        return list(mapsource.bundled_names())

    def active_rooms(self) -> list[dict[str, Any]]:
        """Active rooms currently ticking on this game server."""
        result = []
        for room in self.server.rooms.values():
            result.append(
                {
                    "id": room.id,
                    "map": room.map_name,
                    "playerCount": len(room.players),
                    "maxPlayers": MAX_PLAYERS,
                    "mode": room.mode.id if room.mode else "dm",
                    "hasGuests": any(
                        getattr(p.conn, "is_guest", False)
                        for p in room.players.values()
                    ),
                    "rated": False,
                }
            )
        return result

    # -- play ---------------------------------------------------------------

    async def join(
        self,
        conn: SeatConn,
        map_name: str,
        room_id: str | None = None,
        new: bool = False,
        mode: str | None = None,
    ) -> dict[str, Any]:
        """Seat a player, opening a room if there is none on that map.

        `mode` picks the game for a room this join opens or fills (deathmatch by
        default): a join without a room id lands in a room on that map *in that
        mode*, and opens one if there is none. An unknown mode, or one the map
        cannot host (defuse on a map with no bomb sites), is refused. A join
        that names a live room takes whatever that room is running.

        Deathmatch default. If joining by explicit room_id, uses the room's existing
        map; otherwise verifies that map_name is a valid bundled map.

        The welcome is the room's whole `state_payload` — items, what has been
        taken, the mode, the scores — exactly what a node sends its own browser.
        It used to be five fields, so a client joining here drew no pickups and
        no mode until something happened to mention them.

        `new` opens a room of its own instead of filling one that has space —
        the desktop's "open an online room", which is a room to share, not a
        seat in whatever happens to be running. Ignored when `room_id` names a
        live room: that is a join, not a creation.
        """
        existing = self.server.get(room_id) if room_id else None
        if room_id and existing is None:
            # A share link outlives its room — rooms close a minute after the
            # last human leaves. The only joins that name a room here are those
            # links (a ranked join names a map), and what the person following
            # one wanted was to play on that map, not to be told the room is gone.
            room_id = None
        target_map = existing.map_name if existing else (map_name or "hd_assault")
        if not self.playable(target_map):
            raise ValueError(f"{target_map!r} is not a bundled map")
        if mode and not modes.is_mode(mode):
            raise ValueError(f"unknown game mode {mode!r}")
        if existing is not None:
            mode = existing.mode.id
        opening = existing is None and (new or not self._has_space(target_map, mode))
        if opening and len(self.server.rooms) >= max_rooms():
            raise ValueError("this server is full; try a room that is already running")
        if opening:
            # Opened here rather than inside `server.join`, so a map that cannot
            # host the mode is a refusal with a reason, before anyone is seated.
            room_id = self.server.create(target_map, mode=mode).id
        loadouts, look = self.account_look(conn)
        room, player = await self.server.join(
            conn,
            target_map,
            conn.display_name,
            room_id,
            mode,
            loadouts=loadouts,
            cosmetics=look,
        )
        has_guests = conn.is_guest or any(
            getattr(p.conn, "is_guest", False) for p in room.players.values()
        )
        welcome = room.state_payload()
        welcome["playerId"] = player.id
        welcome["rated"] = not has_guests
        welcome["roomSettings"] = self.settings_payload(room)
        return welcome

    # -- room settings ------------------------------------------------------

    @staticmethod
    def host_of(room: Any) -> str:
        """The room's host: its longest-seated human. Nobody is elected and
        nothing is stored — when the host leaves, the next-oldest seat simply
        is the host, so a room is never left with nobody able to change it."""
        humans = room.humans
        return humans[0].id if humans else ""

    def settings_for(self, room_id: str) -> dict[str, bool]:
        # Forget rooms that have closed, so the table never outgrows the server.
        for stale in [r for r in self._settings if r not in self.server.rooms]:
            del self._settings[stale]
        return {**ROOM_SETTINGS, **self._settings.get(room_id, {})}

    def settings_payload(self, room: Any) -> dict[str, Any]:
        return {"settings": self.settings_for(room.id), "hostId": self.host_of(room)}

    async def set_room_setting(
        self, conn: SeatConn, data: dict[str, Any]
    ) -> str | None:
        """The host changing a room setting; why it was refused, or None.

        On success every player in the room gets `room_settings`, the host
        included — the echo is what the client shows, as with chat.
        """
        entry = self.server.player_for(conn)
        if entry is None:
            return "join a room first"
        room, player = entry
        if player.id != self.host_of(room):
            return "only the room's host can change that"
        key = data.get("key")
        value = data.get("value")
        if key not in ROOM_SETTINGS or not isinstance(value, bool):
            return f"unknown room setting {key!r}"
        self._settings.setdefault(room.id, {})[str(key)] = value
        await self.server.broadcast_event(
            room, "room_settings", self.settings_payload(room)
        )
        return None

    def _has_space(self, map_name: str, mode: str | None = None) -> bool:
        """Whether a join on `map_name` in `mode` would land in a room that
        already exists — the same test `MatchServer.find_or_create` makes."""
        want = mode or modes.DEFAULT_MODE
        return any(
            room.map_name == map_name
            and room.mode.id == want
            and len(room.players) < MAX_PLAYERS
            for room in self.server.rooms.values()
        )

    @staticmethod
    def account_look(
        conn: SeatConn,
    ) -> tuple[dict[str, dict[str, int]] | None, dict[str, Any]]:
        """A seat's loadouts and cosmetics, from this server's own tables.

        A guest has no account, so no stored loadout and the default look. A
        failure to read is the default too: what a player looks like must never
        be why they could not join.
        """
        from backend.games_server import hassault_account
        from backend.modules.hassault import cosmetics

        if conn.is_guest:
            return None, cosmetics.default_bundle()
        try:
            return (
                hassault_account.loadouts(conn.account_id),
                hassault_account.cosmetics(conn.account_id),
            )
        except Exception:  # noqa: BLE001 - cosmetic; the join goes ahead
            logger.exception("hassault: could not read %s's look", conn.account_id)
            return None, cosmetics.default_bundle()

    async def refresh_account(self, account_id: str) -> None:
        """Re-read an account's look and loadouts for every seat it holds.

        Called after an equip or a loadout or agent setting changes. The look
        is broadcast now; the loadout, like any loadout, applies at the next
        spawn.
        """
        for room in list(self.server.rooms.values()):
            for player in list(room.players.values()):
                conn = player.conn
                if not isinstance(conn, SeatConn) or conn.account_id != account_id:
                    continue
                loadouts, look = self.account_look(conn)
                if loadouts is not None:
                    player.loadouts = loadouts
                await self.server.update_cosmetics(room, player, look)

    def apply_loadout(self, conn: SeatConn, data: dict[str, Any]) -> dict[str, int] | None:
        """A seated player's loadout pick, validated by the room like any other.

        The stored setting is read at join; this is the same choice made
        mid-match, before it has reached the account.
        """
        entry = self.server.player_for(conn)
        if entry is None:
            return None
        room, player = entry
        return room.set_loadout(player, data)

    def apply_input(self, conn: SeatConn, data: dict[str, Any]) -> None:
        """One input message from a seated player.

        Runs through **`match.parse_command`** — the same validator a browser's
        input goes through on a node, and the same one the peer fabric uses. A
        second, laxer implementation on the path that happens to be rated is
        exactly where a gap would appear, and it would appear in the one place
        nobody looks.
        """
        entry = self.server.player_for(conn)
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
            player.rtt_ms = max(0.0, min(60_000.0, float(rtt)))

    async def chat(self, conn: SeatConn, data: dict[str, Any]) -> str | None:
        """One chat line from a seated player; why it was refused, or None.

        Through **`chat.post`**, the node's own path, handed this server's rooms:
        a ranked match is the last place the cleaning and rate limit should be a
        second, drifting copy.
        """
        from backend.modules.hassault import chat

        entry = self.server.player_for(conn)
        if entry is None:
            return None
        room, player = entry
        return await chat.post(
            room,
            player,
            data.get("text"),
            bool(data.get("team", False)),
            server=self.server,
        )

    # -- duels --------------------------------------------------------------

    def duel_window(self, waited: float) -> float:
        """How far apart two ratings may be to pair, after `waited` seconds:
        close matches first, wider as the wait grows, so nobody waits forever."""
        return DUEL_WINDOW + DUEL_WINDOW_STEP * (waited // DUEL_WINDOW_STEP_S)

    async def queue_duel(self, conn: SeatConn) -> dict[str, Any]:
        """Put a signed-in seat in the duel queue, and pair if there is a match.

        Guests are refused: a duel is rated, and a rating needs an account. A
        seat already in a room leaves it first, the way a join would.
        """
        from backend.games_server import hassault_rating

        if conn.is_guest:
            raise ValueError("sign in to play a rated duel")
        await self.leave(conn)
        if not any(c is conn for c, _, _ in self._duel_queue):
            rating = hassault_rating.rating_of(conn.account_id)["rating"]
            self._duel_queue.append((conn, time.monotonic(), rating))
        await self._pair_duels()
        self._ensure_sweeper()
        return self.duel_status(conn)

    def cancel_duel(self, conn: SeatConn) -> bool:
        before = len(self._duel_queue)
        self._duel_queue = [e for e in self._duel_queue if e[0] is not conn]
        return len(self._duel_queue) != before

    def duel_status(self, conn: SeatConn) -> dict[str, Any]:
        for position, (c, since, rating) in enumerate(self._duel_queue):
            if c is conn:
                waited = time.monotonic() - since
                return {
                    "queued": True,
                    "position": position + 1,
                    "waiting": len(self._duel_queue),
                    "waited": round(waited, 1),
                    "window": self.duel_window(waited),
                    "rating": round(rating, 1),
                }
        return {"queued": False, "waiting": len(self._duel_queue)}

    def _ensure_sweeper(self) -> None:
        """A loop that re-tries pairing as windows widen, while anyone waits."""
        if self._duel_sweeper is not None and not self._duel_sweeper.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        async def sweep() -> None:
            while self._duel_queue:
                await asyncio.sleep(DUEL_SWEEP_S)
                self._duel_queue = [e for e in self._duel_queue if not e[0].closed]
                await self._pair_duels()
                for conn, _, _ in list(self._duel_queue):
                    await conn.send_json(
                        {"channel": "hassault", "event": "duel_queue", "data": self.duel_status(conn)}
                    )

        self._duel_sweeper = loop.create_task(sweep())

    async def _pair_duels(self) -> None:
        """Pair the oldest waiting seat with the closest rating inside its
        window, repeatedly, until no pair is left to make."""
        now = time.monotonic()
        made = True
        while made and len(self._duel_queue) >= 2:
            made = False
            for i, (a, since_a, rating_a) in enumerate(self._duel_queue):
                window = self.duel_window(now - since_a)
                best = None
                for j, (b, since_b, rating_b) in enumerate(self._duel_queue):
                    if j == i or b.account_id == a.account_id:
                        continue
                    gap = abs(rating_a - rating_b)
                    both = min(window, self.duel_window(now - since_b))
                    if gap <= both and (best is None or gap < best[0]):
                        best = (gap, j)
                if best is not None:
                    j = best[1]
                    b = self._duel_queue[j][0]
                    self._duel_queue = [
                        e for k, e in enumerate(self._duel_queue) if k not in (i, j)
                    ]
                    await self._start_duel(a, b)
                    made = True
                    break

    def duel_map(self) -> str:
        """The map every duel is fought on. Served (`/hassault/maps`), because
        a client loads its world before it queues and must load this one."""
        candidates = [m for m in DUEL_MAPS if self.playable(m)] or self.maps()
        return candidates[0] if candidates else "hd_pit"

    async def _start_duel(self, a: SeatConn, b: SeatConn) -> None:
        """Open a duel room for two seats, seat them, and send each a welcome."""
        map_name = self.duel_map()
        room = self.server.create(map_name, mode="duel")
        room.on_over = lambda r, summary: self._duel_over(r, summary)
        for conn in (a, b):
            loadouts, look = self.account_look(conn)
            joined_room, player = await self.server.join(
                conn,
                map_name,
                conn.display_name,
                room.id,
                loadouts=loadouts,
                cosmetics=look,
            )
            welcome = joined_room.state_payload()
            welcome["playerId"] = player.id
            welcome["rated"] = True
            welcome["roomSettings"] = self.settings_payload(joined_room)
            await conn.send_json({"channel": "hassault", "event": "welcome", "data": welcome})

    def _duel_over(self, room: Any, summary: dict[str, Any]) -> None:
        """Rate a finished duel. Called from inside the tick, so the rating and
        the messages that announce it go out on a task of their own."""
        seats: dict[int, SeatConn] = {}
        gone = summary.get("seats") or {}
        for player in list(room.players.values()):
            if isinstance(player.conn, SeatConn) and not player.conn.is_guest:
                seats[player.team] = player.conn
        # A forfeit: the leaver's conn is no longer on a player. Recover it.
        leaver = getattr(room, "_last_left_conn", None)
        if isinstance(leaver, SeatConn) and not leaver.is_guest:
            for pid, team in gone.items():
                if team not in seats:
                    seats[team] = leaver
        if set(seats) != {0, 1}:
            return
        winner = int(summary.get("winner", -1))
        score_a = 0.5 if winner not in (0, 1) else (1.0 if winner == 0 else 0.0)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        async def announce() -> None:
            from backend.games_server import hassault_rating

            rated = hassault_rating.rate_match(
                f"duel:{room.id}",
                seats[0].account_id,
                seats[1].account_id,
                score_a,
                reason=str(summary.get("reason") or ""),
            )
            if rated is None:
                return
            for conn in (seats[0], seats[1]):
                mine = rated[conn.account_id]
                await conn.send_json(
                    {
                        "channel": "hassault",
                        "event": "rating_update",
                        "data": {
                            "queue": "duel",
                            "before": mine["before"],
                            "after": mine["after"],
                            "delta": round(mine["after"] - mine["before"], 1),
                            "rating": hassault_rating.rating_of(conn.account_id),
                            "reason": summary.get("reason"),
                            "won": (score_a == 1.0 and conn is seats[0])
                            or (score_a == 0.0 and conn is seats[1]),
                            "draw": score_a == 0.5,
                        },
                    }
                )

        if loop is None:
            asyncio.run(announce())
        else:
            loop.create_task(announce())

    async def leave(self, conn: SeatConn) -> dict[str, Any] | None:
        """Take a player out and **write down what they did**.

        Recorded on leaving rather than when the room empties, because a
        deathmatch has no natural end: players arrive and go, and a card that
        waited for the last of them would arrive for everyone at once, hours
        later. What a player did in their session is final the moment they stop
        playing.
        """
        self.cancel_duel(conn)
        entry = self.server.player_for(conn)
        if entry is not None:
            # For a duel forfeit, which is rated after the leaver's player is gone.
            entry[0]._last_left_conn = conn
        result = await self.server.leave(conn)
        if result is None:
            return None
        result["authority"] = "server"
        self._record(conn, result)
        # The host may have been the one who left: tell whoever is still there
        # who holds the room now, so their console knows whose call it is.
        room = self.server.rooms.get(str(result.get("room", "")))
        if room is not None and room.humans:
            await self.server.broadcast_event(
                room, "room_settings", self.settings_payload(room)
            )
        return result

    async def _kick(self, conn: SeatConn) -> None:
        """Take an AFK player out (`MatchServer.kick_handler`). The socket stays
        open — the client is told why and may join again — and the result goes
        back exactly as the `leave` event sends it, so the node records it."""
        result = await self.leave(conn)
        if result is not None:
            await conn.send_json(
                {"channel": "hassault", "event": "result", "data": result}
            )

    def _record(self, conn: SeatConn, result: dict[str, Any]) -> None:
        """Log the session and grant its XP through the server's own store.

        **Unrated, deliberately.** `store.record_result` applies ELO to two seats
        facing each other; a free-for-all in which people come and go is not that
        shape, and inventing a rating for it here would be the very thing this
        module exists to stop. What it does give is a `results` row written by the
        server and XP granted by the server — both real, both the server's word.
        A 1v1 duel mode can be rated properly, and that is where ELO belongs.
        """
        if conn.is_guest:
            return
        from backend.games_server import store

        payoff = 1.0 if result.get("won") else -1.0
        try:
            store.record_result(
                GAME_ID,
                str(result.get("room", "")),
                [conn.account_id],
                {0: payoff},
                0 if result.get("won") else None,
                rated=False,
                ruleset={
                    "map": result.get("map"),
                    # The room's mode (`join` takes one since P6). A row from
                    # before then has none, which reads as `dm`: the only mode
                    # these rooms ran.
                    "mode": result.get("mode", "dm"),
                    "kills": result.get("kills"),
                    "deaths": result.get("deaths"),
                    "headKills": result.get("headKills"),
                    "damageDealt": result.get("damageDealt"),
                    "objectives": result.get("objectives"),
                    "opponents": result.get("opponents"),
                },
            )
        except Exception:
            # A store hiccup must not break a player's disconnect — the same
            # guard `hub.py` puts around its own result write.
            logger.exception("hassault: could not record a server-hosted result")
        # XP into the account's ledger: the server's word, since the server ran
        # the match. Keyed by a per-session id so a retry cannot pay twice.
        try:
            import uuid

            from backend.games_server import hassault_account

            hassault_account.grant_match_xp(
                conn.account_id, str(result.get("matchId") or uuid.uuid4()), result
            )
        except Exception:
            logger.exception("hassault: could not ledger XP for a server-hosted result")

    async def shutdown(self) -> None:
        await self.server.shutdown()


referee = HassaultReferee()

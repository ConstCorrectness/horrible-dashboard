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

import json
import logging
import os
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

#: The id these matches are logged and rated under, in `results` and `ratings`.
GAME_ID = "hassault"


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
        self, conn: SeatConn, map_name: str, room_id: str | None = None
    ) -> dict[str, Any]:
        """Seat a player, opening a room if there is none on that map.

        Deathmatch default. If joining by explicit room_id, uses the room's existing
        map; otherwise verifies that map_name is a valid bundled map.

        The welcome is the room's whole `state_payload` — items, what has been
        taken, the mode, the scores — exactly what a node sends its own browser.
        It used to be five fields, so a client joining here drew no pickups and
        no mode until something happened to mention them.
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
        if existing is None and not room_id and not self._has_space(target_map):
            if len(self.server.rooms) >= max_rooms():
                raise ValueError(
                    "this server is full; try a room that is already running"
                )
        room, player = await self.server.join(
            conn, target_map, conn.display_name, room_id
        )
        has_guests = conn.is_guest or any(
            getattr(p.conn, "is_guest", False) for p in room.players.values()
        )
        welcome = room.state_payload()
        welcome["playerId"] = player.id
        welcome["rated"] = not has_guests
        return welcome

    def _has_space(self, map_name: str) -> bool:
        """Whether a join on `map_name` would land in a room that already exists —
        the same test `MatchServer.find_or_create` makes before it opens one."""
        return any(
            room.map_name == map_name
            and room.mode.id == modes.DEFAULT_MODE
            and len(room.players) < MAX_PLAYERS
            for room in self.server.rooms.values()
        )

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

    async def leave(self, conn: SeatConn) -> dict[str, Any] | None:
        """Take a player out and **write down what they did**.

        Recorded on leaving rather than when the room empties, because a
        deathmatch has no natural end: players arrive and go, and a card that
        waited for the last of them would arrive for everyone at once, hours
        later. What a player did in their session is final the moment they stop
        playing.
        """
        result = await self.server.leave(conn)
        if result is None:
            return None
        result["authority"] = "server"
        self._record(conn, result)
        return result

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
                    # Always `dm` today — see `join`. Written anyway so a row
                    # from before a second mode existed is distinguishable from
                    # one where nobody recorded which, which is not the same
                    # fact.
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

    async def shutdown(self) -> None:
        await self.server.shutdown()


referee = HassaultReferee()

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
import time
from typing import Any

from backend.modules.hassault import mapsource
from backend.modules.hassault.match import (
    MAX_NAME_LEN,
    MatchServer,
    parse_command,
)

#: Matches the browser channel's cap: an input message is one frame's worth of
#: commands plus whatever a lossy link made it re-send, never an unbounded batch.
MAX_COMMANDS_PER_MESSAGE = 64

logger = logging.getLogger(__name__)

#: The id these matches are logged and rated under, in `results` and `ratings`.
GAME_ID = "hassault"


class SeatConn:
    """One player's socket, in the shape `MatchRoom` expects.

    The simulation only ever calls `send_json`, so this is the entire contract —
    plus the account id, which is *ours* rather than the simulation's: the match
    knows a display name, and the ladder needs an account. Keeping the mapping
    here is what stops a player naming themselves into somebody else's row.
    """

    def __init__(self, websocket: Any, account_id: str, display_name: str) -> None:
        self.websocket = websocket
        self.account_id = account_id
        self.display_name = display_name[:MAX_NAME_LEN] or "player"
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
        self.server = MatchServer()

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

    # -- play ---------------------------------------------------------------

    async def join(
        self, conn: SeatConn, map_name: str, room_id: str | None = None
    ) -> dict[str, Any]:
        """Seat a player, opening a room if there is none on that map."""
        if not self.playable(map_name):
            raise ValueError(f"{map_name!r} is not a bundled map")
        room, player = await self.server.join(
            conn, map_name, conn.display_name, room_id
        )
        return {
            "room": room.id,
            "map": room.map_name,
            "playerId": player.id,
            "rated": True,
            "players": [p.snapshot(time.monotonic()) for p in room.players.values()],
        }

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
                    "kills": result.get("kills"),
                    "deaths": result.get("deaths"),
                    "headKills": result.get("headKills"),
                    "damageDealt": result.get("damageDealt"),
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

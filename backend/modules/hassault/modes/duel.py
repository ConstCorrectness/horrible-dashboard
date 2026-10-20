"""Duel: one against one, first to `KILLS_TO_WIN`, rated on the game server.

The one hassault mode that has an **ending a rating can be built on**: two seats,
facing each other, until one of them has the kills or the clock runs out. That
is the shape `rating.compute_glicko2_step` wants (a result between two players),
and deathmatch is not: people come and go, and "you outscored the room" is not a
game against anybody in particular.

Phases: `warmup` until both seats are filled, then `live` for `TIME_LIMIT`
seconds, then `over`. The match ends at `KILLS_TO_WIN`, at the time limit (most
kills wins, equal is a draw), or the moment one player leaves a live match,
which forfeits it to the other: a rating a player could protect by quitting
when behind is a rating of who quits.

`over` is announced once, as a `match_over` effect and through `room.on_over`,
which is how the game server's referee learns to rate it
(`games_server/hassault_rating.py`). This module rates nothing itself.
"""

from __future__ import annotations

import math
from itertools import combinations
from typing import TYPE_CHECKING, Any

from .base import GameMode

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..match import MatchPlayer, MatchRoom

KILLS_TO_WIN = 10
#: Seconds of live play before the clock decides it.
TIME_LIMIT = 180.0

WARMUP = "warmup"
LIVE = "live"
OVER = "over"

#: Eye height above the floor, in cubes, for the spawn sightline check.
EYE_HEIGHT = 4.5
#: Brute force covers at most this many spawns a side (2^n subsets each).
MAX_SPAWNS_SEARCHED = 8


def sightline(world: Any, a: Any, b: Any) -> tuple[bool, float]:
    """Whether two spawn entities see each other at eye height, through the
    server's own hitscan ray, and how far apart they are."""
    from .. import weapons

    ax, ay = a.x + 0.5, a.y + 0.5
    bx, by = b.x + 0.5, b.y + 0.5
    az = world.floor_at(int(ax), int(ay)) + EYE_HEIGHT
    bz = world.floor_at(int(bx), int(by)) + EYE_HEIGHT
    dx, dy, dz = bx - ax, by - ay, bz - az
    reach = math.sqrt(dx * dx + dy * dy + dz * dz)
    if reach < 1e-6:
        return True, 0.0
    hit = weapons.raycast_world(
        world, (ax, ay, az), (dx / reach, dy / reach, dz / reach), reach
    )
    return hit >= reach - 1.0, reach


def candidates(map_name: str, spawns: list[Any]) -> list[Any]:
    """The spawns a duel on `map_name` may use: the map's own `duelSpawns` when
    its source declares them, else its team spawns.

    A defuse map's team spawns are a map apart on purpose (Office's are 55 m and
    three walls), so a map that hosts duels names a pair of places facing each
    other across one room, `{x, y, yaw, team}` in cubes like a `playerstart`.
    They are still put through `duel_spawns`, so a declared spawn with no line
    to the other side is dropped rather than trusted.
    """
    import json

    from .. import mapsource
    from ..cgz import PLAYERSTART, MapEntity

    path = mapsource.source_path(map_name) if map_name else None
    if path is None:
        return spawns
    declared = json.loads(path.read_text(encoding="utf-8")).get("duelSpawns")
    if not isinstance(declared, list) or not declared:
        return spawns
    return [
        MapEntity(
            type=PLAYERSTART,
            name="playerstart",
            x=int(s["x"]),
            y=int(s["y"]),
            z=0,
            attr1=round(float(s.get("yaw", 0)) % 360.0 * 10),
            attr2=1 if s.get("team") == 1 else 0,
        )
        for s in declared
    ]


def duel_spawns(world: Any, spawns: list[Any]) -> dict[int, list[Any]]:
    """The largest sets of team-0 and team-1 spawns in which **every** pair has
    a clear line: whichever two the room's RNG draws, the players can see each
    other. Largest by the smaller side first, then pairs, then the shortest
    mean distance. Empty when no pair at all is clear.

    A brute-force biclique search, which is fine at the four or so spawns a
    side these maps have; it runs once, when the room opens.
    """
    a = [e for e in spawns if e.attr2 == 0][:MAX_SPAWNS_SEARCHED]
    b = [e for e in spawns if e.attr2 == 1][:MAX_SPAWNS_SEARCHED]
    sees = {(i, j): sightline(world, s, t) for i, s in enumerate(a) for j, t in enumerate(b)}
    best: tuple[tuple[int, int, float], tuple[int, ...], tuple[int, ...]] | None = None
    for na in range(1, len(a) + 1):
        for sa in combinations(range(len(a)), na):
            for nb in range(1, len(b) + 1):
                for sb in combinations(range(len(b)), nb):
                    pairs = [sees[(i, j)] for i in sa for j in sb]
                    if not all(clear for clear, _ in pairs):
                        continue
                    mean = sum(r for _, r in pairs) / len(pairs)
                    key = (min(na, nb), na * nb, -mean)
                    if best is None or key > best[0]:
                        best = (key, sa, sb)
    if best is None:
        return {0: [], 1: []}
    return {0: [a[i] for i in best[1]], 1: [b[j] for j in best[2]]}


class Duel(GameMode):
    id = "duel"
    name = "Duel"
    teams = True
    score_label = "Kills"

    def __init__(self) -> None:
        self.phase = WARMUP
        self.remaining = TIME_LIMIT
        #: The winning team once `over`, `-1` for a draw.
        self.winner = -1
        #: Why it ended: `kills`, `time` or `forfeit`.
        self.reason = ""

    # -- lifecycle ----------------------------------------------------------

    def attach(self, room: MatchRoom) -> None:
        #: The spawns each side duels from: a set in which every pair sees the
        #: other (`duel_spawns`), or none, which falls back to the room's rule.
        #: Drawn from the map's `duelSpawns` when it has them (`candidates`).
        self.spawns_by_team = duel_spawns(
            room.world, candidates(getattr(room, "map_name", ""), list(room.spawns))
        )

    def spawn_options(self, room: MatchRoom, team: int) -> list[Any] | None:
        return getattr(self, "spawns_by_team", {}).get(team) or None

    def reset(self, room: MatchRoom) -> None:
        self.phase = WARMUP
        self.remaining = TIME_LIMIT
        self.winner = -1
        self.reason = ""
        room.scores[:] = [0, 0]

    def _seated(self, room: MatchRoom) -> list[MatchPlayer]:
        return [p for p in room.players.values() if not p.is_bot]

    def tick(self, room: MatchRoom, elapsed: float, now: float) -> None:
        if self.phase == WARMUP:
            teams = {p.team for p in self._seated(room)}
            if teams >= {0, 1}:
                self.phase = LIVE
                self.remaining = TIME_LIMIT
                room.scores[:] = [0, 0]
                for player in self._seated(room):
                    player.kills = 0
                    player.deaths = 0
                    room.respawn(player)
                room._emit({"kind": "objective", "event": "duel_live"})
            return
        if self.phase != LIVE:
            return
        self.remaining = max(0.0, self.remaining - elapsed)
        top = max(room.scores[:2]) if room.scores else 0
        if top >= KILLS_TO_WIN:
            self._finish(room, 0 if room.scores[0] > room.scores[1] else 1, "kills")
        elif self.remaining <= 0:
            a, b = room.scores[0], room.scores[1]
            self._finish(room, -1 if a == b else (0 if a > b else 1), "time")

    def on_leave(self, room: MatchRoom, player: MatchPlayer) -> None:
        if self.phase == LIVE and not player.is_bot:
            # Forfeit: the one still here wins.
            # `remove` has already taken them out of `room.players`; the
            # summary still needs both seats to rate.
            self._finish(
                room,
                1 - player.team if player.team in (0, 1) else -1,
                "forfeit",
                leaver=player,
            )

    def _finish(
        self,
        room: MatchRoom,
        winner: int,
        reason: str,
        leaver: MatchPlayer | None = None,
    ) -> None:
        if self.phase == OVER:
            return
        self.phase = OVER
        self.winner = winner
        self.reason = reason
        summary = self.summary(room, leaver)
        room._emit({"kind": "objective", "event": "match_over", **summary})
        hook = getattr(room, "on_over", None)
        if hook is not None:
            hook(room, summary)

    def summary(
        self, room: MatchRoom, leaver: MatchPlayer | None = None
    ) -> dict[str, Any]:
        """Who played which side, the score, and who won: what a rating needs."""
        seats = {p.id: p.team for p in self._seated(room)}
        if leaver is not None and not leaver.is_bot:
            seats[leaver.id] = leaver.team
        return {
            "winner": self.winner,
            "reason": self.reason,
            "scores": list(room.scores[:2]),
            "seats": seats,
        }

    # -- simulation ---------------------------------------------------------

    def damage_scale(
        self, room: MatchRoom, attacker: MatchPlayer, victim: MatchPlayer
    ) -> float:
        if self.phase != LIVE:
            return 0.0
        return 0.0 if attacker.team == victim.team else 1.0

    def on_kill(
        self,
        room: MatchRoom,
        victim: MatchPlayer,
        attacker: MatchPlayer,
        head: bool,
        weapon: Any,
    ) -> None:
        if self.phase == LIVE and attacker.id != victim.id:
            super().on_kill(room, victim, attacker, head, weapon)

    def may_respawn(self, room: MatchRoom, player: MatchPlayer, now: float) -> bool:
        return self.phase != OVER

    def afk_clock_runs(self, room: MatchRoom, player: MatchPlayer) -> bool:
        return self.phase == LIVE

    # -- wire ---------------------------------------------------------------

    def welcome_state(self, room: MatchRoom) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "scoreLabel": self.score_label,
            "v": self.version,
            "teams": self.teams,
            "config": {"killsToWin": KILLS_TO_WIN, "timeLimit": TIME_LIMIT},
            **self.shared_state(room),
        }

    def shared_state(self, room: MatchRoom) -> dict[str, Any]:
        out: dict[str, Any] = {
            "phase": self.phase,
            "phaseIn": round(self.remaining, 1),
        }
        if self.phase == OVER:
            out["winner"] = self.winner
            out["reason"] = self.reason
        return out

    # -- results ------------------------------------------------------------

    def outcome_for(self, room: MatchRoom, player: MatchPlayer) -> tuple[bool, bool]:
        if self.phase == OVER:
            won = self.winner == player.team
            return (won, won)
        mine = room.scores[player.team] if player.team in (0, 1) else 0
        theirs = room.scores[1 - player.team] if player.team in (0, 1) else 0
        return (mine > theirs, mine > theirs)

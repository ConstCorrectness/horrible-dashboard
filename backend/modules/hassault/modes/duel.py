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

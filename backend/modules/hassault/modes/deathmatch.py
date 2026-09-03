"""Free-for-all and team deathmatch.

Two modes and no new mechanics, which is the point of shipping them first: if
`Deathmatch(teams=True)` needs anything the hooks in `base` do not already offer,
the abstraction is wrong and it is cheap to find out here rather than three modes
later.

Free-for-all is the behaviour `MatchRoom` had before modes existed, and this file
is where the reasoning that used to live in `MatchRoom.result_for` now lives with
the rule it explains.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import GameMode

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..match import MatchPlayer, MatchRoom


class Deathmatch(GameMode):
    """Kills are the score. With `teams`, they are the *team's* score."""

    def __init__(self, teams: bool = False) -> None:
        # Shadows the class attribute on `GameMode` on purpose: this is the one
        # mode whose answer depends on which of the two it is.
        self.teams = teams
        self.id = "tdm" if teams else "dm"
        self.name = "Team Deathmatch" if teams else "Deathmatch"

    score_label = "Kills"

    # No `damage_scale` override, and that is deliberate rather than an omission.
    #
    # **Both of these have friendly fire off**, including the one called plain
    # deathmatch. `MatchRoom.add` auto-balances every joiner into team 0 or 1 and
    # `_spawn_state` picks a spawn by that team, so the shipped game already had
    # sides whether or not anything called them that — and the comment this
    # replaced said why turning friendly fire on would ruin it: a small match on
    # a map with team spawns is otherwise decided by who turns around first.
    #
    # So the difference between these two modes is not who you can shoot. It is
    # what the score *means* and how the debrief reads it, which is `outcome_for`
    # below. A genuine free-for-all — no sides, everyone a target — is a third
    # mode and a real change to how a match plays, not a default worth slipping
    # in under an existing name.

    def welcome_state(self, room: MatchRoom) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "scoreLabel": self.score_label,
            "v": self.version,
            "teams": self.teams,
        }

    def outcome_for(self, room: MatchRoom, player: MatchPlayer) -> tuple[bool, bool]:
        """Who won, and who was best.

        In plain deathmatch this is **relative to the room**, not to a team score:
        you won if nobody outscored you, and you are the MVP if nobody equalled
        you either. Bots count — losing to one is losing, and a card that quietly
        excluded them would be flattering rather than true.

        With teams it is the team score, because a player who went 2-14 on the
        winning side did win, and telling them otherwise would be describing a
        different game than the one the scoreboard showed.
        """
        if self.teams:
            mine = room.scores[player.team] if player.team < len(room.scores) else 0
            theirs = max(
                (s for i, s in enumerate(room.scores) if i != player.team),
                default=0,
            )
            best = max(
                (p.kills for p in room.players.values() if p.team == player.team),
                default=0,
            )
            return (mine > theirs, mine > theirs and player.kills >= best)
        others = [p.kills for p in room.players.values() if p.id != player.id]
        if not others:
            return (False, False)
        return (player.kills >= max(others), player.kills > max(others))

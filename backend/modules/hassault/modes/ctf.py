"""Capture the flag.

Two flags, one per team, each with a stand it starts on. Take the enemy's, carry
it to your own, and score — provided your own flag is home, which is the rule that
makes a full defence worth playing rather than a race.

## Why the flag is a piece of state and not an item

`pickups` would nearly fit: a flag is a thing on the floor you walk into. It is
the wrong shape for two reasons that are the whole mode — a flag comes *back*,
and a flag is somewhere specific while it is being carried. An item's whole
lifecycle is "taken, then respawns"; a flag's is a small state machine, and
running one through `Field.collect` would mean the mode reading state it does not
own out of a structure that cannot express it.

## The rules that were each a bug first

- **Taking is automatic; returning is not.** Walking over the enemy flag picks it
  up, because that is what every game in this genre does and a player who has to
  press a key to take a flag they ran onto will not notice it is there. Walking
  over your *own* dropped flag returns it — also automatic, for the same reason.
  `use` exists on the command for defuse's sake and CTF deliberately needs none of
  it, which is worth stating: a mode that requires a key for something the player
  expects to be automatic is a mode that reads as broken.
- **A carrier who dies drops the flag where they fell**, and it stays there on a
  timer before going home. Vanishing it back to the stand instantly removes every
  fight over a dropped flag, which is most of the mode; leaving it forever means a
  flag can be parked somewhere nobody goes.
- **A capture needs your own flag at home.** Without that rule the mode is two
  teams running past each other, and the fastest team wins every round with no
  defence played at all.
- **A flag is dropped, not deleted, when its carrier leaves the match.** They are
  gone; the flag is still a thing on the map, and the players still in the room
  are owed the chance to fight over it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .base import GameMode, Goal
from .objectives import Flag

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..match import Command, MatchPlayer, MatchRoom

#: Captures to win.
CAPTURES_TO_WIN = 3

#: How long a dropped flag lies where it fell before returning itself.
#:
#: Long enough that a dropped flag is a fight, short enough that one parked in a
#: corner of the map does not stall the match. AC uses 30; this is shorter
#: because these maps are smaller.
DROP_RETURN_AFTER = 20.0

#: How close you have to be to take, return or capture, in cubes.
#:
#: A little wider than an item's reach: a flag is a destination you run at rather
#: than something you notice on the floor, and missing a capture by half a cube
#: while standing on your own stand is infuriating in a way that missing an ammo
#: box is not.
FLAG_REACH = 2.5


@dataclass(slots=True)
class FlagState:
    """One team's flag: where it is, and whose problem it currently is."""

    team: int
    home: Flag
    #: `"home"`, `"carried"` or `"dropped"`.
    state: str = "home"
    #: Player id of the carrier, while carried.
    carrier: str = ""
    #: Where it lies, while dropped.
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    #: Room-clock instant a dropped flag returns itself.
    return_at: float = 0.0

    @property
    def at_home(self) -> bool:
        return self.state == "home"

    def position(self, room: MatchRoom) -> tuple[float, float, float]:
        """Where the flag is right now, in cubes.

        A carried flag is wherever its carrier is, which is why this is derived
        rather than stored: storing it would mean a second copy of a position
        that already moves 20 times a second, and the two would disagree for a
        tick every time somebody was killed.
        """
        if self.state == "carried":
            carrier = room.players.get(self.carrier)
            if carrier is not None:
                return (carrier.state.x, carrier.state.y, carrier.state.z)
        if self.state == "dropped":
            return (self.x, self.y, self.z)
        return (self.home.x, self.home.y, self.home.z)

    def send_home(self) -> None:
        self.state = "home"
        self.carrier = ""
        self.return_at = 0.0


def _near(a: tuple[float, float, float], b: tuple[float, float, float]) -> bool:
    """Within `FLAG_REACH` horizontally, and on roughly the same level.

    Split rather than a 3D distance for the reason `pickups.in_reach` is: a
    player on a gantry directly above a flag is not standing on it, and a sphere
    says they are.
    """
    dx, dy = a[0] - b[0], a[1] - b[1]
    if dx * dx + dy * dy > FLAG_REACH * FLAG_REACH:
        return False
    return abs(a[2] - b[2]) <= 3.0


class CaptureTheFlag(GameMode):
    id = "ctf"
    name = "Capture the Flag"
    score_label = "Captures"

    def __init__(self, captures_to_win: int = CAPTURES_TO_WIN) -> None:
        self.captures_to_win = captures_to_win
        self.flags: dict[int, FlagState] = {}
        self.over = False

    # -- lifecycle ----------------------------------------------------------

    def attach(self, room: MatchRoom) -> None:
        self.flags = {
            flag.team: FlagState(team=flag.team, home=flag)
            for flag in room.objectives.flags
        }

    def reset(self, room: MatchRoom) -> None:
        for flag in self.flags.values():
            flag.send_home()
        room.scores[:] = [0, 0]
        self.over = False

    def on_leave(self, room: MatchRoom, player: MatchPlayer) -> None:
        """Drop, never delete.

        The carrier is gone; the flag is still a thing on the map, and the people
        still in the room are owed the chance to fight over it. Deleting it would
        make quitting-while-carrying a way to deny a flag entirely.
        """
        for flag in self.flags.values():
            if flag.carrier == player.id:
                self._drop(room, flag, player)

    # -- simulation ---------------------------------------------------------

    def tick(self, room: MatchRoom, elapsed: float, now: float) -> None:
        if self.over:
            return
        for flag in self.flags.values():
            if flag.state == "dropped" and now >= flag.return_at:
                flag.send_home()
                room._emit({"kind": "flag_return", "team": flag.team, "auto": True})

        for player in room.players.values():
            if player.alive:
                self._touch(room, player, now)
            else:
                self._drop_if_carrying(room, player)

    def on_kill(
        self,
        room: MatchRoom,
        victim: MatchPlayer,
        attacker: MatchPlayer,
        head: bool,
        weapon: Any,
    ) -> None:
        """Drop the flag **here**, at the moment of death. A kill does not score.

        Note there is no `super().on_kill(...)` call, and its absence is the
        point: the base implementation adds one to the killer's team score, which
        is right for deathmatch and wrong here, where `scores` counts *captures*.
        Inheriting it silently mixed the two — a match reading 9 to 0 off six
        captures and three kills, under a scoreboard labelled "Captures". Both
        numbers were real, which is why it looked plausible.

        Kills still count: `_apply_damage` credits `attacker.kills`
        unconditionally, and `outcome_for` weighs them when picking the MVP.

        Dropping it in `tick` instead is not equivalent, and the difference is a
        real bug rather than a style point: `simulate` runs `_respawn_due` *before*
        `mode.tick`, so a player whose respawn timer has already elapsed is alive
        again — and standing somewhere else — by the time the mode gets to look.
        The flag would then either stay carried by a player who died, or be
        dropped at their spawn.

        The poll in `tick` stays as the backstop for a death with no killer: a
        fall or a drowning goes through `_fall_damage`, which deliberately does
        not come through here because there is nobody to credit.
        """
        self._drop_if_carrying(room, victim)

    def on_command(
        self, room: MatchRoom, player: MatchPlayer, command: Command, now: float
    ) -> None:
        """CTF needs nothing from `use`.

        Stated rather than left implicit: taking and returning are automatic on
        contact, because a player who has to press a key for a flag they just ran
        onto will not notice the flag is there. The hook is here so the reason is
        written down next to the mode that does not use it.
        """

    def _touch(self, room: MatchRoom, player: MatchPlayer, now: float) -> None:
        """Everything contact with a flag can mean, for one player."""
        here = (player.state.x, player.state.y, player.state.z)
        mine = self.flags.get(player.team)
        theirs = next((f for f in self.flags.values() if f.team != player.team), None)

        # Your own flag: return it if it is lying about, or capture onto it.
        if mine is not None and _near(here, mine.position(room)):
            if mine.state == "dropped":
                mine.send_home()
                room._emit({"kind": "flag_return", "team": mine.team, "by": player.id})
            elif mine.at_home and theirs is not None and theirs.carrier == player.id:
                self._capture(room, player, theirs)
                return

        # The enemy's: pick it up, wherever it is standing or lying.
        if theirs is None or theirs.state == "carried":
            return
        if not _near(here, theirs.position(room)):
            return
        theirs.state = "carried"
        theirs.carrier = player.id
        theirs.return_at = 0.0
        room._emit({"kind": "flag_take", "team": theirs.team, "by": player.id})

    def _capture(
        self, room: MatchRoom, player: MatchPlayer, carried: FlagState
    ) -> None:
        carried.send_home()
        if 0 <= player.team < len(room.scores):
            room.scores[player.team] += 1
        player.objectives += 1
        room._emit({"kind": "capture", "team": player.team, "by": player.id})
        if room.scores[player.team] >= self.captures_to_win:
            self.over = True
            room._emit({"kind": "match_over", "team": player.team})

    def _drop_if_carrying(self, room: MatchRoom, player: MatchPlayer) -> None:
        for flag in self.flags.values():
            if flag.carrier == player.id:
                self._drop(room, flag, player)

    def _drop(self, room: MatchRoom, flag: FlagState, player: MatchPlayer) -> None:
        flag.state = "dropped"
        flag.carrier = ""
        flag.x, flag.y, flag.z = (
            player.state.x,
            player.state.y,
            player.state.z,
        )
        flag.return_at = time.monotonic() + DROP_RETURN_AFTER
        room._emit({"kind": "flag_drop", "team": flag.team, "by": player.id})

    def may_respawn(self, room: MatchRoom, player: MatchPlayer, now: float) -> bool:
        """Normal deathmatch respawns. CTF has no rounds to hold anyone out of."""
        return not self.over

    # -- wire ---------------------------------------------------------------

    def welcome_state(self, room: MatchRoom) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "scoreLabel": self.score_label,
            "v": self.version,
            "teams": True,
            "config": {
                "capturesToWin": self.captures_to_win,
                "dropReturnAfter": DROP_RETURN_AFTER,
                "reach": FLAG_REACH,
            },
            # Where each stand is. Static, so it rides once with the welcome
            # rather than twenty times a second.
            "stands": [f.home.to_dict() for f in self.flags.values()],
            **(self.shared_state(room) or {}),
        }

    def shared_state(self, room: MatchRoom) -> dict[str, Any]:
        """Public, and deliberately so — including who is carrying.

        Every game in this genre shows the carrier, the flag's position is
        already visible on the map, and hiding the name while showing the
        position would be incoherent rather than secret.
        """
        now = time.monotonic()
        rows = []
        for flag in self.flags.values():
            x, y, z = flag.position(room)
            row: dict[str, Any] = {
                "team": flag.team,
                "state": flag.state,
                "x": round(x, 2),
                "y": round(y, 2),
                "z": round(z, 2),
            }
            if flag.state == "carried":
                row["by"] = flag.carrier
            elif flag.state == "dropped":
                row["returnIn"] = round(max(0.0, flag.return_at - now), 1)
            rows.append(row)
        return {"flags": rows, "over": self.over}

    def private_state(self, room: MatchRoom, player: MatchPlayer) -> dict[str, Any]:
        """What this player needs that the shared rows do not already say.

        `carrying` is derivable from the shared rows and is sent anyway, because
        the alternative is every client searching a list every frame to answer a
        question about itself.
        """
        return {
            "carrying": any(f.carrier == player.id for f in self.flags.values()),
            "captures": player.objectives,
        }

    # -- results ------------------------------------------------------------

    def outcome_for(self, room: MatchRoom, player: MatchPlayer) -> tuple[bool, bool]:
        mine = room.scores[player.team] if player.team < len(room.scores) else 0
        theirs = max(
            (s for i, s in enumerate(room.scores) if i != player.team), default=0
        )
        if mine <= theirs:
            return (False, False)

        # MVP goes to the best *contribution*, not the most kills: in a mode
        # about carrying a flag, the player who took every one of them and died
        # doing it did more than the one who camped a corridor.
        def worth(p: MatchPlayer) -> int:
            return p.objectives * 3 + p.kills

        best = max(
            (worth(p) for p in room.players.values() if p.team == player.team),
            default=0,
        )
        return (True, worth(player) >= best)

    # -- bots ---------------------------------------------------------------

    def bot_goal(self, room: MatchRoom, me: MatchPlayer) -> Goal | None:
        """Carry it home, go and get it, or defend your own.

        No navmesh is needed because every position here is a flag stand or a
        place a player actually reached, which `maplint` has already proved
        standable and connected — the same class of position `_pick_roam` uses.
        """
        mine = self.flags.get(me.team)
        theirs = next((f for f in self.flags.values() if f.team != me.team), None)
        if mine is None or theirs is None:
            return None

        if theirs.carrier == me.id:
            # Carrying: go home. If your own flag is out, going home is still
            # right — that is where the capture happens once somebody returns it.
            x, y, z = mine.position(room)
            return Goal(x=x, y=y, z=z, radius=FLAG_REACH)

        if mine.state == "carried":
            # Chase the carrier. Their position is public, so this is not the bot
            # knowing something a player could not.
            carrier = room.players.get(mine.carrier)
            if carrier is not None:
                return Goal(
                    x=carrier.state.x,
                    y=carrier.state.y,
                    z=carrier.state.z,
                    radius=3.0,
                    expires_in=6.0,
                )
        elif mine.state == "dropped":
            return Goal(x=mine.x, y=mine.y, z=mine.z, radius=FLAG_REACH)

        x, y, z = theirs.position(room)
        return Goal(x=x, y=y, z=z, radius=FLAG_REACH)

"""Practice modes that are deathmatch with one rule changed.

- **Aim arena** (`aim`) and **AWP arena** (`awp`): everybody carries the same
  fixed loadout, whatever they picked, and respawns at once. The point is the
  duel at the crosshair, so the loadout is not a decision.
- **Random guns** (`dm_random`): every life hands out a primary drawn from the
  room's own RNG, so a replay of the room deals the same guns in the same
  order.

None of these is rated or has an ending; they are deathmatch scoring.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .. import weapons
from .deathmatch import Deathmatch

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..match import MatchPlayer, MatchRoom

#: A respawn in an arena: long enough to see what killed you, no longer.
ARENA_RESPAWN = 0.6


def _index(weapon_id: str) -> int:
    return weapons.WEAPONS.index(weapons.WEAPON_BY_ID[weapon_id])


class Arena(Deathmatch):
    """Fixed loadout, instant respawn."""

    #: Arena id → the weapons every player holds, primary first.
    LOADOUTS: dict[str, tuple[str, ...]] = {
        "aim": ("assault", "pistol", "knife"),
        "awp": ("sniper", "knife"),
    }
    NAMES = {"aim": "Aim Arena", "awp": "AWP Arena"}

    def __init__(self, kind: str = "aim") -> None:
        super().__init__(teams=False)
        self.kind = kind
        self.id = kind
        self.name = self.NAMES[kind]
        self.carried = tuple(_index(w) for w in self.LOADOUTS[kind])

    def holds(self, room: MatchRoom, player: MatchPlayer, index: int) -> bool:
        return index in self.carried

    def slots(self, room: MatchRoom, player: MatchPlayer) -> dict[str, int]:
        out = {"melee": _index("knife")}
        for index in self.carried:
            slot = weapons.WEAPONS[index].slot
            out.setdefault(slot, index)
        out.setdefault("primary", self.carried[0])
        out.setdefault("secondary", self.carried[0])
        return out

    def outfit(self, room: MatchRoom, player: MatchPlayer) -> None:
        player.held = set(self.carried)
        player.weapon = self.carried[0]

    def respawn_delay(self, room: MatchRoom, player: MatchPlayer) -> float:
        return ARENA_RESPAWN


class RandomGuns(Deathmatch):
    """A primary from the room's RNG every life, the sidearm and knife as ever."""

    id = "dm_random"
    name = "Random Guns"

    def __init__(self) -> None:
        super().__init__(teams=False)
        self.id = "dm_random"
        self.name = "Random Guns"
        self.pool = [i for i, w in enumerate(weapons.WEAPONS) if w.slot == "primary"]

    def outfit(self, room: MatchRoom, player: MatchPlayer) -> None:
        primary = room.rng.choice(self.pool)
        player.held = {primary, player.loadout["secondary"], player.loadout["melee"]}
        player.loadout = {**player.loadout, "primary": primary}
        player.weapon = primary

    def slots(self, room: MatchRoom, player: MatchPlayer) -> dict[str, int]:
        return dict(player.loadout)

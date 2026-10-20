"""Retake: defuse with the bomb already down.

Every round goes live with the bomb planted on a site drawn from the room's RNG,
the attackers spawned on it to hold, and the defenders where they always spawn,
to retake it. There is no economy: each side gets its kit card (its own rifle,
armour, and a kit for the defenders), and nothing is for sale. The rounds, the
fuse, the defuse and the half are `Defuse`'s, unchanged.
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from .. import weapons
from .defuse import FUSE_TIME, LIVE, Defuse

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..match import MatchPlayer, MatchRoom

#: Seconds of freeze before a retake: enough to see where the bomb is.
RETAKE_FREEZE = 4.0

#: Each side's card: the rifle it fights with.
CARDS = {"t": "raider", "ct": "lancer"}


class Retake(Defuse):
    id = "retake"
    name = "Retake"

    def __init__(self) -> None:
        super().__init__()
        #: The site this round's bomb is on.
        self.site_id = ""

    # -- the round ------------------------------------------------------------

    def _perform(self, room: MatchRoom, emit: Any) -> None:
        super()._perform(room, emit)
        if emit.kind == "round_start" and self.sites:
            self._shorten_freeze()
            site = room.rng.choice(self.sites)
            self.site_id = site.id
            self._place_attackers(room, site)
        elif emit.kind == "round_live" and self.site_id:
            self._plant(room)

    def _shorten_freeze(self) -> None:
        self.state = replace(
            self.state, remaining=min(self.state.remaining, RETAKE_FREEZE)
        )

    def _place_attackers(self, room: MatchRoom, site: Any) -> None:
        """The attackers start on the site they are holding, spread round its
        middle on the same floor; the bomb is no one's to carry."""
        attackers = [
            p
            for p in room.players.values()
            if p.team == self.state.attackers and p.alive
        ]
        floor = room.world.floor_at(int(site.x), int(site.y))
        for i, player in enumerate(attackers):
            angle = 2.0 * math.pi * i / max(1, len(attackers))
            reach = min(site.radius * 0.5, 2.0) if len(attackers) > 1 else 0.0
            x = site.x + math.cos(angle) * reach
            y = site.y + math.sin(angle) * reach
            if room.world.floor_at(int(x), int(y)) != floor:
                x, y = site.x, site.y
            player.state.x, player.state.y, player.state.z = x, y, float(floor)
        self.state = replace(
            self.state, bomb=replace(self.state.bomb, state="carried", carrier="")
        )

    def _plant(self, room: MatchRoom) -> None:
        site = next((s for s in self.sites if s.id == self.site_id), None)
        if site is None or self.state.phase != LIVE:
            return
        self.state = replace(
            self.state,
            bomb=replace(
                self.state.bomb,
                state="planted",
                site=site.id,
                carrier="",
                x=site.x,
                y=site.y,
                z=site.z,
                fuse=FUSE_TIME,
            ),
        )
        room._emit(
            {"kind": "bomb_planted", "team": self.state.attackers, "detail": site.id}
        )

    def _give_bomb(self, room: MatchRoom) -> None:
        """Nobody carries it: it goes down by itself when the round goes live."""

    # -- no economy -------------------------------------------------------------

    def can_buy(self, room: MatchRoom, player: MatchPlayer) -> bool:
        return False

    def holds(self, room: MatchRoom, player: MatchPlayer, index: int) -> bool:
        card = weapons.WEAPONS.index(
            weapons.WEAPON_BY_ID[CARDS[weapons.side_of(player.team)]]
        )
        return index in (0, 1, card)

    def slots(self, room: MatchRoom, player: MatchPlayer) -> dict[str, int]:
        card = weapons.WEAPONS.index(
            weapons.WEAPON_BY_ID[CARDS[weapons.side_of(player.team)]]
        )
        return {"primary": card, "secondary": 1, "melee": 0}

    def outfit(self, room: MatchRoom, player: MatchPlayer) -> None:
        card = weapons.WEAPONS.index(
            weapons.WEAPON_BY_ID[CARDS[weapons.side_of(player.team)]]
        )
        player.owned = {card}
        player.held = {0, 1, card}
        spec = weapons.WEAPONS[card]
        player.ammo[card] = spec.mag
        player.reserve[card] = spec.reserve
        player.weapon = card
        player.armour = weapons.MAX_ARMOUR
        player.owned_extras = {"armour"}
        if player.team != self.state.attackers:
            player.owned_extras.add("defuser")
        player.money = 0

    def welcome_state(self, room: MatchRoom) -> dict[str, Any]:
        out = super().welcome_state(room)
        out["catalog"] = []
        out["retake"] = {"cards": CARDS}
        return out

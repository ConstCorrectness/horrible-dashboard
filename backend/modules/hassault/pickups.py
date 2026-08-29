"""Items lying on the map: what they are, where they end up, and what taking one does.

Every `.cgz` has always carried these entities — `cgz.ENTITY_NAMES` has named them
since the reader was written — and until now they were parsed and dropped on the
floor of `MatchRoom.__init__`. That had one visible consequence, recorded in
`weapons.Weapon.reserve`: the sidearm had to be bottomless, because a finite
reserve on everything with no way to refill it ends a long match with everybody
standing around empty. Items are the missing half of that trade, and the reserve
comment is now the *reason* they exist rather than an apology for their absence.

Three things here are decisions rather than ports, and they are decisions because
AssaultCube's numbers describe AssaultCube's loadout:

- **What a pickup gives.** AC splits `clips` (pistol) from `ammo` (your primary).
  We carry all five weapons at once, so "your primary" names nothing, and a
  pickup keyed to whichever gun you happen to be holding turns an item into a
  puzzle about inventory rather than a thing you run over. Ours top up *every*
  weapon with a finite reserve, and the two kinds differ only in size: `clips` is
  the small one, `ammo` the big one.

- **Armour.** AC has it; we did not, which left `helmet` and `armour` entities
  with nothing to do. Rather than leave two of the six item types inert — the
  silent no-op this codebase keeps refusing — armour is implemented here as a
  real mechanic (`weapons.ARMOUR_ABSORB`), and it is **private**, like ammo and
  unlike health: how much protection somebody is carrying is exactly the thing
  you would like to know about them before starting a fight.

- **Height.** An item's `z` is no more trustworthy than a `playerstart`'s, and for
  the same reason: the AC editor flies and stores the mapper's eye. So an item is
  resolved onto the floor beneath it with `physics._support`, the same query
  `step` resolves against — which makes an item's resting height the fixed point
  of the body that runs over it, instead of a number that floats it out of reach.

A taken item is **not removed**. It is marked unavailable until `available_at`,
which is what makes an item a piece of map control — everyone can learn when the
armour comes back, and a fight over the spot is a fight worth having.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any

from backend.modules.hassault import cgz, grenades, physics, weapons

# Entity type bytes we place. Deliberately a subset of `cgz.ENTITY_NAMES`:
# `akimbo` (9) is in every official map and we have no akimbo pistols, and
# placing it as *something else* would be a lie about what the map says.
CLIPS = 3
AMMO = 4
GRENADE = 5
HEALTH = 6
HELMET = 7
ARMOUR = 8

#: How close the body's centre has to get, horizontally, in cubes. Comfortably
#: wider than the player's own radius (~1.1): an item you have to stand exactly
#: on top of reads as broken, and every shooter since Doom has been generous here.
PICKUP_RADIUS = 1.8

#: Vertical band around the item, measured from the player's feet. Asymmetric on
#: purpose — an item is picked up by running over it, so a body *above* it may be
#: nearly a whole height up (mid-jump), while one below it has to be almost level.
PICKUP_BELOW = 1.25
PICKUP_ABOVE = 4.5


@dataclass(frozen=True, slots=True)
class ItemSpec:
    """One item kind's numbers.

    Served to the browser by `GET /api/hassault/items` for the same reason the
    weapon table is: the client draws a label and a respawn countdown, and a
    second copy of these numbers in TypeScript is a HUD that disagrees with the
    server about when the armour is back.
    """

    kind: str
    name: str
    #: Seconds between being taken and being available again.
    respawn: float
    #: Health restored, up to `weapons.MAX_HEALTH`.
    health: float = 0.0
    #: Armour restored, up to `armour_cap`.
    armour: float = 0.0
    armour_cap: float = 0.0
    #: Reserve rounds added per weapon, as a multiple of that weapon's magazine.
    #: A multiple rather than a flat count because a shotgun magazine and a rifle
    #: magazine are not the same amount of gun, and a flat 20 would be four
    #: reloads for one and half a reload for the other.
    mags: float = 0.0
    #: Grenade slot topped up, back to the number you spawn carrying.
    nade_slot: int | None = None


#: The table, keyed by the on-disk entity type.
ITEMS: dict[int, ItemSpec] = {
    CLIPS: ItemSpec(kind="clips", name="Clips", respawn=15.0, mags=1.0),
    AMMO: ItemSpec(kind="ammo", name="Ammo", respawn=20.0, mags=2.0),
    GRENADE: ItemSpec(kind="grenade", name="Grenade", respawn=25.0, nade_slot=0),
    HEALTH: ItemSpec(kind="health", name="Health", respawn=25.0, health=25.0),
    HELMET: ItemSpec(
        kind="helmet", name="Helmet", respawn=25.0, armour=25.0, armour_cap=50.0
    ),
    ARMOUR: ItemSpec(
        kind="armour",
        name="Armour",
        respawn=40.0,
        armour=50.0,
        armour_cap=weapons.MAX_ARMOUR,
    ),
}


def specs_payload() -> list[dict[str, Any]]:
    return [
        {
            "kind": spec.kind,
            "name": spec.name,
            "respawn": spec.respawn,
            "health": spec.health,
            "armour": spec.armour,
            "armourCap": spec.armour_cap,
            "mags": spec.mags,
            "nade": (
                grenades.GRENADES[spec.nade_slot].id
                if spec.nade_slot is not None
                else None
            ),
        }
        for spec in ITEMS.values()
    ]


@dataclass(slots=True)
class Item:
    """One item on the map, and whether it is currently there."""

    id: int
    kind: str
    x: float
    y: float
    z: float
    spec: ItemSpec
    #: Wall clock. Zero means available now. Wall clock rather than a player's
    #: simulated time for the same reason the respawn timer is: an item comes
    #: back whether or not anybody is sending commands.
    available_at: float = 0.0

    def available(self, now: float) -> bool:
        return now >= self.available_at

    def placement(self) -> dict[str, Any]:
        """The half that never changes: sent once, with the map."""
        return {
            "id": self.id,
            "kind": self.kind,
            "x": round(self.x, 3),
            "y": round(self.y, 3),
            "z": round(self.z, 3),
        }


def place(world: physics.World, entities: list[cgz.MapEntity]) -> list[Item]:
    """Resolve every item entity in a map onto the floor beneath it.

    Ids are the index into *this* list and are stable for the life of the room,
    which is what lets the wire refer to an item by a number instead of repeating
    its position every tick.
    """
    items: list[Item] = []
    for entity in entities:
        spec = ITEMS.get(entity.type)
        if spec is None:
            continue
        x = entity.x + 0.5
        y = entity.y + 0.5
        floor, _ceil, enclosed = _support_or_cell(world, x, y, entity)
        if enclosed:
            # Buried in rock. Officially impossible, but a community map can do
            # it, and an item nobody can reach is better dropped than left to
            # sit inside a wall drawing attention to itself.
            continue
        items.append(Item(id=len(items), kind=spec.kind, x=x, y=y, z=floor, spec=spec))
    return items


def _support_or_cell(
    world: physics.World, x: float, y: float, entity: cgz.MapEntity
) -> tuple[float, float, bool]:
    floor, ceil, enclosed = physics._support(world, x, y)
    if not enclosed:
        return floor, ceil, False
    if world.is_solid(int(entity.x), int(entity.y)):
        return 0.0, math.inf, True
    return world.floor_at(int(entity.x), int(entity.y)), math.inf, False


def in_reach(item: Item, x: float, y: float, z: float) -> bool:
    """Whether a body with its feet at `(x, y, z)` is standing on this item."""
    if math.hypot(item.x - x, item.y - y) > PICKUP_RADIUS:
        return False
    return -PICKUP_BELOW <= (z - item.z) <= PICKUP_ABOVE


@dataclass(slots=True)
class Taken:
    """What one pickup did, for the taker's own envelope.

    Reports the *applied* amounts, not the spec's: a health pack taken at 90 gave
    ten, and telling the HUD it gave twenty-five would print a number the health
    bar visibly disagrees with.
    """

    item: int
    kind: str
    health: float = 0.0
    armour: float = 0.0
    rounds: int = 0
    nade: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"item": self.item, "kind": self.kind}
        if self.health:
            out["health"] = round(self.health)
        if self.armour:
            out["armour"] = round(self.armour)
        if self.rounds:
            out["rounds"] = self.rounds
        if self.nade:
            out["nade"] = self.nade
        return out


def apply(item: Item, player: Any) -> Taken | None:
    """Give `player` what `item` holds, or `None` if it would give them nothing.

    Returning `None` is the whole reason this is a function rather than a few
    lines in the tick loop: **an item that cannot help you must not be consumed**.
    Running over the armour at full armour and taking it away from a teammate for
    forty seconds — having gained nothing — is the kind of quiet unfairness that
    makes a map feel broken without anybody being able to say why.
    """
    spec = item.spec
    taken = Taken(item=item.id, kind=item.kind)

    if spec.health:
        gain = min(spec.health, weapons.MAX_HEALTH - player.health)
        if gain > 0:
            player.health += gain
            taken.health = gain

    if spec.armour:
        gain = min(spec.armour, spec.armour_cap - player.armour)
        if gain > 0:
            player.armour += gain
            taken.armour = gain

    if spec.mags:
        for index, weapon in enumerate(weapons.WEAPONS):
            if weapon.reserve < 0:
                # Bottomless by design (the sidearm). Nothing to add, and
                # counting it would report rounds the player never received.
                continue
            held = player.reserve.get(index, 0)
            gain = min(int(round(weapon.mag * spec.mags)), weapon.reserve - held)
            if gain > 0:
                player.reserve[index] = held + gain
                taken.rounds += gain

    if spec.nade_slot is not None:
        slot = spec.nade_slot
        carried = grenades.GRENADES[slot].carried
        if player.nades.counts.get(slot, 0) < carried:
            player.nades.counts[slot] = player.nades.counts.get(slot, 0) + 1
            taken.nade = grenades.GRENADES[slot].id

    empty = not (taken.health or taken.armour or taken.rounds or taken.nade)
    return None if empty else taken


@dataclass(slots=True)
class Field:
    """Every item in one room, and the availability half of the wire.

    Kept as its own object rather than a list on `MatchRoom` because the taken
    set is the only part that changes, and the room's job is to ask two
    questions: "did this body just run over something" and "what is currently
    gone".
    """

    items: list[Item] = field(default_factory=list)

    def placements(self) -> list[dict[str, Any]]:
        return [item.placement() for item in self.items]

    def taken_ids(self, now: float | None = None) -> list[int]:
        """The unavailable ones, by id.

        Sent every tick, and cheap precisely because it is the *complement* of
        the usual state: a map with sixty items normally has a handful gone, so
        this is a few numbers rather than sixty.
        """
        moment = time.time() if now is None else now
        return [item.id for item in self.items if not item.available(moment)]

    def collect(self, player: Any, now: float | None = None) -> list[Taken]:
        """Take everything this player is standing on that has something to give."""
        moment = time.time() if now is None else now
        got: list[Taken] = []
        for item in self.items:
            if not item.available(moment):
                continue
            if not in_reach(item, player.state.x, player.state.y, player.state.z):
                continue
            result = apply(item, player)
            if result is None:
                continue
            item.available_at = moment + item.spec.respawn
            got.append(result)
        return got

    def reset(self) -> None:
        for item in self.items:
            item.available_at = 0.0

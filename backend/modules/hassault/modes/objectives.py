"""Objective geometry, resolved onto the map the way items are.

The `pickups.place` analogue, and it exists for the same reason that does: an
entity's `z` in an AssaultCube map is **the mapper's eye at placement time**, not
a ground height, and the editor flies. Resolving it here — once, at load, through
the same `physics._support` query `step` resolves against — is what makes an
objective's position the fixed point of the first frame rather than a number that
drifts against the floor under it.

Two sources, deliberately different:

- **Flags** are `ctf_flag` entities (type 13), which AssaultCube has always had
  and this repo has always parsed. `attr2` is the team, by the same convention
  `CgzMap.spawns(team)` uses for player starts. They round-trip to `.cgz`
  losslessly.
- **Bomb sites** come from the map *source* document's `objectives` block,
  because AssaultCube has no such entity and inventing one would take the next
  free `ENTITY_NAMES` byte — past `MAXENTTYPES`, so an exported `.cgz` would be
  rejected or read as garbage. See `CgzMap.objectives`.

The consequence is the guard in `check_playable`: a `.cgz`-only map has no sites,
so opening a defuse room on one must **raise** rather than open. A room where the
bomb can never be planted runs forever, expires every round timer, and says
nothing about why.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.modules.hassault import cgz, physics, pickups
from backend.modules.hassault.physics import World

#: How close you have to be to a site to plant on it, beyond its own radius.
#:
#: Reuses `pickups.in_reach` rather than a sphere test, which is not tidiness:
#: that helper carries an asymmetric vertical band (`PICKUP_BELOW` /
#: `PICKUP_ABOVE`) that exists precisely because "am I standing on this" is not a
#: distance. One implementation of that question serves items and objectives
#: alike.
SITE_REACH = 0.0


@dataclass(slots=True)
class Site:
    """A bomb site: somewhere the bomb can be planted."""

    id: str
    x: float
    y: float
    z: float
    radius: float

    def contains(self, x: float, y: float, z: float) -> bool:
        """Whether a body at this position is on the site.

        Horizontal distance against the radius, vertical against the same band
        `pickups` uses — so standing on a gantry directly above a site is not
        standing on it, which is the case a plain 3D distance gets wrong.
        """
        dx, dy = x - self.x, y - self.y
        if dx * dx + dy * dy > self.radius * self.radius:
            return False
        return -pickups.PICKUP_BELOW <= z - self.z <= pickups.PICKUP_ABOVE

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "x": round(self.x, 2),
            "y": round(self.y, 2),
            "z": round(self.z, 2),
            "radius": round(self.radius, 2),
        }


@dataclass(slots=True)
class Flag:
    """A team's flag stand, and where its flag sits when it is at home."""

    team: int
    x: float
    y: float
    z: float
    yaw: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "team": self.team,
            "x": round(self.x, 2),
            "y": round(self.y, 2),
            "z": round(self.z, 2),
            "yaw": round(self.yaw, 1),
        }


@dataclass(slots=True)
class Objectives:
    """Everything a mode needs to know about where things are on this map."""

    sites: list[Site] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)

    def flag_for(self, team: int) -> Flag | None:
        return next((f for f in self.flags if f.team == team), None)

    def site_at(self, x: float, y: float, z: float) -> Site | None:
        """The site a body at this position is standing on, if any."""
        return next((s for s in self.sites if s.contains(x, y, z)), None)


def _on_floor(world: World, x: float, y: float) -> float:
    """The height a body standing at `(x, y)` would rest at.

    The same query `physics.spawn_at` resolves a spawn against, so an objective
    and a spawn on the same tile agree about where the ground is.
    """
    return physics.spawn_at(world, _Point(x, y)).z


class _Point:
    """The three fields `physics.spawn_at` reads. Cheaper than a `MapEntity`, and
    it makes clear that only the position is being used — the `z` an entity
    carries is exactly the number being thrown away."""

    __slots__ = ("x", "y", "yaw")

    def __init__(self, x: float, y: float, yaw: float = 0.0) -> None:
        self.x = x
        self.y = y
        self.yaw = yaw


def place(world: World, cmap: cgz.CgzMap) -> Objectives:
    """Resolve this map's objectives against its world."""
    flags = [
        Flag(
            team=1 if entity.attr2 else 0,
            x=entity.x + 0.5,
            y=entity.y + 0.5,
            z=_on_floor(world, entity.x, entity.y),
            yaw=float(entity.yaw or 0.0),
        )
        for entity in cmap.entities
        if entity.type == cgz.CTF_FLAG
    ]
    sites = [
        Site(
            id=str(spec["id"]),
            x=float(spec["x"]) + 0.5,
            y=float(spec["y"]) + 0.5,
            z=_on_floor(world, float(spec["x"]), float(spec["y"])),
            radius=float(spec.get("radius", 4.0)),
        )
        for spec in (cmap.objectives or {}).get("sites", [])
    ]
    return Objectives(sites=sites, flags=flags)


def check_playable(mode_id: str, objectives: Objectives) -> None:
    """Refuse to open a room this map cannot host.

    Raises rather than degrading, because every degraded form of this is worse
    than a refusal: a defuse room with no sites runs forever with an unplantable
    bomb, and a CTF room with one flag has a team that cannot score. Both look
    like a working match from the outside and neither reports anything.
    """
    if mode_id == "defuse" and not objectives.sites:
        raise ValueError(
            "this map declares no bomb sites, so a defuse match could never end — "
            "add an `objectives.sites` block to its source"
        )
    if mode_id == "ctf":
        teams = sorted(f.team for f in objectives.flags)
        if teams != [0, 1]:
            raise ValueError(
                "capture the flag needs exactly one ctf_flag entity per team; "
                f"this map has {len(objectives.flags)}"
            )

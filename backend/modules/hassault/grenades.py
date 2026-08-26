"""Thrown utility: smoke, flashbang, incendiary and HE frag.

Before this, `weapons.TACTICALS` was a table of numbers served over
`GET /api/hassault/tacticals` with nothing behind it — no throw, no fuse, no
detonation, no effect. It was the same shape of bug the match result had before
`results.py`: a description of a feature rather than the feature. This is the
simulation.

Everything here is **server-side and authoritative**, for the same reason
`weapons.py` is: where a grenade lands decides a round, and a client that
computed its own bounce would be a client that could decide it. The browser
draws the arc it is told about and predicts nothing.

### The four, and why they are not one thing with a switch

They differ in *when* they resolve, not just in what they do, and that is why
each gets a real branch rather than a damage number:

- **HE** resolves once, on a fuse, as radial damage gated by line of sight.
- **Flashbang** resolves once, on a fuse, but the result is **per victim** — it
  depends on where each player was looking, so there is no shared "the flash went
  off this bright" value to broadcast.
- **Smoke** resolves *continuously*: it is a volume that exists for a while and
  changes what can be seen through it, including by the bots and by the radar.
- **Incendiary** resolves continuously too, but as damage over time on the
  ground rather than as an occluder.

Two of them therefore leave a **zone** behind (`Zone`), and two do not. A single
"grenade with a radius and a damage number" would have had to special-case all of
that anyway, with the branches spread across the caller instead of here.

### What is deliberately *not* here

**Prediction.** A grenade is thrown, and then it is the server's. Unlike movement
— where the client predicts because it cannot wait a round trip to know where its
own feet are — nobody is holding a grenade after it leaves the hand, so there is
nothing to feel laggy. The client interpolates the projectile from snapshots like
any other moving thing.

**Bullet blocking by smoke.** Smoke stops vision, not rounds, which is what makes
spraying into a smoke a real (and punished) decision rather than a free wall.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable

from backend.modules.hassault.physics import GRAVITY, World

#: How far below the eye a grenade leaves the hand, and how far in front. Thrown
#: from in front of the face rather than from the eye itself: a grenade released
#: exactly at the eye clips the thrower's own body on the first substep when they
#: are backed against a wall, and detonates in their face for reasons invisible
#: from the screen.
THROW_FORWARD = 1.3
THROW_DROP = 0.35

#: Speed of a normal throw, in cubes per second. Tuned against this game's own
#: gravity (55) rather than copied: at this speed a flat throw travels about 30
#: cubes before it has fallen a body height, which is a room and a half.
THROW_SPEED = 34.0

#: Fraction of `THROW_SPEED` an underhand lob leaves at. The short throw is what
#: makes a smoke placeable at your own feet, and it is one flag rather than a
#: charge meter because a held button that quietly changes a throw is the kind of
#: mechanic nobody discovers.
LOB_SCALE = 0.42

#: The thrower's own motion added to the throw. Running forward and throwing sends
#: it further, which is both correct and the thing every player expects.
THROW_INHERIT = 0.6

#: How small a bounce has to get before the grenade is treated as settled. Without
#: it a grenade jitters against the floor forever, and a smoke that never comes to
#: rest is a smoke whose cloud centre keeps moving.
REST_SPEED = 1.2

#: Simulation substep. Bounces are resolved by sampling, so this bounds how far a
#: grenade can travel inside one test — at 34 cubes/s a 1/120 s step is 0.28 of a
#: cube, comfortably less than the cell it is checking against.
SUBSTEP = 1.0 / 120.0

#: A cap on substeps per tick, so a pathological `dt` cannot spin here.
MAX_SUBSTEPS = 64


@dataclass(frozen=True, slots=True)
class GrenadeSpec:
    """One kind of thrown utility.

    Served to the client (`GET /api/hassault/tacticals`) rather than duplicated
    in TypeScript, the `interval` / `zoom_levels` / `plane_order` precedent: the
    HUD needs the carry count and the name, the renderer needs the radius to draw
    a cloud the right size, and a second copy of those is a smoke that is drawn a
    different size from the one that is actually blocking sight.
    """

    id: str
    name: str
    kind: str
    """Seconds from leaving the hand to detonation."""
    fuse: float
    """Detonate on the first solid contact instead of on the fuse.

    The incendiary's defining property: it is aimed at a *place*, so a throw that
    lands right is not then spoiled by rolling somewhere else. Everything else
    cooks on a timer, which is what makes bouncing one round a corner work.
    """
    impact: bool
    """Radius of the effect in cubes — damage for HE, cloud for smoke and fire."""
    radius: float
    """How long the zone lasts. Zero for the two that resolve instantly."""
    duration: float
    """Peak damage at the centre, before falloff."""
    damage: float
    """Damage per second inside a fire, for the one that burns."""
    damage_per_second: float
    """Fraction of speed kept across a bounce."""
    bounce: float
    """Fraction of horizontal speed kept per second while rolling on the ground."""
    friction: float
    """How many you spawn with."""
    carried: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.kind,
            "fuseTime": self.fuse,
            "impact": self.impact,
            "radius": self.radius,
            "duration": self.duration,
            "maxDamage": self.damage,
            "damagePerSecond": self.damage_per_second,
            "bounceDamping": self.bounce,
            "carried": self.carried,
        }


#: The loadout, in slot order. Tuned against this game's movement speed and map
#: scale rather than copied from anywhere: a cube is about 36 cm, so a 7-cube
#: smoke is roughly two and a half metres of cover and an 8-cube HE kills what is
#: standing next to it and hurts what is in the same room.
GRENADES: tuple[GrenadeSpec, ...] = (
    GrenadeSpec(
        id="he",
        name="HE Grenade",
        kind="he",
        fuse=1.9,
        impact=False,
        radius=9.0,
        duration=0.0,
        damage=98.0,
        damage_per_second=0.0,
        bounce=0.45,
        friction=3.2,
        carried=1,
    ),
    GrenadeSpec(
        id="flash",
        name="Flashbang",
        kind="flash",
        fuse=1.6,
        impact=False,
        radius=26.0,
        duration=0.0,
        damage=0.0,
        damage_per_second=0.0,
        bounce=0.5,
        friction=3.0,
        carried=2,
    ),
    GrenadeSpec(
        id="smoke",
        name="Smoke Grenade",
        kind="smoke",
        fuse=1.7,
        impact=False,
        radius=7.5,
        duration=15.0,
        damage=0.0,
        damage_per_second=0.0,
        bounce=0.35,
        friction=5.5,
        carried=1,
    ),
    GrenadeSpec(
        id="molotov",
        name="Incendiary",
        kind="fire",
        # Never reached — `impact` ends it first — but not zero, because a
        # molotov that somehow fails to touch anything (thrown off the top of the
        # map) must still resolve rather than fall forever.
        fuse=6.0,
        impact=True,
        radius=6.0,
        duration=8.0,
        damage=0.0,
        damage_per_second=26.0,
        bounce=0.0,
        friction=99.0,
        carried=1,
    ),
)

BY_ID: dict[str, GrenadeSpec] = {g.id: g for g in GRENADES}


def spec_at(slot: int) -> GrenadeSpec | None:
    """The grenade in a slot, or `None`. Out of range is a miss, not an error:
    the slot comes off the wire."""
    if 0 <= slot < len(GRENADES):
        return GRENADES[slot]
    return None


@dataclass(slots=True)
class Grenade:
    """One thrown object in flight."""

    id: str
    spec: GrenadeSpec
    owner: str
    team: int
    x: float
    y: float
    z: float
    vx: float
    vy: float
    vz: float
    """Seconds of fuse left. Counted down rather than compared against a wall
    clock, so a grenade advances on the room's own simulated time like everything
    else in the tick."""
    fuse: float
    resting: bool = False
    """Set when the fuse runs out or an impact ends it. The room collects these
    after stepping rather than detonating mid-iteration — mutating the list you
    are walking is how one grenade in a cluster silently goes missing."""
    detonated: bool = False

    def snapshot(self) -> dict[str, Any]:
        """The wire form. Public: a grenade in the air is visible to everyone, and
        hiding one you can see on screen would be worse than useless."""
        return {
            "id": self.id,
            "kind": self.spec.kind,
            "owner": self.owner,
            "team": self.team,
            "x": round(self.x, 2),
            "y": round(self.y, 2),
            "z": round(self.z, 2),
            "fuse": round(self.fuse, 2),
        }


@dataclass(slots=True)
class Zone:
    """A smoke cloud or a patch of fire: an effect that persists in a place.

    Kept separate from `Grenade` because it is a different thing with a different
    lifetime — the grenade is gone by the time the zone exists, and the zone has
    no velocity, no fuse and no owner-relative behaviour except who to blame for
    the burns.
    """

    id: str
    kind: str
    owner: str
    team: int
    x: float
    y: float
    z: float
    radius: float
    """Seconds left. Same reason as `Grenade.fuse`: the room's clock, not the
    wall's."""
    remaining: float
    duration: float
    damage_per_second: float

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "x": round(self.x, 2),
            "y": round(self.y, 2),
            "z": round(self.z, 2),
            "r": round(self.radius, 2),
            "left": round(self.remaining, 2),
            "duration": round(self.duration, 2),
        }

    def contains(self, x: float, y: float, z: float) -> bool:
        return (
            (x - self.x) ** 2 + (y - self.y) ** 2 + (z - self.z) ** 2
        ) <= self.radius**2


def throw_velocity(
    yaw: float,
    pitch: float,
    lob: bool,
    inherit: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> tuple[float, float, float]:
    """The velocity a grenade leaves the hand with.

    Yaw/pitch use the same convention as `weapons.aim_vector`, so a grenade goes
    where the crosshair is pointing. The thrower's own velocity is added at a
    fraction: at 1.0 a player running backwards can drop a grenade that never
    leaves them, which reads as the throw having failed.
    """
    speed = THROW_SPEED * (LOB_SCALE if lob else 1.0)
    cp = math.cos(pitch)
    dx = math.cos(yaw) * cp
    dy = math.sin(yaw) * cp
    dz = math.sin(pitch)
    return (
        dx * speed + inherit[0] * THROW_INHERIT,
        dy * speed + inherit[1] * THROW_INHERIT,
        dz * speed + inherit[2] * THROW_INHERIT,
    )


def throw_origin(
    x: float, y: float, eye_z: float, yaw: float, pitch: float
) -> tuple[float, float, float]:
    """Where the grenade appears, in front of and below the eye."""
    cp = math.cos(pitch)
    return (
        x + math.cos(yaw) * cp * THROW_FORWARD,
        y + math.sin(yaw) * cp * THROW_FORWARD,
        eye_z - THROW_DROP + math.sin(pitch) * THROW_FORWARD,
    )


def _blocked(world: World, x: float, y: float, z: float) -> bool:
    """Whether a point is inside the level's geometry.

    The same three questions `raycast_world` asks of a cell, which is deliberate:
    a grenade must come to rest on the surfaces a bullet stops at, or it will roll
    into somewhere a shot cannot reach.
    """
    cx, cy = math.floor(x), math.floor(y)
    if world.is_solid(cx, cy):
        return True
    return z < world.floor_at(cx, cy) or z > world.ceil_at(cx, cy)


def step_grenade(world: World, nade: Grenade, dt: float) -> bool:
    """Advance one grenade. Returns whether it hit something this step.

    Axis-separated, like the player's movement resolve and for the same reason:
    resolving a diagonal contact as one event has to pick an axis to reflect
    anyway, and picking the wrong one sends a grenade along a wall it should have
    bounced off. Each axis is tested and reflected on its own, so a corner is two
    bounces rather than a guess.
    """
    spec = nade.spec
    hit = False
    remaining = dt
    steps = 0
    while remaining > 1e-6 and steps < MAX_SUBSTEPS:
        steps += 1
        h = min(SUBSTEP, remaining)
        remaining -= h

        nade.vz -= GRAVITY * h

        for axis in ("x", "y", "z"):
            if axis == "x":
                nx, ny, nz = nade.x + nade.vx * h, nade.y, nade.z
            elif axis == "y":
                nx, ny, nz = nade.x, nade.y + nade.vy * h, nade.z
            else:
                nx, ny, nz = nade.x, nade.y, nade.z + nade.vz * h

            if _blocked(world, nx, ny, nz):
                hit = True
                if axis == "x":
                    nade.vx = -nade.vx * spec.bounce
                elif axis == "y":
                    nade.vy = -nade.vy * spec.bounce
                else:
                    # Landing on the floor is also where rolling starts, so the
                    # horizontal speed is bled here rather than in a separate
                    # "is it on the ground" branch that would need its own test.
                    grounded = nade.vz < 0
                    nade.vz = -nade.vz * spec.bounce
                    if grounded:
                        decay = max(0.0, 1.0 - spec.friction * h)
                        nade.vx *= decay
                        nade.vy *= decay
                        if abs(nade.vz) < REST_SPEED:
                            nade.vz = 0.0
                            nade.resting = abs(nade.vx) + abs(nade.vy) < REST_SPEED
            else:
                nade.x, nade.y, nade.z = nx, ny, nz

    nade.fuse -= dt
    return hit


def _falloff(distance: float, radius: float) -> float:
    """Damage scale at a distance. Linear to zero at the radius.

    Linear rather than inverse-square: inverse-square puts almost all of a
    grenade's damage in the first cube and makes the rest of its radius a rumour,
    which is exactly the grenade nobody can learn to use.
    """
    if distance >= radius:
        return 0.0
    return 1.0 - distance / radius


@dataclass(slots=True)
class BlastHit:
    victim: str
    damage: float
    """How much of the blast reached them, 0..1 — for the client's shake."""
    strength: float


def resolve_blast(
    world: World,
    nade: Grenade,
    targets: dict[str, tuple[float, float, float]],
    los: bool = True,
) -> list[BlastHit]:
    """Who an HE hurt, and by how much.

    **Line of sight is checked, not assumed.** A grenade on the other side of a
    wall from you is the single most common thing a radius test gets wrong, and
    getting it wrong means taking 90 damage through a floor. The ray is traced
    from the grenade to the body, and anything in the way stops it entirely
    rather than attenuating: the wall is either between you or it is not, and a
    partial credit model here would mean tuning a number nobody can observe.
    """
    hits: list[BlastHit] = []
    for victim, (tx, ty, tz) in targets.items():
        dx, dy, dz = tx - nade.x, ty - nade.y, tz - nade.z
        distance = math.sqrt(dx * dx + dy * dy + dz * dz)
        scale = _falloff(distance, nade.spec.radius)
        if scale <= 0:
            continue
        if (
            los
            and distance > 1e-4
            and not visible(world, (nade.x, nade.y, nade.z), (tx, ty, tz))
        ):
            continue
        hits.append(
            BlastHit(
                victim=victim,
                damage=nade.spec.damage * scale,
                strength=scale,
            )
        )
    return hits


def visible(
    world: World,
    origin: tuple[float, float, float],
    target: tuple[float, float, float],
) -> bool:
    """Whether the level's geometry leaves these two points connected.

    Thin wrapper over the shot raycast, which is the point: a grenade, a bot's
    eye and the radar all have to agree with a bullet about what a wall is.
    """
    from backend.modules.hassault.weapons import raycast_world

    dx = target[0] - origin[0]
    dy = target[1] - origin[1]
    dz = target[2] - origin[2]
    distance = math.sqrt(dx * dx + dy * dy + dz * dz)
    if distance < 1e-6:
        return True
    direction = (dx / distance, dy / distance, dz / distance)
    # A hair short of the target: a ray that reaches exactly the surface a body
    # is standing against reports a hit on it.
    return raycast_world(world, origin, direction, distance) >= distance - 0.05


#: How long a full flash lasts, in seconds, at the very centre of the effect
#: looking straight at it. Everything else is a fraction of it.
FLASH_MAX = 4.2

#: Fraction of `FLASH_MAX` someone gets with the flash directly behind them.
#: Not zero: a bang at your heels is disorienting even when it is not blinding,
#: and a hard zero makes turning away a perfect counter rather than a good one.
FLASH_BEHIND = 0.12


def flash_strength(
    world: World,
    nade: Grenade,
    x: float,
    y: float,
    eye_z: float,
    yaw: float,
    pitch: float,
) -> float:
    """How blind a flashbang leaves one player, 0..1.

    Resolved **per victim on the server**, which is the only place it can be
    resolved correctly: it depends on where that player was looking, on whether a
    wall was between them, and on how far away they were. Broadcasting "a flash
    went off at (x, y)" and letting each client decide how blind it is would make
    not being blinded a client-side setting.

    Three terms, and each is a real counter a player can use:

    - **Distance.** Falls off to nothing at the spec's radius.
    - **Facing.** Looking away is the counter everybody learns first, and it is
      the dot product between the view direction and the direction to the bang.
    - **Line of sight.** A wall between you and it stops the light, because it is
      light.
    """
    dx, dy, dz = nade.x - x, nade.y - y, nade.z - eye_z
    distance = math.sqrt(dx * dx + dy * dy + dz * dz)
    reach = _falloff(distance, nade.spec.radius)
    if reach <= 0:
        return 0.0
    if not visible(world, (x, y, eye_z), (nade.x, nade.y, nade.z)):
        return 0.0
    if distance < 1e-4:
        return 1.0

    cp = math.cos(pitch)
    view = (math.cos(yaw) * cp, math.sin(yaw) * cp, math.sin(pitch))
    towards = (dx / distance, dy / distance, dz / distance)
    dot = view[0] * towards[0] + view[1] * towards[1] + view[2] * towards[2]
    # -1 (behind) → FLASH_BEHIND, +1 (straight at it) → 1.
    facing = FLASH_BEHIND + (1.0 - FLASH_BEHIND) * (dot + 1.0) / 2.0
    return max(0.0, min(1.0, reach * facing))


def sight_blocked_by(
    zones: Iterable[Zone], a: tuple[float, float, float], b: tuple[float, float, float]
) -> bool:
    """Whether a smoke lies across the line between two points.

    This is what makes a smoke a smoke rather than a decal, and it has to be
    asked by everything that decides what can be seen — the bots' targeting and
    the radar both — or the cloud will be something only humans have to respect.

    A segment/sphere test rather than sampling: sampling along the line misses a
    cloud thinner than the sample spacing, which is precisely the grazing shot
    somebody will complain about.
    """
    ax, ay, az = a
    bx, by, bz = b
    dx, dy, dz = bx - ax, by - ay, bz - az
    length_sq = dx * dx + dy * dy + dz * dz
    if length_sq < 1e-9:
        return any(z.kind == "smoke" and z.contains(ax, ay, az) for z in zones)
    for zone in zones:
        if zone.kind != "smoke":
            continue
        # Closest point on the segment to the cloud's centre, clamped to the
        # segment so a cloud *behind* the viewer never blocks anything.
        t = ((zone.x - ax) * dx + (zone.y - ay) * dy + (zone.z - az) * dz) / length_sq
        t = max(0.0, min(1.0, t))
        cx, cy, cz = ax + dx * t, ay + dy * t, az + dz * t
        if (cx - zone.x) ** 2 + (cy - zone.y) ** 2 + (
            cz - zone.z
        ) ** 2 <= zone.radius**2:
            return True
    return False


@dataclass(slots=True)
class Inventory:
    """What one player is carrying, per slot.

    A dict rather than a list because it is keyed by the same slot index the wire
    carries, and because `reset` has to restore exactly the spawn loadout rather
    than whatever the last round left.
    """

    counts: dict[int, int] = field(default_factory=dict)

    def reset(self) -> None:
        self.counts = {i: g.carried for i, g in enumerate(GRENADES)}

    def has(self, slot: int) -> bool:
        return self.counts.get(slot, 0) > 0

    def take(self, slot: int) -> bool:
        if not self.has(slot):
            return False
        self.counts[slot] -= 1
        return True

    def to_dict(self) -> dict[str, int]:
        """Keyed by grenade id rather than slot for the HUD, which shows names."""
        return {
            GRENADES[i].id: n for i, n in self.counts.items() if 0 <= i < len(GRENADES)
        }

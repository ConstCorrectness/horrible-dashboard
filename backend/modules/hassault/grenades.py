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
import time
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
        radius=11.5,
        duration=0.0,
        damage=140.0,
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
        radius=34.0,
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
        radius=10.5,
        duration=18.0,
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
        radius=8.5,
        duration=9.5,
        damage=0.0,
        damage_per_second=52.0,
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
    cleared_until: float = 0.0
    channels: list[tuple[float, float, float, float, float]] = field(
        default_factory=list
    )
    volumetric_nodes: list[dict[str, float]] = field(default_factory=list)

    def snapshot(self, now: float | None = None) -> dict[str, Any]:
        """The wire form.

        Every time in it is **seconds remaining**, never a moment. The room's
        clock is `time.monotonic()` — seconds since this machine booted — and a
        client has no way to read it: `cleared` used to go out as that absolute
        value, the browser compared it with `Date.now()`, and an HE-cleared smoke
        stayed opaque on screen while the server let everybody see through it.
        """
        if now is None:
            now = time.monotonic()
        data = {
            "id": self.id,
            "kind": self.kind,
            "x": round(self.x, 2),
            "y": round(self.y, 2),
            "z": round(self.z, 2),
            "r": round(self.radius, 2),
            "left": round(self.remaining, 2),
            "duration": round(self.duration, 2),
        }
        cleared_for = self.cleared_until - now
        if cleared_for > 0.0:
            data["clearedFor"] = round(cleared_for, 2)
        if self.volumetric_nodes:
            data["nodes"] = self.volumetric_nodes
        active_channels = [c for c in self.channels if c[4] > now]
        if active_channels:
            data["channels"] = [
                [
                    round(c[0], 2),
                    round(c[1], 2),
                    round(c[2], 2),
                    # `c[4]` is stored as an absolute (monotonic) expiry, not a
                    # duration — the wire's `remainingSec` field must be the
                    # time still left, not the moment it runs out.
                    round(max(0.0, c[4] - now), 2),
                    # The tunnel's radius in cubes, so the hole a client draws
                    # is the hole `sight_blocked_by` lets a bot see through.
                    round(c[3], 2),
                ]
                for c in active_channels[-8:]
            ]
        return data

    def spheres(self) -> list[tuple[float, float, float, float]]:
        """The cloud's shape as (x, y, z, r) balls: the main one and its lobes.

        **The one shape every reader asks about** — `contains`,
        `sight_blocked_by` and both clients' renderers. They used to disagree
        (the lobes were in `contains` only), which meant the edge of a smoke that
        stopped a flash was not the edge that stopped a bot.
        """
        balls = [(self.x, self.y, self.z, self.radius)]
        for node in self.volumetric_nodes:
            balls.append((node["x"], node["y"], node["z"], node["r"]))
        return balls

    def contains(self, x: float, y: float, z: float) -> bool:
        return any(
            (x - bx) ** 2 + (y - by) ** 2 + (z - bz) ** 2 <= br**2
            for bx, by, bz, br in self.spheres()
        )


def generate_volumetric_smoke_nodes(
    world: Any, x: float, y: float, z: float, radius: float = 4.2
) -> list[dict[str, float]]:
    """Compute volumetric sub-nodes expanding outwards from detonation point,
    wrapping around corners and stopping at solid geometry walls.
    """
    nodes = [
        {
            "x": round(x, 2),
            "y": round(y, 2),
            "z": round(z, 2),
            "r": round(radius * 0.65, 2),
        }
    ]
    angles = [i * (math.pi / 4.0) for i in range(8)]
    offset_dist = radius * 0.55
    for ang in angles:
        nx = x + math.cos(ang) * offset_dist
        ny = y + math.sin(ang) * offset_dist
        blocked = False
        if world is not None and hasattr(world, "solid"):
            try:
                if world.solid(int(nx), int(ny), int(z)):
                    blocked = True
            except Exception:
                pass
        if not blocked:
            nodes.append(
                {
                    "x": round(nx, 2),
                    "y": round(ny, 2),
                    "z": round(z, 2),
                    "r": round(radius * 0.5, 2),
                }
            )
    return nodes


def throw_velocity(
    yaw: float,
    pitch: float,
    lob: bool = False,
    inherit: tuple[float, float, float] = (0.0, 0.0, 0.0),
    power: float | None = None,
) -> tuple[float, float, float]:
    """The velocity a grenade leaves the hand with.

    Yaw/pitch use the same convention as `weapons.aim_vector`, so a grenade goes
    where the crosshair is pointing. The thrower's own velocity is added at a
    fraction: at 1.0 a player running backwards can drop a grenade that never
    leaves them, which reads as the throw having failed.
    """
    scale = power if power is not None else (LOB_SCALE if lob else 1.0)
    speed = THROW_SPEED * max(0.2, min(1.5, scale))
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


#: Seconds of solid white for a flash at your feet with your crosshair on it.
#: Counter-Strike's measured figure for a full flash is about 1.9 s of hold; this
#: is a little longer because the radius here is shorter, so a full hold is rarer.
FLASH_HOLD_MAX = 2.2

#: Seconds the white then takes to clear, after the hold. The fade is where
#: the afterimage lives, and where most of a flash's real cost is paid: you can
#: see shapes again before you can see a player.
FLASH_FADE_MAX = 3.0

#: The longest a flash can take from bang to clear. What a test that waits for
#: one to wear off waits for.
FLASH_MAX = FLASH_HOLD_MAX + FLASH_FADE_MAX

#: The facing bands, by angle off the crosshair in degrees: (widest angle, peak
#: whiteness, fraction of the hold, fraction of the fade).
#:
#: **Bands, not a smooth function of the angle**, because that is the counter
#: players learn: "turn away" has to mean something sharp. The old linear dot
#: product gave a flash at 90° five eighths of a frontal one and a flash directly
#: behind a quarter — turning round halved it, which is a nuisance rather than a
#: counter. These are Counter-Strike's: the screen's own width (~53° off centre
#: at 16:9) is a full flash, past the edge of the screen is a flicker, and behind
#: you is a jolt that never reaches white.
FLASH_BANDS: tuple[tuple[float, float, float, float], ...] = (
    (53.0, 1.0, 1.0, 1.0),
    (72.0, 1.0, 0.45, 0.8),
    (101.0, 0.9, 0.04, 0.45),
    (180.0, 0.5, 0.0, 0.25),
)

#: Above this much white a player sees nothing: a bot cannot acquire a target
#: and an ally paints nothing on the radar. The same threshold for both, because
#: a radar fed by a blind teammate is a wall hack with a delay.
FLASH_BLIND = 0.6

#: Below this a flash is a tint, not a handicap: no aim penalty for a bot.
FLASH_DAZED = 0.15

#: Within this many cubes the bang is too close to turn away from: the band is
#: never worse than the 53–72° one. Light that bright wraps round your head.
FLASH_POINT_BLANK = 3.0

#: Where the light is sampled from, around the bang, in cubes. Five rays rather
#: than one so a flash half round a corner is half a flash: a single ray to the
#: grenade's centre turns a lip of floor between you and it into a hard zero.
FLASH_PROBES: tuple[tuple[float, float, float], ...] = (
    (0.0, 0.0, 0.0),
    (0.0, 0.0, 0.6),
    (0.45, 0.0, 0.25),
    (-0.45, 0.0, 0.25),
    (0.0, 0.45, 0.25),
    (0.0, -0.45, 0.25),
)


@dataclass(frozen=True, slots=True)
class FlashEffect:
    """What one flashbang did to one player.

    **Three numbers, not one.** The old model had a single 0..1 strength that set
    both how white the screen went (its square) and how long it lasted, so a flash
    at medium range was a long pale wash that never actually blinded anybody. How
    white and for how long are separate questions with separate counters — facing
    sets the first, distance mostly the second — so they are separate fields.
    """

    #: How white the screen gets at the effect's height, 0..1.
    peak: float
    #: Seconds held at `peak` before the fade begins.
    hold: float
    #: Seconds from `peak` to clear.
    fade: float

    @property
    def dose(self) -> float:
        """A single comparable size, for deciding whether a second flash
        replaces the first. The fade counts half: it is half as blinding."""
        return self.peak * (self.hold + 0.5 * self.fade)


NO_FLASH = FlashEffect(0.0, 0.0, 0.0)


def flash_alpha(
    peak: float, hold_left: float, fade_left: float, fade_total: float
) -> float:
    """How white the screen is right now, 0..1.

    **The one curve every client draws**, implemented three times (here,
    `flashbang.ts` and `flashbang.rs`) and pinned by `flash-vectors.json`. The
    server sends the three remaining durations and each client runs this at its
    own frame rate, so the fade is smooth whatever the tick rate — and nobody
    squares anything on the way in, because the shaping is in here.

    Held flat at `peak`, then a smoothstep down: bright for most of the fade's
    first half, then clearing quickly, so the tail is not a long pale wash.
    """
    if peak <= 0.0:
        return 0.0
    if hold_left > 0.0:
        return min(1.0, peak)
    if fade_left <= 0.0 or fade_total <= 0.0:
        return 0.0
    t = max(0.0, min(1.0, fade_left / fade_total))
    return min(1.0, peak * t * t * (3.0 - 2.0 * t))


def _flash_band(angle_deg: float, distance: float) -> tuple[float, float, float]:
    """(peak, hold fraction, fade fraction) for an angle off the crosshair."""
    floor = 1 if distance < FLASH_POINT_BLANK else len(FLASH_BANDS) - 1
    for i, (widest, peak, hold, fade) in enumerate(FLASH_BANDS):
        if angle_deg <= widest or i >= floor:
            return peak, hold, fade
    _, peak, hold, fade = FLASH_BANDS[-1]
    return peak, hold, fade


def flash_light_fraction(
    world: World,
    nade: Grenade,
    eye: tuple[float, float, float],
    zones: Iterable[Zone] = (),
    now: float = 0.0,
) -> float:
    """How much of the bang's light reaches an eye, 0..1.

    The fraction of `FLASH_PROBES` rays that arrive. **A smoke between the two
    stops it** — a cloud dense enough to hide a player is dense enough to hide a
    flash, which is what makes "smoke, then flash through it" a mistake rather
    than a combo. The exception is a cloud the eye is *inside*: you are in the
    light's room then, and the smoke is around you rather than between.
    """
    blocking = [z for z in zones if not z.contains(*eye)]
    if sight_blocked_by(blocking, eye, (nade.x, nade.y, nade.z), now):
        return 0.0
    arrived = 0
    for ox, oy, oz in FLASH_PROBES:
        if visible(world, eye, (nade.x + ox, nade.y + oy, nade.z + oz)):
            arrived += 1
    return arrived / len(FLASH_PROBES)


def flash_effect(
    world: World,
    nade: Grenade,
    x: float,
    y: float,
    eye_z: float,
    yaw: float,
    pitch: float,
    zones: Iterable[Zone] = (),
    now: float = 0.0,
) -> FlashEffect:
    """What a flashbang does to one player.

    Resolved **per victim on the server**, which is the only place it can be
    resolved correctly: it depends on where that player was looking, on whether a
    wall or a smoke was between them, and on how far away they were. Broadcasting
    "a flash went off at (x, y)" and letting each client decide how blind it is
    would make not being blinded a client-side setting.

    The terms, each a counter a player can use:

    - **Distance** shortens it, linearly to nothing at the spec's radius. It
      shortens the hold and the fade; it does not make a frontal flash grey.
    - **Facing** picks a band (`FLASH_BANDS`): how white, and how much of the
      hold and fade.
    - **Line of sight**, sampled from several points round the bang
      (`flash_light_fraction`), scales both durations.
    """
    eye = (x, y, eye_z)
    dx, dy, dz = nade.x - x, nade.y - y, nade.z - eye_z
    distance = math.sqrt(dx * dx + dy * dy + dz * dz)
    reach = _falloff(distance, nade.spec.radius)
    if reach <= 0:
        return NO_FLASH
    light = flash_light_fraction(world, nade, eye, zones, now)
    if light <= 0:
        return NO_FLASH

    if distance < 1e-4:
        angle = 0.0
    else:
        cp = math.cos(pitch)
        view = (math.cos(yaw) * cp, math.sin(yaw) * cp, math.sin(pitch))
        dot = (view[0] * dx + view[1] * dy + view[2] * dz) / distance
        angle = math.degrees(math.acos(max(-1.0, min(1.0, dot))))
    peak, hold, fade = _flash_band(angle, distance)
    scale = reach * light
    effect = FlashEffect(
        peak=peak,
        hold=FLASH_HOLD_MAX * hold * scale,
        fade=FLASH_FADE_MAX * fade * scale,
    )
    if effect.hold + effect.fade < 0.05:
        return NO_FLASH
    return effect


def sight_blocked_by(
    zones: Iterable[Zone],
    a: tuple[float, float, float],
    b: tuple[float, float, float],
    now: float = 0.0,
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
        return any(
            z.kind == "smoke" and z.cleared_until <= now and z.contains(ax, ay, az)
            for z in zones
        )
    for zone in zones:
        if zone.kind != "smoke":
            continue
        if zone.cleared_until > now:
            continue
        crossed = False
        for sx, sy, sz, sr in zone.spheres():
            # Closest point on the segment to the ball's centre, clamped to the
            # segment so a cloud *behind* the viewer never blocks anything.
            t = ((sx - ax) * dx + (sy - ay) * dy + (sz - az) * dz) / length_sq
            t = max(0.0, min(1.0, t))
            cx, cy, cz = ax + dx * t, ay + dy * t, az + dz * t
            if (cx - sx) ** 2 + (cy - sy) ** 2 + (cz - sz) ** 2 <= sr**2:
                crossed = True
                break
        if crossed:
            # Bullet smoke channel check: if the ray passes through an active bullet hole/channel
            # punched through the smoke cloud, visibility is temporarily unblocked along that tunnel.
            if zone.channels:
                in_channel = False
                for ch in zone.channels:
                    chx, chy, chz, ch_r, ch_exp = ch
                    if ch_exp > now:
                        tch = max(
                            0.0,
                            min(
                                1.0,
                                ((chx - ax) * dx + (chy - ay) * dy + (chz - az) * dz)
                                / length_sq,
                            ),
                        )
                        px, py, pz = ax + dx * tch, ay + dy * tch, az + dz * tch
                        if (px - chx) ** 2 + (py - chy) ** 2 + (
                            pz - chz
                        ) ** 2 <= ch_r**2:
                            in_channel = True
                            break
                if in_channel:
                    continue
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

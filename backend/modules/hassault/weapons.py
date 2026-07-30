"""Weapons, hit registration, and lag compensation.

Everything here is server-side and authoritative. The client draws a muzzle flash
and kicks its own crosshair the instant you press the button — that is what makes
a gun feel like a gun — but *whether you hit anything* is decided here, from the
server's own copy of the world and its own record of where everybody was.

### Why a rewind is not optional

A shooter aims at what their screen is showing, and their screen is showing the
past twice over: half a round trip of network delay, plus the ~100 ms of
deliberate interpolation delay the renderer adds so remote players move smoothly
(`INTERP_DELAY_MS` in `net.ts`). On a 60 ms link that is ~130 ms. In 130 ms a
sprinting player covers nearly three cubes — more than their own width. Testing
the shot against *current* positions would mean you have to lead every target by
a body width, which reads as "this game does not register hits".

So the room keeps a short **history** of where everyone was, and a shot is
resolved against the world as the shooter saw it. The client already computes
that instant — `SnapshotBuffer.renderTime` is literally "the server-clock time I
am drawing right now" — so it stamps each shot with it and the server rewinds to
match, rather than the server guessing from a latency estimate it measured on a
different packet.

That means the rewind target is client-supplied, which makes the **clamp** the
security boundary and not a tidiness measure: `view_t` is pinned into
`[now - MAX_REWIND_MS, now]`. Without it a client could ask to be judged against
a position from ten seconds ago and shoot people where they used to be standing.

The cost of lag compensation is the one everybody pays: occasionally you take
damage after stepping behind a wall, because on the shooter's screen you had not
reached it yet. That is the honest trade — one of the two players has to be
wrong, and it should be the one who did not have to aim.

See docs/modules/hassault.mdx.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any

from backend.modules.hassault.physics import (
    PLAYER_ABOVE_EYE,
    PLAYER_EYE_HEIGHT,
    PLAYER_RADIUS,
    World,
)

# Total body height. The same figure the collision code reserves headroom for and
# the same one the avatar capsule is built to, so what you see is what you hit.
BODY_HEIGHT = PLAYER_EYE_HEIGHT + PLAYER_ABOVE_EYE

# The top band of the body that counts as a head. Roughly the top of the capsule
# rather than a separate sphere: a second collision volume would have to be
# replicated in the client's avatar to stay honest, and this does not.
#
# A *band* rather than an absolute height, because crouching shortens the body and
# a head band pinned to a standing figure would sit above a crouched player
# entirely — making them unheadshottable, which is not a crouch bonus anyone asked
# for. `HEAD_Z` is kept as the standing case for the tests that name it.
HEAD_BAND = 1.0
HEAD_Z = BODY_HEIGHT - HEAD_BAND

# How far back a shot may be judged. Generous enough for a guest playing across
# the fabric (browser → their backend → peer → host is two hops each way), tight
# enough that "shot behind a wall" stays a fifth of a second rather than a
# grudge. Beyond this the shooter simply has to lead.
MAX_REWIND_MS = 500.0

# Position history kept per room. Twice the rewind cap so an interpolated rewind
# always has a frame on each side of its target.
HISTORY_SECONDS = 1.0

MAX_HEALTH = 100
RESPAWN_DELAY = 3.0

# Invulnerability granted on spawning, and dropped the moment you fire. Without
# it, spawn points on small maps are a lottery; without the drop, it is a shield
# you can attack from.
SPAWN_PROTECT = 1.5

# Recoil push while crouched, from AC's `attackphysics`. A braced shot moves you
# less — which makes crouching the accurate option *and* the stable one, two
# incentives pointing the same way instead of a dial to balance.
CROUCH_KICK_SCALE = 0.75


@dataclass(frozen=True, slots=True)
class Weapon:
    """One weapon's numbers.

    Served to the browser by `GET /api/hassault/weapons` rather than duplicated
    in TypeScript. The client needs the fire interval (so it does not send input
    the server will only throw away), the magazine size and the name — and a
    second copy of those constants is a drift trap for no gain, the same reason
    `plane_order` is reported rather than hardcoded.
    """

    id: str
    name: str
    damage: float
    """Multiplier applied to a hit above `HEAD_Z`."""
    head_multiplier: float
    """Rounds per minute. The server enforces it; the client only avoids spamming."""
    rpm: float
    mag: int
    """Rounds held in reserve; `-1` is unlimited.

    There are no ammo pickups on the map yet — the entities are parsed but not
    placed — so a finite reserve on *everything* would end a long match with
    everybody standing around empty. The sidearm is therefore bottomless and the
    rest are not, which keeps ammo a real resource without letting a match reach
    a state it cannot leave. Dying refills you, which is its own small mercy.
    """
    reserve: int
    reload_time: float
    """Cone half-angle in radians, applied per pellet."""
    spread: float
    pellets: int
    """Beyond this the shot simply stops; nothing is hit past it."""
    range: float
    """Distance at which damage starts tapering, down to half at `range`."""
    falloff_start: float
    """Whether holding the button keeps firing."""
    auto: bool
    """Cubes per second the shot shoves the *shooter*, opposite their aim.

    AssaultCube's `attackphysics` does exactly this —
    `owner->vel.add(vec(unitv).mul(recoil/dist))` with a negative recoil — and it
    is the whole of shoot-jumping: aim at the floor and the push is upward. It is
    served to the browser rather than duplicated in TypeScript because the client
    has to predict the same impulse the server is about to apply, and two copies
    of that number is a mispredict on every shot.

    Held on the big, slow weapons and near-zero on the fast ones, which is what
    keeps it a technique rather than a flight mode: an automatic firing at 700 rpm
    loses more to gravity between shots than each shot gives back.
    """
    kickback: float

    @property
    def interval(self) -> float:
        return 60.0 / self.rpm

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "damage": self.damage,
            "headMultiplier": self.head_multiplier,
            "rpm": self.rpm,
            "interval": round(self.interval, 4),
            "mag": self.mag,
            "reserve": self.reserve,
            "reloadTime": self.reload_time,
            "spread": self.spread,
            "pellets": self.pellets,
            "range": self.range,
            "auto": self.auto,
            "kickback": self.kickback,
        }


# The loadout. AssaultCube-flavoured rather than AssaultCube-derived: these are
# tuned against our own movement speed (22 cubes/s, which is not AC's), so
# copying its damage table would produce a different game wearing its numbers.
#
# Order is the slot order — weapon 1 through 5 on the number row.
WEAPONS: tuple[Weapon, ...] = (
    Weapon(
        id="knife",
        name="Knife",
        damage=55,
        head_multiplier=2.0,
        rpm=120,
        mag=0,
        reserve=-1,
        reload_time=0.0,
        spread=0.0,
        pellets=1,
        range=5.0,
        falloff_start=5.0,
        auto=False,
        kickback=0.0,
    ),
    Weapon(
        id="pistol",
        name="Pistol",
        damage=19,
        head_multiplier=2.2,
        rpm=420,
        mag=10,
        reserve=-1,
        reload_time=1.4,
        spread=0.013,
        pellets=1,
        range=140.0,
        falloff_start=60.0,
        auto=False,
        kickback=1.2,
    ),
    Weapon(
        id="assault",
        name="Assault Rifle",
        damage=21,
        head_multiplier=2.0,
        rpm=700,
        mag=20,
        reserve=120,
        reload_time=1.9,
        spread=0.021,
        pellets=1,
        range=200.0,
        falloff_start=80.0,
        auto=True,
        kickback=1.6,
    ),
    Weapon(
        id="shotgun",
        name="Shotgun",
        damage=11,
        head_multiplier=1.4,
        rpm=68,
        mag=7,
        reserve=40,
        reload_time=2.6,
        spread=0.075,
        pellets=8,
        range=48.0,
        falloff_start=14.0,
        auto=False,
        kickback=9.5,
    ),
    Weapon(
        id="sniper",
        name="Sniper Rifle",
        damage=72,
        head_multiplier=2.0,
        rpm=62,
        mag=5,
        reserve=25,
        reload_time=2.3,
        spread=0.002,
        pellets=1,
        range=320.0,
        falloff_start=320.0,
        auto=False,
        kickback=8.0,
    ),
)

WEAPON_BY_ID = {w.id: w for w in WEAPONS}
DEFAULT_WEAPON = 2  # the assault rifle, index into WEAPONS


def weapon_at(index: int) -> Weapon:
    """The weapon in a slot, clamped. An out-of-range slot on the wire is a
    typo or a probe, not a reason to drop the player's whole input frame."""
    return WEAPONS[max(0, min(len(WEAPONS) - 1, index))]


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def aim_vector(yaw: float, pitch: float) -> tuple[float, float, float]:
    """A unit direction from view angles, in cube coordinates.

    Matches the client's camera exactly: `x`/`y` are the grid plane (the same
    `cos(yaw)`, `sin(yaw)` pair movement uses) and `z` is height, with a positive
    pitch looking up.
    """
    cp = math.cos(pitch)
    return (cp * math.cos(yaw), cp * math.sin(yaw), math.sin(pitch))


def eye_position(
    x: float, y: float, z: float, eye: float = PLAYER_EYE_HEIGHT
) -> tuple[float, float, float]:
    """Where a shot leaves from: the eye, not the feet.

    `eye` is a parameter because crouching lowers it, and a crouched player whose
    shots still left from standing height would be firing through their own cover.
    """
    return (x, y, z + eye)


def kick_vector(
    weapon: Weapon, yaw: float, pitch: float, crouching: bool = False
) -> tuple[float, float, float]:
    """The impulse a shot applies to the **shooter**, in cubes per second.

    Opposite the aim, which is the entire mechanic: aim at the floor and the push
    is upward, so a jump plus a well-timed shotgun blast reaches ledges a jump
    cannot. AC scales it by 0.75 while crouching (`attackphysics`), and keeping
    that is what makes a braced shot the accurate one *and* the one that moves you
    least — two reasons to crouch that point the same way.

    Derived here rather than in `match.py` so the client can compute the identical
    vector from the served `kickback` number and predict its own recoil.
    """
    if weapon.kickback <= 0:
        return (0.0, 0.0, 0.0)
    dx, dy, dz = aim_vector(yaw, pitch)
    push = weapon.kickback * (CROUCH_KICK_SCALE if crouching else 1.0)
    return (-dx * push, -dy * push, -dz * push)


def spread_vector(
    direction: tuple[float, float, float], spread: float, rng: random.Random
) -> tuple[float, float, float]:
    """Perturb an aim direction inside a cone of half-angle `spread`.

    Sampled uniformly over the cone's *area* (hence the square root) rather than
    uniformly in angle, which would cluster every pellet at the centre and make a
    shotgun behave like a rifle at range.
    """
    if spread <= 0:
        return direction
    dx, dy, dz = direction
    # Any vector not parallel to the aim gives a usable first basis vector.
    ax, ay, az = (0.0, 0.0, 1.0) if abs(dz) < 0.9 else (1.0, 0.0, 0.0)
    ux, uy, uz = (
        dy * az - dz * ay,
        dz * ax - dx * az,
        dx * ay - dy * ax,
    )
    ul = math.sqrt(ux * ux + uy * uy + uz * uz) or 1.0
    ux, uy, uz = ux / ul, uy / ul, uz / ul
    vx, vy, vz = (
        dy * uz - dz * uy,
        dz * ux - dx * uz,
        dx * uy - dy * ux,
    )
    angle = spread * math.sqrt(rng.random())
    phi = rng.random() * math.tau
    sa, ca = math.sin(angle), math.cos(angle)
    ox = ca * dx + sa * (math.cos(phi) * ux + math.sin(phi) * vx)
    oy = ca * dy + sa * (math.cos(phi) * uy + math.sin(phi) * vy)
    oz = ca * dz + sa * (math.cos(phi) * uz + math.sin(phi) * vz)
    length = math.sqrt(ox * ox + oy * oy + oz * oz) or 1.0
    return (ox / length, oy / length, oz / length)


def raycast_world(
    world: World,
    origin: tuple[float, float, float],
    direction: tuple[float, float, float],
    max_distance: float,
) -> float:
    """Distance along `direction` to the first surface, or `max_distance`.

    A grid DDA, because the world *is* a grid: the ray is walked cell by cell,
    and within each cell only two things can stop it — the cell being solid, or
    the ray leaving the gap between that cell's floor and ceiling. That makes a
    shot across a 256-cube map a few hundred cheap steps rather than a scene
    traversal.

    The height test uses the cell's flat floor/ceiling, so a heightfield slope is
    treated as a step. Shots graze slopes they might have clipped by a few
    hundredths of a cube; the alternative is per-triangle intersection against a
    mesh the server does not build.
    """
    ox, oy, oz = origin
    dx, dy, dz = direction
    cx, cy = math.floor(ox), math.floor(oy)

    if world.is_solid(cx, cy):
        return 0.0

    step_x = 1 if dx > 0 else -1
    step_y = 1 if dy > 0 else -1
    inf = math.inf
    t_delta_x = abs(1.0 / dx) if dx != 0 else inf
    t_delta_y = abs(1.0 / dy) if dy != 0 else inf
    t_max_x = ((cx + 1 - ox) / dx if dx > 0 else (cx - ox) / dx) if dx != 0 else inf
    t_max_y = ((cy + 1 - oy) / dy if dy > 0 else (cy - oy) / dy) if dy != 0 else inf

    t = 0.0
    # Bounded rather than `while True`: a direction of (0, 0, ±1) never leaves its
    # cell, and a loop that only exits on a boundary crossing would never end.
    for _ in range(4 * world.ssize + 8):
        t_exit = min(t_max_x, t_max_y, max_distance)
        floor = world.floor_at(cx, cy)
        ceil = world.ceil_at(cx, cy)
        # The ray is linear in z, so the crossing solves directly.
        if dz < 0:
            t_hit = (floor - oz) / dz
            if t <= t_hit <= t_exit:
                return t_hit
        elif dz > 0:
            t_hit = (ceil - oz) / dz
            if t <= t_hit <= t_exit:
                return t_hit
        else:
            if oz < floor or oz > ceil:
                return t
        if t_exit >= max_distance:
            return max_distance
        if t_max_x < t_max_y:
            cx += step_x
            t = t_max_x
            t_max_x += t_delta_x
        else:
            cy += step_y
            t = t_max_y
            t_max_y += t_delta_y
        if world.is_solid(cx, cy):
            return t
    return max_distance


def ray_hits_body(
    origin: tuple[float, float, float],
    direction: tuple[float, float, float],
    feet: tuple[float, float, float],
    radius: float = PLAYER_RADIUS,
    height: float = BODY_HEIGHT,
) -> float | None:
    """Distance at which the ray enters a player's cylinder, or `None`.

    The hitbox is the *collision* cylinder — the same radius the movement code
    keeps clear and the same height the avatar capsule is drawn to. Solved as the
    intersection of two intervals (inside the infinite cylinder, inside the
    height slab) so a shot straight up or down is not a special case.
    """
    ox, oy, oz = origin
    dx, dy, dz = direction
    fx, fy, fz = feet
    px, py = ox - fx, oy - fy

    a = dx * dx + dy * dy
    c = px * px + py * py - radius * radius
    if a > 1e-9:
        b = 2.0 * (px * dx + py * dy)
        disc = b * b - 4.0 * a * c
        if disc < 0:
            return None
        root = math.sqrt(disc)
        enter = (-b - root) / (2.0 * a)
        exit_ = (-b + root) / (2.0 * a)
    elif c > 0:
        # Travelling vertically and outside the cylinder: never enters it.
        return None
    else:
        enter, exit_ = -math.inf, math.inf

    z0, z1 = fz, fz + height
    if abs(dz) > 1e-9:
        tz0 = (z0 - oz) / dz
        tz1 = (z1 - oz) / dz
        if tz0 > tz1:
            tz0, tz1 = tz1, tz0
    elif oz < z0 or oz > z1:
        return None
    else:
        tz0, tz1 = -math.inf, math.inf

    enter = max(enter, tz0)
    exit_ = min(exit_, tz1)
    if enter > exit_ or exit_ < 0:
        return None
    # A negative entry with a positive exit means the muzzle is already inside
    # them — point blank, which is a hit at zero distance, not a miss.
    return max(enter, 0.0)


def damage_at(weapon: Weapon, distance: float) -> float:
    """Damage after falloff: full out to `falloff_start`, tapering to half at
    `range`. Flat damage across a 200-cube map makes a rifle a sniper."""
    if distance <= weapon.falloff_start or weapon.range <= weapon.falloff_start:
        return weapon.damage
    span = weapon.range - weapon.falloff_start
    t = min(1.0, (distance - weapon.falloff_start) / span)
    return weapon.damage * (1.0 - 0.5 * t)


# ---------------------------------------------------------------------------
# Lag compensation
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class HistoryFrame:
    """Where everyone was at one instant, on the server's own clock."""

    t: float  # milliseconds, the same base as a snapshot's `t`
    positions: dict[str, tuple[float, float, float]] = field(default_factory=dict)
    """Body height per player at that instant, for rewinding a crouch.

    Kept beside the positions rather than folded into them so `record`/`rewind`
    keep their existing shape. It matters for the same reason the positions do: a
    shooter who aimed at a standing head and hit it must not be told they missed
    because the target crouched in the 130 ms since.
    """
    heights: dict[str, float] = field(default_factory=dict)


class PositionHistory:
    """A room's rolling record of player positions, for rewinding a shot.

    Sampled once per tick (20 Hz) and interpolated on read, so a rewind lands on
    the shooter's actual view instant rather than on the nearest tick — 50 ms of
    quantisation is most of a body width at running speed.
    """

    def __init__(self, seconds: float = HISTORY_SECONDS) -> None:
        self.seconds = seconds
        self.frames: list[HistoryFrame] = []

    def record(
        self,
        t_ms: float,
        positions: dict[str, tuple[float, float, float]],
        heights: dict[str, float] | None = None,
    ) -> None:
        self.frames.append(
            HistoryFrame(t=t_ms, positions=positions, heights=heights or {})
        )
        cutoff = t_ms - self.seconds * 1000.0
        while len(self.frames) > 2 and self.frames[0].t < cutoff:
            self.frames.pop(0)

    def clamp(self, view_t: float | None, now_ms: float) -> float:
        """The instant a shot will actually be judged at.

        This is the security boundary, not a nicety: `view_t` comes from the
        client, and an unclamped one lets a shooter be judged against wherever
        their target was ten seconds ago.
        """
        if view_t is None or not math.isfinite(view_t):
            return now_ms
        return max(now_ms - MAX_REWIND_MS, min(now_ms, view_t))

    def _bracket(self, t_ms: float) -> tuple[HistoryFrame, HistoryFrame, float] | None:
        """The two frames `t_ms` falls between, and how far along it sits."""
        if not self.frames:
            return None
        if t_ms >= self.frames[-1].t:
            return self.frames[-1], self.frames[-1], 0.0
        if t_ms <= self.frames[0].t:
            return self.frames[0], self.frames[0], 0.0
        older = self.frames[0]
        newer = self.frames[-1]
        for i in range(1, len(self.frames)):
            if self.frames[i].t >= t_ms:
                older = self.frames[i - 1]
                newer = self.frames[i]
                break
        span = newer.t - older.t
        return older, newer, 0.0 if span <= 0 else (t_ms - older.t) / span

    def rewind(self, t_ms: float) -> dict[str, tuple[float, float, float]] | None:
        """Interpolated positions at `t_ms`, or `None` if nothing covers it.

        `None` means "use the present" — with no history there is nothing better
        to say, and refusing the shot would be worse than resolving it live.
        """
        bracket = self._bracket(t_ms)
        if bracket is None:
            return None
        older, newer, f = bracket
        out: dict[str, tuple[float, float, float]] = {}
        for pid, (x, y, z) in newer.positions.items():
            prev = older.positions.get(pid)
            if prev is None:
                # Someone who joined between the two frames: no earlier position
                # to move from, so they are simply where they are.
                out[pid] = (x, y, z)
                continue
            out[pid] = (
                prev[0] + (x - prev[0]) * f,
                prev[1] + (y - prev[1]) * f,
                prev[2] + (z - prev[2]) * f,
            )
        return out

    def rewind_heights(self, t_ms: float) -> dict[str, float]:
        """Interpolated body heights at `t_ms`. Empty when nothing was recorded,
        which `resolve_shot` reads as "everyone was standing"."""
        bracket = self._bracket(t_ms)
        if bracket is None:
            return {}
        older, newer, f = bracket
        out: dict[str, float] = {}
        for pid, height in newer.heights.items():
            prev = older.heights.get(pid, height)
            out[pid] = prev + (height - prev) * f
        return out

    def clear(self) -> None:
        self.frames.clear()

    def __len__(self) -> int:
        return len(self.frames)


@dataclass(slots=True)
class PelletHit:
    """One pellet that reached a body."""

    victim: str
    distance: float
    damage: float
    head: bool
    point: tuple[float, float, float]


@dataclass(slots=True)
class ShotResult:
    origin: tuple[float, float, float]
    """Where each pellet ended up — a wall, a body, or the end of its range."""
    endpoints: list[tuple[float, float, float]]
    hits: list[PelletHit]
    rewound_ms: float


def resolve_shot(
    world: World,
    weapon: Weapon,
    origin: tuple[float, float, float],
    direction: tuple[float, float, float],
    targets: dict[str, tuple[float, float, float]],
    rng: random.Random,
    rewound_ms: float = 0.0,
    heights: dict[str, float] | None = None,
) -> ShotResult:
    """Trace one trigger pull against the world and a set of rewound bodies.

    `targets` maps player id to the **feet** position the shot is judged against —
    already rewound by the caller, and already filtered to who can legitimately be
    hit (living, not the shooter, not a teammate). Keeping that policy out of here
    means this function is pure geometry and a test can aim it at one body.

    `heights` is the body height per target, defaulting to standing. Crouching
    genuinely shrinks the hitbox — a crouched enemy that still presented a standing
    one would be the sort of disagreement between what you see and what you hit
    that makes a shooter feel dishonest — and the head band moves down with it,
    since it is defined relative to the top of the body rather than absolutely.
    """
    endpoints: list[tuple[float, float, float]] = []
    hits: list[PelletHit] = []
    ox, oy, oz = origin

    for _ in range(max(1, weapon.pellets)):
        pdx, pdy, pdz = spread_vector(direction, weapon.spread, rng)
        wall = raycast_world(world, origin, (pdx, pdy, pdz), weapon.range)

        best: tuple[float, str] | None = None
        for pid, feet in targets.items():
            tall = BODY_HEIGHT if heights is None else heights.get(pid, BODY_HEIGHT)
            distance = ray_hits_body((ox, oy, oz), (pdx, pdy, pdz), feet, height=tall)
            # A body behind a wall is not a target; the wall is nearer, and the
            # `<` is what makes cover work.
            if distance is None or distance >= wall:
                continue
            if best is None or distance < best[0]:
                best = (distance, pid)

        if best is None:
            endpoints.append((ox + pdx * wall, oy + pdy * wall, oz + pdz * wall))
            continue

        distance, pid = best
        point = (ox + pdx * distance, oy + pdy * distance, oz + pdz * distance)
        tall = BODY_HEIGHT if heights is None else heights.get(pid, BODY_HEIGHT)
        # Relative to the top of the body, so a crouched head is where the
        # crouched head actually is.
        head = point[2] >= targets[pid][2] + (tall - HEAD_BAND)
        amount = damage_at(weapon, distance) * (weapon.head_multiplier if head else 1.0)
        hits.append(
            PelletHit(
                victim=pid,
                distance=distance,
                damage=amount,
                head=head,
                point=point,
            )
        )
        endpoints.append(point)

    return ShotResult(
        origin=origin, endpoints=endpoints, hits=hits, rewound_ms=rewound_ms
    )

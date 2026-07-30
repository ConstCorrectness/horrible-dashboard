"""Noise: what a player can hear, and what it tells them.

Sound is **information**, and in a shooter it is often better information than
sight — you hear someone behind you before you can look. So it is modelled here as
a real mechanic rather than left to the client's audio mixer: the server decides
who heard what, from its own copy of the world.

### Why the server owns it

Because the alternative leaks. If the server simply broadcast every footstep with
its position and let each client decide whether it was audible, then the *packet*
would contain the position of an enemy two rooms away — and a client that chose to
draw it anyway would have a wall hack made of sound. Audibility is therefore
resolved per recipient, before the send, and what goes on the wire is only what
ears actually give you:

    {kind, volume, bearing, up}

A **bearing and a loudness**, never an offset. You can tell roughly where and
roughly how far, which is exactly the resolution a player should be working at, and
it is not enough to draw a dot on a map with.

### Why crouching is the point

The loudest thing in the game is movement, and crouching silences it completely
(`STRIDE_LOUDNESS` is never emitted for a crouched body). That is what makes
AC's crouch speed penalty a *trade* rather than a tax: 40% of your speed buys you
the ability to arrive without being announced. Every other mechanic here exists to
give that trade something to work against — walls muffle rather than block, so
listening rewards patience; and a shot is louder than anything, so firing costs
you your own position.

A player's own noises are deliberately **not** sent back to them. They need no
round trip, the client synthesizes them locally the instant they happen, and a
footstep that arrives 50 ms late does not sound like a footstep.

See docs/modules/hassault.mdx.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from backend.modules.hassault.physics import World
from backend.modules.hassault.weapons import raycast_world

# Audible radius per kind, in cubes: the distance at which the noise fades to
# nothing. Ordered deliberately — a shot carries across a map, a footstep carries
# across a room, and that gap is what makes shooting a decision.
STRIDE_LOUDNESS = 42.0
LAND_LOUDNESS = 55.0
JUMP_LOUDNESS = 30.0
RELOAD_LOUDNESS = 34.0
HURT_LOUDNESS = 40.0
DIE_LOUDNESS = 60.0
# A shot's radius comes from the weapon: a knife is silent, a sniper rifle is not.
SHOT_LOUDNESS_BASE = 120.0

# Cubes of travel between footsteps at a run. Roughly two strides per body length,
# which is what makes a sprinting player sound like one.
STRIDE_DISTANCE = 4.2

# What a wall does to a noise. Not a block: hearing someone through a wall is most
# of what listening is for, and an occlusion test that silenced them outright would
# turn every corner into a hard mute and reward nobody for paying attention.
WALL_MUFFLE = 0.35

# Below this a noise is not sent at all. Without a floor, every step by every
# player on a 256-cube map is an entry in somebody's packet.
MIN_AUDIBLE = 0.06


@dataclass(slots=True, frozen=True)
class Noise:
    """One sound made at one place. Produced by the match, filtered per listener."""

    kind: str
    """Who made it, so it can be kept out of their own envelope."""
    source: str
    x: float
    y: float
    z: float
    """Audible radius in cubes."""
    loudness: float


def shot_loudness(weapon) -> float:
    """How far a weapon is heard.

    Scaled by damage rather than given its own dial: the weapon that hits hardest
    is the one worth hearing about, and tying the two means a balance change to one
    cannot silently desynchronise it from the other.
    """
    if weapon.kickback <= 0 and weapon.range <= 6.0:
        # The knife. Swinging it is quiet, which is the entire reason to carry one.
        return 8.0
    return SHOT_LOUDNESS_BASE * min(1.0, weapon.damage / 60.0 + 0.35)


def hear(
    world: World,
    listener: tuple[float, float, float],
    noise: Noise,
) -> tuple[float, float, int] | None:
    """`(volume, bearing, up)` as `listener` perceives `noise`, or `None`.

    `listener` is an **eye** position, not the feet: the ears are on the head, and
    on a map with head-height gaps the difference decides whether a wall is in the
    way at all.

    Volume falls off linearly with distance rather than by inverse square. That is
    not physics, and it is the right call: inverse square spends almost its whole
    range being inaudible, so the useful band — "someone is nearby and I can tell
    roughly how near" — would collapse into the first few cubes.
    """
    lx, ly, lz = listener
    dx = noise.x - lx
    dy = noise.y - ly
    dz = noise.z - lz
    distance = math.sqrt(dx * dx + dy * dy + dz * dz)
    if distance >= noise.loudness:
        return None

    volume = 1.0 - distance / noise.loudness
    if distance > 1e-6:
        # One ray from the ear to the source. A partial credit test rather than a
        # visibility test: the muffle is a multiplier, so "through a wall" is
        # quieter and still there.
        direction = (dx / distance, dy / distance, dz / distance)
        if raycast_world(world, listener, direction, distance) < distance - 1e-6:
            volume *= WALL_MUFFLE
    if volume < MIN_AUDIBLE:
        return None

    bearing = math.atan2(dy, dx)
    up = 0
    # A cube and a half of height difference before it is called high or low: below
    # that it is the same floor, and saying otherwise would flicker every step.
    if dz > 1.5:
        up = 1
    elif dz < -1.5:
        up = -1
    return volume, bearing, up


def envelope(
    world: World,
    listener: tuple[float, float, float],
    listener_id: str,
    noises: list[Noise],
) -> list[dict[str, float | str | int]]:
    """The audible subset of `noises`, in wire form, for one listener.

    Own noises are dropped here: the client makes those sounds itself, without
    waiting for a round trip.
    """
    out: list[dict[str, float | str | int]] = []
    for noise in noises:
        if noise.source == listener_id:
            continue
        heard = hear(world, listener, noise)
        if heard is None:
            continue
        volume, bearing, up = heard
        out.append(
            {
                "kind": noise.kind,
                "volume": round(volume, 3),
                "bearing": round(bearing, 3),
                "up": up,
            }
        )
    return out

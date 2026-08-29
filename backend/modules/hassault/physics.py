"""Server-side cube-world physics: the authoritative half of the netcode.

This is a deliberate port of the client's `world.ts` and `player.ts`. Two
implementations of the same rules is a cost, and the alternative was worse: a
server that cannot simulate cannot be authoritative, and a server that merely
sanity-checks client positions has no answer at all when two clients disagree.

The duplication is made safe the way the peer fabric's Kotlin wire is — with
pinned vectors. `packages/core/src/modules/hassault/__tests__/physics-vectors.json`
is read by *both* `backend/tests/test_hassault_physics.py` and the matching vitest
file, so a change to either side that moves the simulation fails a test rather
than desynchronising a live match.

Agreement is asserted to a tolerance, not bit-for-bit: `math.sin` and
`Math.sin` are not required to round identically, and demanding they do would be
a test about libm rather than about this code.

Coordinates match the client: `x`/`y` index the cube grid and `z` is height,
measured at the player's **feet**.

### The movement model, and why it is velocity-based

Movement is a **velocity** integrated against the grid, not a position stepped by
a direction. That is not decoration: three of the mechanics this game is *for*
have nowhere to live without it. Weapon recoil pushing the shooter (AC's
shoot-jump) is an impulse; the chained-jump speed boost multiplies a speed that
has to already exist; and the difference between ground control and air momentum
*is* the difference between two friction constants.

The constants come from AssaultCube's `physics.cpp` and `entity.h`, converted out
of AC's per-millisecond unit soup into plain SI (cubes, seconds):

* **Response rates.** AC blends velocity toward the wish direction each frame by
  `1/fpsfric` where `fpsfric = friction/curtime*20`, with `friction` 6 on the
  floor and 30 in the air. That is a time constant of `friction/50` seconds, so
  ground control settles in ~0.12 s while air control takes ~0.6 s — which is
  what makes momentum, not the stick, decide where a jump lands.
* **Chained jumps.** AC boosts by `1.25/max(speed/fullspeed, 1)` when you jump
  again within 250 ms while strafing: 25% faster, but never past 125% of run
  speed, and it decays in the air so it has to be re-earned every landing.
* **Crouching.** Eye height drops to 3/4 (`entity.h`), speed to 0.4 — except
  while airborne after crouching *in* the air, where AC deliberately leaves you
  at full speed so a crouch-jump clears a gap without costing momentum.
* **Gravity ramps with time in air** (`dropf = (gravity-1) + timeinair/15`), so a
  fall accelerates harder the longer it lasts and a jump comes down faster than
  it went up.

Two deliberate deviations, both because AC's exact behaviour would be a bug here:

1. The blend is `1 - exp(-k*dt)` rather than AC's `k*dt`. AC's linear form is
   frame-rate dependent — its players tune `maxfps` for movement — and this
   server integrates whatever `dt` a client reports, so a frame-rate-dependent
   rule would literally pay clients for lying about their frame times.
2. The chain-boost window is measured from **landing**, not from the previous
   jump, and carries no "must be higher than last time" test. AC's version only
   fires on rising terrain, which makes it undiscoverable; measured from the
   landing it is a timing skill anyone can find and nobody can automate away.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from backend.modules.hassault.cgz import CHF, FHF, SEMISOLID, SOLID, SPACE, CgzMap
from backend.modules.hassault import hitbox

#: `cgz.ENTITY_NAMES` index of a `ladder`. Named here rather than imported as a
#: bare number so the one place that reads it says what it is reading.
LADDER_ENTITY = 12

#: Water plane for a world that has none. Far below any floor a `.cgz` can hold
#: (`floor` is a signed byte), so "is this body in water" is one comparison with
#: no special case for the absence of water.
NO_WATER = -1e9

# Player dimensions. The numbers themselves live in `hitbox.py`, which is the one
# authority both clients read, and these names survive as aliases for the *default*
# body — they are what the movement vectors were generated against and what every
# call site that does not decide a hit still reads.
#
# Anything that decides where a bullet lands, or how tall a body is right now, goes
# through `hitbox.current()` instead, so a spec tuned in the lab takes effect
# without a restart. The two are the same until somebody tunes one.
PLAYER_RADIUS = hitbox.DEFAULT.radius
PLAYER_EYE_HEIGHT = hitbox.DEFAULT.eye_height
PLAYER_ABOVE_EYE = hitbox.DEFAULT.above_eye
# Total body height standing: what the collision code reserves headroom for, what
# the avatar capsule is drawn to, and what a shot is tested against.
STANDING_HEIGHT = hitbox.DEFAULT.standing_height

# Crouching. The eye drops to three quarters of its height — `maxeyeheight*3/4`
# in AC's `updatecrouch` — while `aboveeye` is unchanged, so a crouched body is
# ~1.1 cubes shorter and fits under a gap a standing one does not.
CROUCH_EYE_SCALE = hitbox.DEFAULT.crouch_eye_scale
CROUCH_EYE_HEIGHT = hitbox.DEFAULT.crouch_eye_height
CROUCH_HEIGHT = hitbox.DEFAULT.crouch_height
# AC's `chspeed`. Slow enough to be a real cost, which is what makes silent
# movement (see `noise.py`) a trade rather than a free upgrade.
CROUCH_SPEED_SCALE = 0.4
# Seconds for a full stand↔crouch transition. AC animates the eye height rather
# than snapping it, and the animation is not cosmetic: it is why crouch-jumping
# has a rhythm to learn instead of being a binary state flip.
CROUCH_TRANSITION = 0.15

# Movement constants. Tuned to feel like AC rather than derived from it, but they
# are part of the wire contract now: a client predicting with different numbers
# mispredicts every frame.
MOVE_SPEED = 22.0
GRAVITY = 55.0
JUMP_SPEED = 19.0
STEP_HEIGHT = 1.6

# How fast velocity converges on the wish direction, per second. AC's friction 6
# on the floor and 30 in the air, as `50/friction` — see the module docstring.
GROUND_RESPONSE = 50.0 / 6.0
AIR_RESPONSE = 50.0 / 30.0

# Gravity ramp. `g * (1 + time_in_air/GRAVITY_RAMP)`, capped: AC's ramp is
# unbounded and reaches 4.5x within a second, which turns any long drop into a
# teleport straight down.
GRAVITY_RAMP = 1.0
MAX_GRAVITY_SCALE = 2.5

# The chained-jump boost. Both numbers are AC's.
JUMP_CHAIN_WINDOW = 0.25
JUMP_CHAIN_BOOST = 1.25

# Landing harder than this costs health, at `FALL_DAMAGE_PER_SPEED` per cube/s
# over. A flat jump lands at `JUMP_SPEED`, so ordinary movement never hurts —
# but a shoot-jump that gains height has to be paid for on the way down, which is
# the only thing stopping recoil-launching from being free vertical travel.
FALL_SAFE_SPEED = 34.0
FALL_DAMAGE_PER_SPEED = 3.0

# -- water ------------------------------------------------------------------
#
# A map's `waterlevel` has been parsed since the reader was written and did
# nothing. It is now the one piece of the map that changes how a body moves, and
# the numbers below are **ours**: AC's water constants are tuned against AC's
# gravity and jump, and copying them into a simulation that already deviates
# (`1-exp(-k*dt)` rather than `k*dt`) would be a port in name only.
#
# What water is *for* decides the numbers. It is a slow, safe, loud route: you
# give up speed and the ability to jump, and you get a descent that costs no
# health and a body that is hard to hit cleanly.

#: Horizontal speed cap in water, wading or swimming. Replaces the crouch scale
#: rather than multiplying with it — there is no crouching in water, and two
#: penalties stacking would make a crouched swimmer slower than walking pace.
WATER_SPEED_SCALE = 0.62

#: Velocity convergence while submerged. Between ground and air on purpose:
#: water gives no footing, but it grips — using `AIR_RESPONSE` here would make
#: swimming feel like ice, and `GROUND_RESPONSE` like walking.
WATER_RESPONSE = 50.0 / 12.0

#: Vertical damping per second while submerged, as `exp(-WATER_DRAG*dt)`.
#:
#: **This is what makes water a safe landing**, and it is deliberately not a
#: special case in the fall-damage rule: a body entering water sheds its speed
#: over a few frames because water is thick, and the impact it eventually reaches
#: the bottom with is genuinely small. The explicit rescue below is the backstop
#: for shallow water, not the mechanism.
WATER_DRAG = 5.5

#: How much of gravity still applies while submerged, so you sink slowly rather
#: than float. A neutral body would make water a place you get stuck in.
WATER_GRAVITY_SCALE = 0.32

#: Upward speed while holding jump under water, and the fraction of it that
#: holding crouch pushes you down at. Diving is slower than surfacing because
#: nothing about water should be a fast way to get anywhere.
SWIM_SPEED = 9.0
SWIM_DOWN_SCALE = 0.7

# -- ladders ----------------------------------------------------------------
#
# `ladder` entities are the other half of the map that was parsed and dropped.
# A ladder is a vertical volume you cannot fall out of.

#: How far from the ladder's cell centre the body's centre may be. Wider than the
#: body's own radius: a ladder you have to stand exactly under is a ladder people
#: report as broken.
LADDER_REACH = 2.0

#: How far below the base the volume still catches you, so walking into the foot
#: of a ladder attaches rather than requiring you to already be off the ground.
LADDER_GRACE = 1.0

#: Climb rate, up or down. Slower than walking — a ladder is exposure, and one
#: that got you out of the pit faster than the stairs would make the stairs
#: decoration.
LADDER_SPEED = 9.0

#: Horizontal movement is damped, not stopped, on a ladder: you have to be able
#: to step off at the top, and a body frozen in x/y could only leave by jumping.
LADDER_HORIZONTAL_SCALE = 0.45

#: How close to the top the axial grip is released, so pressing forward at the
#: last rung walks you onto the ledge instead of holding you against it.
#:
#: Without this the ladder is a trap: the grip that makes descending possible is
#: the same grip that stops you stepping off the top, and a player who climbed all
#: the way up could only leave by strafing sideways into the air.
LADDER_TOP_RELEASE = 0.05

# Longest frame the simulation will integrate in one go. A client that stalls and
# then sends a huge dt would otherwise tunnel straight through a wall — the same
# clamp the client applies, and here it doubles as the cap on how much distance a
# single input packet can be worth.
MAX_STEP_DT = 0.1


def _signed(plane: bytes, i: int) -> int:
    """One byte of a signed plane. `floor` and `ceil` can both go below zero."""
    v = plane[i]
    return v - 256 if v > 127 else v


@dataclass(frozen=True, slots=True)
class Ladder:
    """One climbable volume, already resolved against the floor beneath it.

    Derived rather than stored, by `ladders_from`, because the entity carries a
    *height* and the simulation needs a span — and the base of that span is the
    floor of the cell, which only the grid knows.
    """

    #: Cell centre, in cubes.
    x: float
    y: float
    #: Foot and head of the climbable span.
    base: float
    top: float


def ladders_from(
    ssize: int, floor_at: Any, entities: Iterable[Any]
) -> tuple[Ladder, ...]:
    """Resolve every `ladder` entity into a span.

    Takes `floor_at` as a callable rather than a `World` so it can run inside
    `World.from_map`, before the world it is describing exists. Mirrored by
    `laddersFrom` in `world.ts` and pinned by the conformance vectors — the two
    sides must agree on where a ladder starts and ends, or a climb desyncs on the
    first frame.

    A height of zero is **dropped**, not treated as unbounded: a mapper who never
    set the attribute meant "I did not finish this", and a ladder of infinite
    height in the middle of a room would be a hole in the map's physics.
    """
    out: list[Ladder] = []
    for entity in entities:
        if getattr(entity, "type", None) != LADDER_ENTITY:
            continue
        height = float(getattr(entity, "attr1", 0) or 0)
        if height <= 0:
            continue
        x, y = int(entity.x), int(entity.y)
        if not (0 <= x < ssize and 0 <= y < ssize):
            continue
        base = float(floor_at(x, y))
        out.append(Ladder(x=x + 0.5, y=y + 0.5, base=base, top=base + height))
    return tuple(out)


@dataclass(slots=True)
class World:
    """The cube grid, with the height rules the simulation reads.

    Holds the planes as `bytes` straight off the parsed map — the same buffers the
    `/cubes` route serves, so the server and the browser are looking at literally
    the same numbers.
    """

    ssize: int
    type: bytes
    floor: bytes
    ceil: bytes
    vdelta: bytes
    #: The map's water plane. Defaults far below any real floor rather than to
    #: zero: zero is a perfectly ordinary floor height, and a default of zero
    #: would flood every world built without one — including every test world.
    waterlevel: float = NO_WATER
    #: Climbable spans, from the map's `ladder` entities. A tuple because it is
    #: read on every step and never changes for the life of a room.
    ladders: tuple[Ladder, ...] = ()

    @classmethod
    def from_map(cls, world: CgzMap) -> World:
        grid = cls(
            ssize=world.ssize,
            type=world.type,
            floor=world.floor,
            ceil=world.ceil,
            vdelta=world.vdelta,
            waterlevel=world.waterlevel,
        )
        grid.ladders = ladders_from(world.ssize, grid.floor_at, world.entities)
        return grid

    def index(self, x: int, y: int) -> int:
        return y * self.ssize + x

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.ssize and 0 <= y < self.ssize

    def is_solid(self, x: int, y: int) -> bool:
        """Out of bounds counts as solid, so nobody walks off the edge of a map."""
        if not self.in_bounds(x, y):
            return True
        t = self.type[self.index(x, y)]
        return t == SOLID or t == SEMISOLID

    def vdelta_at(self, vx: int, vy: int) -> int:
        cx = min(max(vx, 0), self.ssize - 1)
        cy = min(max(vy, 0), self.ssize - 1)
        return self.vdelta[self.index(cx, cy)]

    def _corner_delta_sum(self, x: int, y: int) -> int:
        def d(cx: int, cy: int) -> int:
            return self.vdelta[self.index(cx, cy)] if self.in_bounds(cx, cy) else 0

        return d(x, y) + d(x + 1, y) + d(x, y + 1) + d(x + 1, y + 1)

    def floor_at(self, x: int, y: int) -> float:
        """The floor height a body standing in this cell rests on.

        `(sum of four vdeltas) / 16` — the mean of four `vdelta/4` corner terms,
        which is `physics.cpp:287`. Using `/4` here sinks the player into slopes.
        """
        if not self.in_bounds(x, y):
            return 0.0
        i = self.index(x, y)
        f = float(_signed(self.floor, i))
        if self.type[i] == FHF:
            f -= self._corner_delta_sum(x, y) / 16
        return f

    def ceil_at(self, x: int, y: int) -> float:
        if not self.in_bounds(x, y):
            return 0.0
        i = self.index(x, y)
        c = float(_signed(self.ceil, i))
        if self.type[i] == CHF:
            c += self._corner_delta_sum(x, y) / 16
        return c

    def cells_in_radius(
        self, x: float, y: float, radius: float
    ) -> tuple[int, int, int, int]:
        """Inclusive grid bounds of the cells a body of `radius` overlaps.

        The circle's AABB, as AC's `rectcollide` uses — which is why the body is
        2.2 cubes wide and needs three cells of clearance.
        """
        return (
            math.floor(x - radius),
            math.floor(x + radius),
            math.floor(y - radius),
            math.floor(y + radius),
        )


@dataclass(slots=True)
class PlayerState:
    """Everything the simulation needs to advance one body.

    Rather more than a position, now that movement carries momentum. The fields
    past `on_ground` are the ones a client cannot derive from a snapshot on its
    own, which is why they ride in the **private** half of a snapshot envelope
    (`MatchPlayer.private_view`) — see `reconcile` in `net.ts`.
    """

    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    vel_x: float = 0.0
    vel_y: float = 0.0
    vel_z: float = 0.0
    yaw: float = 0.0
    pitch: float = 0.0
    on_ground: bool = False
    """Crouch animation, 0 standing to 1 fully crouched."""
    crouch: float = 0.0
    """What the last input asked for, so the *transition* into a crouch can be
    detected — which is what `crouched_in_air` keys off."""
    crouch_held: bool = False
    """Crouch began while airborne. AC leaves such a player at full speed, so a
    crouch-jump clears a gap without paying the crouch speed penalty."""
    crouched_in_air: bool = False
    time_in_air: float = 0.0
    """Simulated seconds this body has been advanced by. A clock local to the
    simulation, so the jump-chain window means the same thing on both sides of
    the wire without either trusting the other's wall clock."""
    t: float = 0.0
    """`t` of the last landing. The chain-boost window is measured from here."""
    landed_at: float = -999.0
    """Impact speed of a landing that happened *this step*, else 0.

    An output, not state: the server turns it into fall damage and the client
    ignores it. Written every step so a replayed command cannot double-count.
    """
    fall_speed: float = 0.0


@dataclass(slots=True)
class MoveInput:
    forward: float = 0.0
    strafe: float = 0.0
    jump: bool = False
    crouch: bool = False
    # Deliberately absent: noclip. It is a local debugging affordance on the
    # client and must never be something a packet can ask the server for.
    yaw: float = 0.0
    pitch: float = 0.0
    dt: float = 0.0
    seq: int = 0


def body_height(player: PlayerState) -> float:
    """Total height of the body right now, mid-crouch included.

    Reads the live spec rather than the module constants: this is one of the two
    numbers a shot is resolved against, so it is exactly what tuning has to be able
    to move."""
    return hitbox.current().height_at(player.crouch)


def eye_height(player: PlayerState) -> float:
    """Height of the eye above the feet — where the camera sits and where a shot
    leaves from, so it has to be the same number in both places."""
    return hitbox.current().eye_at(player.crouch)


def can_stand(
    world: World, x: float, y: float, z: float, height: float | None = None
) -> bool:
    """Whether a body of `height` fits at `(x, y)` with its feet at `z`.

    Three ways to fail: overlapping a solid cell, a floor more than one step
    above the feet, or a ceiling too low. `height` is a parameter rather than the
    standing constant because that is exactly what crouching changes — and it is
    also how "you cannot stand up in here" is decided.

    `height` is nullable rather than defaulted to the standing constant: a default
    argument binds at import, so a tuned standing height would silently never reach
    the callers that omit it — the same `is None` discipline the hardware-resolved
    request fields use, where an explicit `0` is a real answer.
    """
    spec = hitbox.current()
    if height is None:
        height = spec.standing_height
    x0, x1, y0, y1 = world.cells_in_radius(x, y, spec.radius)
    for cy in range(y0, y1 + 1):
        for cx in range(x0, x1 + 1):
            if world.is_solid(cx, cy):
                return False
            if world.floor_at(cx, cy) > z + STEP_HEIGHT:
                return False
            if world.ceil_at(cx, cy) < z + height:
                return False
    return True


def _support(world: World, x: float, y: float) -> tuple[float, float, bool]:
    """Highest floor under the body and lowest ceiling over it, plus `enclosed`."""
    x0, x1, y0, y1 = world.cells_in_radius(x, y, hitbox.current().radius)
    highest_floor = -math.inf
    lowest_ceil = math.inf
    for cy in range(y0, y1 + 1):
        for cx in range(x0, x1 + 1):
            if world.is_solid(cx, cy):
                continue
            highest_floor = max(highest_floor, world.floor_at(cx, cy))
            lowest_ceil = min(lowest_ceil, world.ceil_at(cx, cy))
    if highest_floor == -math.inf:
        return 0.0, math.inf, True
    return highest_floor, lowest_ceil, False


def apply_impulse(player: PlayerState, dx: float, dy: float, dz: float) -> None:
    """Add an external kick to a body's velocity.

    The one way anything outside this module moves a player, and it exists for
    exactly one caller: weapon recoil (`match._fire`). Clearing `on_ground` on an
    upward kick is what makes a shoot-jump work at all — otherwise the vertical
    resolve at the end of the next step lands the player again immediately,
    before the velocity has moved them anywhere.
    """
    player.vel_x += dx
    player.vel_y += dy
    player.vel_z += dz
    if dz > 0:
        player.on_ground = False


def in_water(world: World, player: PlayerState) -> bool:
    """Feet under the water plane: wading or swimming, either way slowed."""
    return player.z < world.waterlevel


def submerged(world: World, player: PlayerState) -> bool:
    """Eye under the water plane: swimming.

    Measured at the **eye**, not the feet, because that is the line the two water
    states actually differ across — a body with its head out has footing, sight
    and a jump, and a body with its head under has none of them. It is also what
    the screen tint keys off, so the moment you lose the jump is the moment the
    screen says so.
    """
    return player.z + eye_height(player) < world.waterlevel


def ladder_at(world: World, player: PlayerState) -> Ladder | None:
    """The ladder this body is on, if any. Nearest wins, which only matters where
    a mapper has put two side by side."""
    if not world.ladders:
        return None
    best: Ladder | None = None
    best_d = LADDER_REACH
    for ladder in world.ladders:
        if not (ladder.base - LADDER_GRACE <= player.z <= ladder.top):
            continue
        d = math.hypot(ladder.x - player.x, ladder.y - player.y)
        if d <= best_d:
            best, best_d = ladder, d
    return best


def _climb_rate(player: PlayerState, ladder: Ladder, move: MoveInput) -> float:
    """How fast this input climbs, -1..1.

    `forward` projected onto **where the body is facing**, not onto its wish
    direction. Two consequences, both wanted: strafing along a ladder does not
    climb it, and running *past* one with forward held does not launch you up it
    — the alignment is near zero when the ladder is off to your side, so the
    volume merely stops you falling for the frame you are inside it.
    """
    dx = ladder.x - player.x
    dy = ladder.y - player.y
    distance = math.hypot(dx, dy)
    if distance < 1e-6:
        # Standing exactly on the cell centre: there is no direction to face
        # toward, so take the input at face value rather than dividing by zero.
        return max(-1.0, min(1.0, move.forward))
    align = (math.cos(player.yaw) * dx + math.sin(player.yaw) * dy) / distance
    return max(-1.0, min(1.0, move.forward * align))


def _tangential(
    player: PlayerState, ladder: Ladder, wx: float, wy: float
) -> tuple[float, float]:
    """Strip the component of a wish direction that points at the ladder."""
    dx = ladder.x - player.x
    dy = ladder.y - player.y
    distance = math.hypot(dx, dy)
    if distance < 1e-6:
        return wx, wy
    tx, ty = dx / distance, dy / distance
    along = wx * tx + wy * ty
    return wx - along * tx, wy - along * ty


def _update_crouch(
    world: World, player: PlayerState, move: MoveInput, dt: float
) -> None:
    """Advance the crouch animation, and refuse to stand up under a low ceiling.

    Reads `on_ground` from the previous step, as AC's `updatecrouch` reads
    `onfloor` — the alternative is resolving crouch after movement, which would
    let a body change height *after* the collision test that admitted it.
    """
    if move.crouch and not player.crouch_held and not player.on_ground:
        player.crouched_in_air = True
    player.crouch_held = move.crouch

    if move.crouch:
        target = 1.0
    elif can_stand(
        world, player.x, player.y, player.z, hitbox.current().standing_height
    ):
        target = 0.0
    else:
        # Nowhere to stand up into. Holding the current crouch beats popping the
        # body through a ceiling, and it is why crouch is worth binding to a hold
        # rather than a toggle in tight geometry.
        target = player.crouch

    rate = dt / CROUCH_TRANSITION if CROUCH_TRANSITION > 0 else 1.0
    if target > player.crouch:
        player.crouch = min(target, player.crouch + rate)
    else:
        player.crouch = max(target, player.crouch - rate)


def _wish_direction(player: PlayerState, move: MoveInput) -> tuple[float, float]:
    """Unit direction the player is asking to move in, in grid coordinates.

    Normalised, so holding forward and strafe is not 1.41x faster than forward
    alone. Diagonal overspeed is the accidental version of a movement tech; this
    game has a deliberate one (the chain boost) and does not need both.
    """
    sin = math.sin(player.yaw)
    cos = math.cos(player.yaw)
    dx = cos * move.forward - sin * move.strafe
    dy = sin * move.forward + cos * move.strafe
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return 0.0, 0.0
    return dx / length, dy / length


def step(world: World, player: PlayerState, move: MoveInput, dt: float) -> None:
    """Advance `player` by `dt` seconds. Mirrors `player.ts` `step` exactly."""
    dt = min(dt, MAX_STEP_DT)
    if dt <= 0:
        player.fall_speed = 0.0
        return
    player.t += dt
    # An output of this step only. Cleared first so a step with no landing in it
    # cannot report the previous one's impact a second time.
    player.fall_speed = 0.0

    _update_crouch(world, player, move, dt)

    # -- horizontal: converge on the wish velocity -------------------------
    #
    # Crouched speed is AC's `chspeed`: 0.4 on the floor, and 0.4 in the air too
    # *unless* the crouch began airborne, which is the crouch-jump exemption.
    #
    # Water replaces the crouch scale rather than multiplying with it: there is
    # no crouching in water, and two penalties stacking would make a crouched
    # swimmer slower than a walk.
    wet = in_water(world, player)
    under = wet and submerged(world, player)

    # -- ladder attachment -------------------------------------------------
    #
    # Resolved before anything reads it, because whether this body is on a ladder
    # changes its speed cap, its wish direction and its whole vertical branch.
    ladder = ladder_at(world, player)
    climb = 0.0
    if ladder is not None:
        climb = _climb_rate(player, ladder, move)
        if climb > 0 and player.z >= ladder.top - LADDER_TOP_RELEASE:
            # At the last rung and still pressing up: **let go entirely**, rather
            # than merely releasing the horizontal grip.
            #
            # Releasing the grip alone leaves the body attached while it walks
            # forward, and walking forward from the top carries it *across* the
            # ladder's centre — at which point the direction "toward the ladder"
            # reverses, the alignment flips sign, and the input that was climbing
            # up is suddenly climbing down. A player stepping off the top would
            # ride the ladder back to the bottom. Detaching is also simply what
            # happened: the climb is over.
            ladder = None

    scale = 1.0
    if wet:
        scale = WATER_SPEED_SCALE
    elif player.crouch > 0.5 and (player.on_ground or not player.crouched_in_air):
        scale = CROUCH_SPEED_SCALE
    if ladder is not None:
        # Damped, not stopped: strafing off is one of the two ways to leave.
        scale *= LADDER_HORIZONTAL_SCALE
    speed_cap = MOVE_SPEED * scale

    wx, wy = _wish_direction(player, move)
    if ladder is not None and climb != 0.0:
        # Movement *along the ladder axis* becomes climbing, not walking, while
        # the input is actually climbing.
        #
        # Without this, pressing forward into a ladder both climbs it and walks
        # you through it, and pressing back to descend walks you straight out of
        # the volume — which made descending impossible at 0.45 of run speed
        # against a two-cube reach. With `climb == 0` the grip is off, so a body
        # merely passing through the volume is not caught by it.
        wx, wy = _tangential(player, ladder, wx, wy)

    if under:
        response = WATER_RESPONSE
    else:
        response = GROUND_RESPONSE if player.on_ground else AIR_RESPONSE
    blend = 1.0 - math.exp(-response * dt)
    player.vel_x += (wx * speed_cap - player.vel_x) * blend
    player.vel_y += (wy * speed_cap - player.vel_y) * blend

    # -- jump, and the chained-jump boost ----------------------------------
    #
    # Not while submerged, even standing on the bottom: down there `jump` is the
    # swim control, and a body that could also launch itself off the riverbed at
    # full jump speed would make deep water a trampoline.
    if move.jump and player.on_ground and not under:
        if move.strafe != 0.0 and (player.t - player.landed_at) <= JUMP_CHAIN_WINDOW:
            speed = math.hypot(player.vel_x, player.vel_y)
            if speed > 0.1:
                # 25% faster, but never past 125% of run speed: AC's
                # `1.25/max(speed/fullspeed, 1)`, which is a boost below the cap
                # and a clamp above it.
                factor = JUMP_CHAIN_BOOST / max(speed / MOVE_SPEED, 1.0)
                player.vel_x *= factor
                player.vel_y *= factor
        player.vel_z = JUMP_SPEED
        player.on_ground = False
        player.time_in_air = 0.0

    # -- horizontal: move, one axis at a time ------------------------------
    #
    # Separated so a blocked direction slides along the wall instead of stopping
    # dead — testing the combined vector once makes every corner sticky. A
    # refused axis loses its velocity: keeping it would store up a shove that
    # fires the instant the body clears the wall.
    height = body_height(player)
    dx = player.vel_x * dt
    dy = player.vel_y * dt
    if dx != 0:
        if can_stand(world, player.x + dx, player.y, player.z, height):
            player.x += dx
        else:
            player.vel_x = 0.0
    if dy != 0:
        if can_stand(world, player.x, player.y + dy, player.z, height):
            player.y += dy
        else:
            player.vel_y = 0.0

    # Resolved before gravity, not after: `_support` reads only x and y, and
    # checking afterwards means a wedged player has already been moved down by
    # one frame of falling — which does not look like falling, it looks like
    # sinking half a cube a second forever.
    floor, ceil, enclosed = _support(world, player.x, player.y)
    if enclosed:
        # Wedged in solid geometry: hold still so the player can walk back out.
        player.vel_x = 0.0
        player.vel_y = 0.0
        player.vel_z = 0.0
        player.on_ground = True
        return

    # -- vertical ----------------------------------------------------------
    #
    # Whether the body was already resting on the floor when this step began —
    # read *after* the jump, which clears it. Both branches below need it, and
    # for the same reason: "arrived on the ground" and "was already on the
    # ground" are different events, and conflating them costs the game two
    # mechanics. A resting body dips below the floor under gravity every single
    # frame, so treating that as a landing would reset the chain-boost window
    # continuously (making the timing free) and charge fall damage for standing
    # still; and a body genuinely falling passes through the snap-down band on
    # its way in, so treating that as a snap would mean nothing ever lands.
    was_grounded = player.on_ground

    if ladder is not None:
        # -- on a ladder ---------------------------------------------------
        #
        # A ladder is a volume you cannot fall out of, which is the whole of it:
        # gravity is off, the climb is driven by the input, and letting go of
        # everything holds position. Jump still fires (the branch above ran) and
        # is how you leave one sideways.
        if move.jump:
            # Jumping off, which has to work from *anywhere* on the ladder and
            # not just from its foot: the jump branch above only fires for a body
            # already on the ground, and a climber is not. Deliberately not
            # clamped to the top — pushing off is how you leave.
            player.vel_z = JUMP_SPEED
            player.z += player.vel_z * dt
            player.on_ground = False
            player.time_in_air = 0.0
            player.crouched_in_air = False
            return
        player.vel_z = LADDER_SPEED * climb
        player.z += player.vel_z * dt
        # Never past the top: the last rung is where the climb ends, and a body
        # that kept rising would float off the roof of the map.
        player.z = min(player.z, ladder.top)
        # No air time accrues on a ladder, so stepping off the top falls at plain
        # gravity rather than at whatever the ramp had reached, and a long climb
        # can never be charged as a fall.
        player.time_in_air = 0.0
        player.crouched_in_air = False
        if player.z <= floor:
            player.z = floor
            player.vel_z = 0.0
            player.on_ground = True
            player.landed_at = player.t
        else:
            player.on_ground = False
        if player.z + height > ceil:
            player.z = max(floor, ceil - height)
            if player.vel_z > 0:
                player.vel_z = 0.0
        return

    if under:
        # -- swimming ------------------------------------------------------
        #
        # Drag first, then buoyancy. The drag is what makes water a safe
        # landing: a body arriving at speed sheds it over a few frames because
        # water is thick, rather than because a rule said falls into water are
        # free.
        player.vel_z *= math.exp(-WATER_DRAG * dt)
        blend = 1.0 - math.exp(-WATER_RESPONSE * dt)
        if move.jump:
            player.vel_z += (SWIM_SPEED - player.vel_z) * blend
        elif move.crouch:
            player.vel_z += (-SWIM_SPEED * SWIM_DOWN_SCALE - player.vel_z) * blend
        else:
            player.vel_z -= GRAVITY * WATER_GRAVITY_SCALE * dt
        # Zeroed rather than accumulated: the ramp is about a fall, and a body
        # surfacing after a long swim must not be met by 2.5x gravity.
        player.time_in_air = 0.0
        player.z += player.vel_z * dt
    else:
        player.time_in_air = 0.0 if was_grounded else player.time_in_air + dt
        # Gravity ramps with time in air, as AC's `dropf` does, so a fall comes
        # down harder than the jump went up.
        gravity = GRAVITY * min(
            MAX_GRAVITY_SCALE, 1.0 + player.time_in_air / GRAVITY_RAMP
        )
        player.vel_z -= gravity * dt
        player.z += player.vel_z * dt

    if player.z <= floor:
        player.z = floor
        if not was_grounded:
            # A real landing. Reported for this step only; the server turns the
            # impact into damage, and the window this opens is what a chained
            # jump has to be timed against.
            #
            # A landing *while submerged* is free, and everything shallower
            # than that is helped in proportion to its depth rather than by a
            # rule: the drag has however long the body spends under the surface
            # to work, so a puddle takes a little off a fall and a pool takes all
            # of it. Gating the rescue on the feet instead of the eye would make
            # any inch of water a total fall-damage negator, which is the
            # puddle-dive that would put shoot-jumping back on a free ride.
            impact = -player.vel_z if player.vel_z < 0 else 0.0
            player.fall_speed = 0.0 if under else impact
            player.landed_at = player.t
        player.vel_z = 0.0
        player.on_ground = True
        player.time_in_air = 0.0
        # On the floor, so the crouch-jump exemption is spent.
        player.crouched_in_air = False
    elif was_grounded and player.vel_z <= 0 and player.z - floor <= STEP_HEIGHT * 0.5:
        # Walking off a small lip shouldn't launch the player into a fall: snap
        # down. Not a landing — nothing was fallen, so it costs no health and
        # opens no chain-boost window that was never earned.
        player.z = floor
        player.vel_z = 0.0
        player.on_ground = True
        player.time_in_air = 0.0
        player.crouched_in_air = False
    else:
        player.on_ground = False
    if player.z + height > ceil:
        player.z = max(floor, ceil - height)
        if player.vel_z > 0:
            player.vel_z = 0.0


def fall_damage(impact: float) -> float:
    """Health cost of landing at `impact` cubes per second.

    Zero for anything a jump can produce, then linear. Kept here beside the
    constants rather than in `match.py` so the client can show the same number
    without a second copy of the rule.
    """
    if impact <= FALL_SAFE_SPEED:
        return 0.0
    return (impact - FALL_SAFE_SPEED) * FALL_DAMAGE_PER_SPEED


def spawn_at(world: World, spawn) -> PlayerState:
    """Place a player on a spawn entity, standing on the ground beneath it.

    **A `playerstart`'s `z` is not the ground.** It is the mapper's own origin at
    the moment they typed `/newent playerstart`, and in Cube 1 that origin is the
    *eye*, not the feet — which is why the single most common value across the
    1741 official spawns is exactly four above the floor the body rests on
    (`(int)(floor + 4.5)`, truncated into the `short` the format stores). Nor is
    it reliable even read that way: AC's editor flies, so the rest are scattered
    from one to twenty-two cubes up with no relation to anything, on flat open
    ground with no map model in sight. The engine gets away with it because
    `entinmap` and gravity resolve the spawn on arrival.

    So the height is taken from the world instead. `_support` is the same query
    `step` resolves against, which makes this its fixed point: a player spawned
    here is already exactly where their first simulated frame would put them,
    rather than falling several cubes into it.

    Reading it as a lower bound — `max(floor_at, spawn.z)`, which this used to do
    — put every one of those 1741 spawns in mid-air, because `spawn.z` is above
    the floor at all but six of them and so the clamp never once fired.
    """
    x = spawn.x + 0.5
    y = spawn.y + 0.5
    floor, _ceil, enclosed = _support(world, x, y)
    if enclosed:
        # Every cell under the body is solid, which no official map manages but a
        # community one might. The centre cell's floor is the best guess left.
        floor = world.floor_at(int(spawn.x), int(spawn.y))
    # Entity yaw is degrees clockwise from north; the camera uses radians about +x.
    yaw = math.radians(spawn.yaw or 0.0)
    return PlayerState(x=x, y=y, z=floor, yaw=yaw, on_ground=True)


def flat_world(ssize: int = 32, floor: int = 0, ceil: int = 16) -> World:
    """An open room with a solid border, for tests and conformance vectors.

    Deliberately not one of the bundled maps: a conformance vector has to mean the
    same thing in Python and in TypeScript, and the cheapest way to guarantee that
    is a world both sides can build from four numbers. The border is solid because
    the engine guarantees one and `is_solid` leans on it.
    """
    n = ssize * ssize
    types = bytearray([SPACE]) * n
    for i in range(ssize):
        for j in (0, 1, ssize - 2, ssize - 1):
            types[i * ssize + j] = SOLID
            types[j * ssize + i] = SOLID
    return World(
        ssize=ssize,
        type=bytes(types),
        floor=bytes([floor & 0xFF]) * n,
        ceil=bytes([ceil & 0xFF]) * n,
        vdelta=bytes(n),
    )

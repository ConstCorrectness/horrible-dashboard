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
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from backend.modules.hassault.cgz import CHF, FHF, SEMISOLID, SOLID, SPACE, CgzMap

# Player dimensions, from AssaultCube's `entity.h` defaults.
PLAYER_RADIUS = 1.1
PLAYER_EYE_HEIGHT = 4.5
PLAYER_ABOVE_EYE = 0.7

# Movement constants. Tuned to feel like AC rather than derived from it, but they
# are part of the wire contract now: a client predicting with different numbers
# mispredicts every frame.
MOVE_SPEED = 22.0
GRAVITY = 55.0
JUMP_SPEED = 19.0
STEP_HEIGHT = 1.6

# Longest frame the simulation will integrate in one go. A client that stalls and
# then sends a huge dt would otherwise tunnel straight through a wall — the same
# clamp the client applies, and here it doubles as the cap on how much distance a
# single input packet can be worth.
MAX_STEP_DT = 0.1


def _signed(plane: bytes, i: int) -> int:
    """One byte of a signed plane. `floor` and `ceil` can both go below zero."""
    v = plane[i]
    return v - 256 if v > 127 else v


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

    @classmethod
    def from_map(cls, world: CgzMap) -> World:
        return cls(
            ssize=world.ssize,
            type=world.type,
            floor=world.floor,
            ceil=world.ceil,
            vdelta=world.vdelta,
        )

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
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    vel_z: float = 0.0
    yaw: float = 0.0
    pitch: float = 0.0
    on_ground: bool = False


@dataclass(slots=True)
class MoveInput:
    forward: float = 0.0
    strafe: float = 0.0
    jump: bool = False
    # Deliberately absent: noclip. It is a local debugging affordance on the
    # client and must never be something a packet can ask the server for.
    yaw: float = 0.0
    pitch: float = 0.0
    dt: float = 0.0
    seq: int = 0


def can_stand(world: World, x: float, y: float, z: float) -> bool:
    x0, x1, y0, y1 = world.cells_in_radius(x, y, PLAYER_RADIUS)
    headroom = PLAYER_EYE_HEIGHT + PLAYER_ABOVE_EYE
    for cy in range(y0, y1 + 1):
        for cx in range(x0, x1 + 1):
            if world.is_solid(cx, cy):
                return False
            if world.floor_at(cx, cy) > z + STEP_HEIGHT:
                return False
            if world.ceil_at(cx, cy) < z + headroom:
                return False
    return True


def _support(world: World, x: float, y: float) -> tuple[float, float, bool]:
    """Highest floor under the body and lowest ceiling over it, plus `enclosed`."""
    x0, x1, y0, y1 = world.cells_in_radius(x, y, PLAYER_RADIUS)
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


def step(world: World, player: PlayerState, move: MoveInput, dt: float) -> None:
    """Advance `player` by `dt` seconds. Mirrors `player.ts` `step` exactly."""
    dt = min(dt, MAX_STEP_DT)

    sin = math.sin(player.yaw)
    cos = math.cos(player.yaw)
    dx = (cos * move.forward - sin * move.strafe) * MOVE_SPEED * dt
    dy = (sin * move.forward + cos * move.strafe) * MOVE_SPEED * dt

    # One axis at a time, so a blocked direction slides along the wall instead of
    # stopping dead. Testing the combined vector makes every corner sticky.
    if dx != 0 and can_stand(world, player.x + dx, player.y, player.z):
        player.x += dx
    if dy != 0 and can_stand(world, player.x, player.y + dy, player.z):
        player.y += dy

    # Resolved before gravity, not after: `_support` reads only x and y, and
    # checking afterwards means a wedged player has already been moved down by
    # one frame of falling — which does not look like falling, it looks like
    # sinking half a cube a second forever.
    floor, ceil, enclosed = _support(world, player.x, player.y)
    if enclosed:
        # Wedged in solid geometry: hold still so the player can walk back out.
        player.vel_z = 0.0
        player.on_ground = True
        return

    if move.jump and player.on_ground:
        player.vel_z = JUMP_SPEED
        player.on_ground = False
    player.vel_z -= GRAVITY * dt
    player.z += player.vel_z * dt

    if player.z <= floor:
        player.z = floor
        player.vel_z = 0.0
        player.on_ground = True
    else:
        player.on_ground = False
        if player.vel_z <= 0 and player.z - floor <= STEP_HEIGHT * 0.5:
            player.z = floor
            player.vel_z = 0.0
            player.on_ground = True
    headroom = PLAYER_EYE_HEIGHT + PLAYER_ABOVE_EYE
    if player.z + headroom > ceil:
        player.z = max(floor, ceil - headroom)
        if player.vel_z > 0:
            player.vel_z = 0.0


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

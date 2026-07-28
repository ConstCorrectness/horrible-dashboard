"""The server's cube physics, and its agreement with the browser's.

Two suites in one file, doing different jobs:

* the **unit tests** pin the AssaultCube rules this port has to obey — the
  heightfield divisors, the three-cell clearance, axis-separated sliding;
* the **conformance tests** replay a fixture that
  `packages/core/src/modules/hassault/__tests__/conformance.test.ts` replays too,
  so the server and the client cannot drift apart without a test going red.

The fixture pins *agreement*, not correctness: it was generated from this
implementation, so a rule that is wrong here would be wrong in it. The unit tests
either side of it are what argue the rules are right. Both matter — a match
desyncs just as thoroughly from two subtly different correct-looking
implementations as from one wrong one.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from backend.modules.hassault.cgz import FHF, SOLID, SPACE
from backend.modules.hassault.physics import (
    JUMP_SPEED,
    MOVE_SPEED,
    PLAYER_ABOVE_EYE,
    PLAYER_EYE_HEIGHT,
    PLAYER_RADIUS,
    STEP_HEIGHT,
    MoveInput,
    PlayerState,
    World,
    can_stand,
    flat_world,
    step,
)

VECTORS = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "core"
    / "src"
    / "modules"
    / "hassault"
    / "__tests__"
    / "physics-vectors.json"
)


def build_world(spec: dict) -> World:
    """Build a world from the fixture's rect description.

    Mirrored by `buildWorld` in the vitest file. Everything starts SOLID so a
    spec only has to describe the space it cares about.
    """
    ssize = spec["ssize"]
    n = ssize * ssize
    types = bytearray([SOLID]) * n
    floor = bytearray(n)
    ceil = bytearray([16]) * n
    vdelta = bytearray(n)
    for rect in spec["rects"]:
        for y in range(rect["y0"], rect["y1"] + 1):
            for x in range(rect["x0"], rect["x1"] + 1):
                i = y * ssize + x
                types[i] = rect.get("type", SPACE)
                floor[i] = rect.get("floor", 0) & 0xFF
                ceil[i] = rect.get("ceil", 16) & 0xFF
                vdelta[i] = rect.get("vdelta", 0)
    return World(
        ssize=ssize,
        type=bytes(types),
        floor=bytes(floor),
        ceil=bytes(ceil),
        vdelta=bytes(vdelta),
    )


def _load_vectors() -> dict:
    return json.loads(VECTORS.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------


def test_flat_world_has_a_solid_border():
    world = flat_world(16)
    assert world.is_solid(0, 0)
    assert world.is_solid(1, 8)
    assert not world.is_solid(8, 8)


def test_out_of_bounds_is_solid():
    """Not a bounds check for its own sake: treating the outside as open would
    let a player walk off the edge of the map."""
    world = flat_world(16)
    assert world.is_solid(-1, 8)
    assert world.is_solid(16, 8)


def test_signed_floor_can_go_below_zero():
    """`floor` and `ceil` are signed planes; reading them unsigned turns a floor
    of -4 into 252 and puts the player in orbit."""
    world = flat_world(8, floor=-4, ceil=12)
    assert world.floor_at(4, 4) == -4


def test_heightfield_floor_uses_the_sixteenth_divisor():
    """`(sum of four corner vdeltas) / 16`, which is the mean of four `vdelta/4`
    terms. Using /4 here sinks a standing player into every slope."""
    world = flat_world(16, floor=8, ceil=24)
    types = bytearray(world.type)
    vdelta = bytearray(world.vdelta)
    for y in range(4, 12):
        for x in range(4, 12):
            types[y * 16 + x] = FHF
            vdelta[y * 16 + x] = 8
    world = World(
        ssize=16,
        type=bytes(types),
        floor=world.floor,
        ceil=world.ceil,
        vdelta=bytes(vdelta),
    )
    # Four corners of 8 → 32/16 = 2 below the base of 8.
    assert world.floor_at(6, 6) == pytest.approx(6.0)
    # A cell on the edge of the patch has two zero corners → 16/16 = 1.
    assert world.floor_at(11, 6) == pytest.approx(7.0)


def test_body_needs_three_cells_of_clearance():
    """Radius 1.1 means a 2.2-cube AABB, as AC's `rectcollide` uses. Worth
    stating out loud — a two-cell corridor looks passable and is not."""
    assert PLAYER_RADIUS * 2 > 2.0
    world = flat_world(16)
    types = bytearray([SOLID]) * (16 * 16)
    for y in (7, 8):  # a two-cell corridor
        for x in range(2, 14):
            types[y * 16 + x] = SPACE
    narrow = World(
        ssize=16,
        type=bytes(types),
        floor=world.floor,
        ceil=world.ceil,
        vdelta=world.vdelta,
    )
    assert not can_stand(narrow, 8.0, 8.0, 0.0)

    for y in (6, 7, 8):  # three cells is enough
        for x in range(2, 14):
            types[y * 16 + x] = SPACE
    wide = World(
        ssize=16,
        type=bytes(types),
        floor=world.floor,
        ceil=world.ceil,
        vdelta=world.vdelta,
    )
    assert can_stand(wide, 8.0, 7.5, 0.0)


def test_headroom_is_checked():
    world = flat_world(16, floor=0, ceil=3)
    assert PLAYER_EYE_HEIGHT + PLAYER_ABOVE_EYE > 3
    assert not can_stand(world, 8.0, 8.0, 0.0)


def test_step_up_is_allowed_but_a_ledge_is_not():
    base = flat_world(16)
    floor = bytearray(base.floor)
    for y in range(16):
        for x in range(9, 16):
            floor[y * 16 + x] = 1
    low = World(
        ssize=16, type=base.type, floor=bytes(floor), ceil=base.ceil, vdelta=base.vdelta
    )
    assert STEP_HEIGHT > 1
    assert can_stand(low, 8.0, 8.0, 0.0)

    for y in range(16):
        for x in range(9, 16):
            floor[y * 16 + x] = 4
    high = World(
        ssize=16, type=base.type, floor=bytes(floor), ceil=base.ceil, vdelta=base.vdelta
    )
    assert not can_stand(high, 8.0, 8.0, 0.0)


def test_movement_resolves_one_axis_at_a_time():
    """Sliding, not sticking. Testing the combined vector once would reject the
    whole move and make every corner sticky."""
    base = flat_world(16)
    types = bytearray(base.type)
    for y in range(16):
        types[y * 16 + 10] = SOLID  # a north-south wall
    world = World(
        ssize=16,
        type=bytes(types),
        floor=base.floor,
        ceil=base.ceil,
        vdelta=base.vdelta,
    )
    player = PlayerState(
        x=8.0, y=8.0, z=0.0, yaw=math.pi / 4
    )  # into the wall, diagonally
    before_y = player.y
    for _ in range(30):
        step(world, player, MoveInput(forward=1.0), 1 / 60)
    assert player.x < 9.0  # stopped by the wall
    assert player.y > before_y + 3  # but still travelled along it


def test_gravity_lands_the_player():
    world = flat_world(16, floor=0, ceil=32)
    player = PlayerState(x=8.0, y=8.0, z=10.0)
    for _ in range(120):
        step(world, player, MoveInput(), 1 / 60)
    assert player.z == pytest.approx(0.0)
    assert player.on_ground
    assert player.vel_z == pytest.approx(0.0)


def test_jump_leaves_the_ground_and_comes_back():
    world = flat_world(16, floor=0, ceil=64)
    player = PlayerState(x=8.0, y=8.0, z=0.0, on_ground=True)
    step(world, player, MoveInput(jump=True), 1 / 60)
    assert not player.on_ground
    assert player.vel_z > 0
    peak = player.z
    for _ in range(120):
        step(world, player, MoveInput(), 1 / 60)
        peak = max(peak, player.z)
    assert peak > 2.0
    assert peak < JUMP_SPEED  # a sanity bound, not the analytic apex
    assert player.on_ground


def test_a_huge_dt_is_clamped():
    """A stalled client sends one enormous frame; integrating it whole teleports
    the player through the far wall."""
    world = flat_world(64, floor=0, ceil=16)
    player = PlayerState(x=32.0, y=32.0, z=0.0, yaw=0.0)
    step(world, player, MoveInput(forward=1.0), 5.0)
    assert player.x - 32.0 <= MOVE_SPEED * 0.1 + 1e-9


def test_enclosed_player_does_not_fall_forever():
    world = flat_world(16, floor=0, ceil=16)
    solid = World(
        ssize=16,
        type=bytes([SOLID]) * (16 * 16),
        floor=world.floor,
        ceil=world.ceil,
        vdelta=world.vdelta,
    )
    player = PlayerState(x=8.0, y=8.0, z=4.0)
    for _ in range(60):
        step(solid, player, MoveInput(), 1 / 60)
    assert player.z == pytest.approx(4.0)
    assert player.on_ground


# ---------------------------------------------------------------------------
# Agreement with the browser
# ---------------------------------------------------------------------------


def test_vectors_file_exists():
    """A missing fixture must fail loudly. Skipping would silently retire the
    only check that the two implementations still agree."""
    assert VECTORS.is_file(), f"conformance vectors missing at {VECTORS}"


@pytest.mark.parametrize("index", range(len(_load_vectors()["cases"])))
def test_conformance_vector(index: int):
    data = _load_vectors()
    case = data["cases"][index]
    world = build_world(data["worlds"][case["world"]])
    start = case["start"]
    player = PlayerState(
        x=start["x"],
        y=start["y"],
        z=start["z"],
        vel_z=start.get("vel_z", 0.0),
        yaw=start.get("yaw", 0.0),
        pitch=start.get("pitch", 0.0),
        on_ground=start.get("on_ground", False),
    )
    for raw in case["steps"]:
        if "yaw" in raw:
            player.yaw = raw["yaw"]
        step(
            world,
            player,
            MoveInput(
                forward=raw.get("forward", 0.0),
                strafe=raw.get("strafe", 0.0),
                jump=raw.get("jump", False),
            ),
            raw["dt"],
        )
    expect = case["expect"]
    tol = data["tolerance"]
    assert player.x == pytest.approx(expect["x"], abs=tol), case["name"]
    assert player.y == pytest.approx(expect["y"], abs=tol), case["name"]
    assert player.z == pytest.approx(expect["z"], abs=tol), case["name"]
    assert player.vel_z == pytest.approx(expect["velZ"], abs=tol), case["name"]
    assert player.on_ground == expect["onGround"], case["name"]

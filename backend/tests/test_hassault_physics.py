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
import random
from pathlib import Path

import pytest

from backend.modules.hassault.cgz import FHF, SOLID, SPACE
from backend.modules.hassault.weapons import (
    BODY_HEIGHT,
    aim_vector,
    FACE_NONE,
    FACE_NORMALS,
    FACE_NX,
    FACE_NY,
    FACE_NZ,
    FACE_PX,
    FACE_PY,
    FACE_PZ,
    ray_hits_body,
    raycast_world,
    raycast_world_face,
)
from backend.modules.hassault import weapons
from backend.modules.hassault.physics import (
    CROUCH_HEIGHT,
    LADDER_ENTITY,
    NO_WATER,
    CROUCH_SPEED_SCALE,
    FALL_SAFE_SPEED,
    JUMP_CHAIN_BOOST,
    JUMP_SPEED,
    MOVE_SPEED,
    PLAYER_ABOVE_EYE,
    PLAYER_EYE_HEIGHT,
    PLAYER_RADIUS,
    STANDING_HEIGHT,
    STEP_HEIGHT,
    MoveInput,
    PlayerState,
    SWIM_SPEED,
    WATER_SPEED_SCALE,
    World,
    apply_impulse,
    in_water,
    ladders_from,
    submerged,
    body_height,
    can_stand,
    eye_height,
    fall_damage,
    flat_world,
    spawn_at,
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
    world = World(
        ssize=ssize,
        type=bytes(types),
        floor=bytes(floor),
        ceil=bytes(ceil),
        vdelta=bytes(vdelta),
        waterlevel=float(spec.get("waterlevel", NO_WATER)),
    )
    # Ladders go in as *entities* and are resolved by the same `ladders_from` the
    # map pipeline uses, so the fixture exercises that derivation on both sides
    # rather than handing each a pre-computed span.
    world.ladders = ladders_from(
        ssize,
        world.floor_at,
        [
            _LadderEntity(led["x"], led["y"], led["height"])
            for led in spec.get("ladders", [])
        ],
    )
    return world


class _LadderEntity:
    """The three fields `ladders_from` reads off a map entity."""

    def __init__(self, x: int, y: int, height: int) -> None:
        self.type = LADDER_ENTITY
        self.x = x
        self.y = y
        self.attr1 = height


class _Spawn:
    """The four fields `spawn_at` reads off a `playerstart` entity.

    Mirrored by the literal the vitest file passes, and by `Spawn` in
    `scripts/gen_hassault_vectors.py`.
    """

    def __init__(self, x: float, y: float, z: float, yaw: float | None = 0.0) -> None:
        self.x = x
        self.y = y
        self.z = z
        self.yaw = yaw


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


def _speed(player: PlayerState) -> float:
    return math.hypot(player.vel_x, player.vel_y)


def _run(world, player, frames: int, **kw) -> None:
    for _ in range(frames):
        step(world, player, MoveInput(**kw), 1 / 60)


# ---------------------------------------------------------------------------
# Momentum
# ---------------------------------------------------------------------------


def test_running_converges_on_the_speed_cap_and_no_further():
    world = flat_world(64, floor=0, ceil=24)
    player = PlayerState(x=8.0, y=8.0, z=0.0, on_ground=True)
    _run(world, player, 120, forward=1.0)
    assert _speed(player) == pytest.approx(MOVE_SPEED, abs=1e-3)


def test_diagonal_movement_is_not_faster_than_straight():
    """The wish direction is normalised. Diagonal overspeed is the *accidental*
    version of a movement tech, and this game has a deliberate one."""
    world = flat_world(64, floor=0, ceil=24)
    straight = PlayerState(x=8.0, y=8.0, z=0.0, on_ground=True)
    diagonal = PlayerState(x=8.0, y=8.0, z=0.0, on_ground=True)
    _run(world, straight, 120, forward=1.0)
    _run(world, diagonal, 120, forward=1.0, strafe=1.0)
    assert _speed(diagonal) == pytest.approx(_speed(straight), abs=1e-6)


def test_air_control_is_much_weaker_than_ground_control():
    """The whole reason movement is velocity-based: in the air, momentum decides
    where you land, not the keys."""
    world = flat_world(64, floor=0, ceil=64)
    ground = PlayerState(x=8.0, y=8.0, z=0.0, vel_x=MOVE_SPEED, on_ground=True)
    air = PlayerState(x=8.0, y=8.0, z=30.0, vel_x=MOVE_SPEED, on_ground=False)
    # Both ask to stop. The grounded one does; the airborne one barely notices.
    _run(world, ground, 12)
    _run(world, air, 12)
    assert ground.vel_x < MOVE_SPEED * 0.25
    assert air.vel_x > MOVE_SPEED * 0.7
    # Stated as a ratio too, because that is the mechanic: the exact fraction is
    # an exponential of two constants, but "air control is several times weaker"
    # is the thing a change must not quietly undo.
    assert air.vel_x > ground.vel_x * 3


def test_gravity_ramps_with_time_in_air():
    """AC's `dropf` grows with `timeinair`, so a fall comes down harder than the
    jump went up. Without the ramp the two halves of a second would be equal."""
    world = flat_world(16, floor=0, ceil=120)
    player = PlayerState(x=8.0, y=8.0, z=110.0)
    _run(world, player, 30)
    first_half = 110.0 - player.z
    before = player.z
    _run(world, player, 30)
    second_half = before - player.z
    assert second_half > first_half * 1.5


# ---------------------------------------------------------------------------
# The chained-jump boost
# ---------------------------------------------------------------------------


def test_chained_strafing_hops_exceed_the_run_cap_but_are_capped():
    world = flat_world(96, floor=0, ceil=24)
    player = PlayerState(x=12.0, y=12.0, z=0.0, yaw=0.6, on_ground=True)
    _run(world, player, 30, forward=1.0, strafe=1.0)
    peak = 0.0
    for _ in range(180):
        step(world, player, MoveInput(forward=1.0, strafe=1.0, jump=True), 1 / 60)
        peak = max(peak, _speed(player))
    assert peak > MOVE_SPEED * 1.05
    # AC's `1.25/max(speed/fullspeed, 1)` is a clamp above the cap, not a
    # multiplier that compounds — so no amount of chaining passes 125%.
    assert peak == pytest.approx(MOVE_SPEED * JUMP_CHAIN_BOOST, abs=1e-6)


def test_the_boost_needs_strafe():
    """Straight-line hopping is not a movement tech. Without this the boost is
    just "hold jump", which is not a skill."""
    world = flat_world(96, floor=0, ceil=24)
    player = PlayerState(x=12.0, y=12.0, z=0.0, on_ground=True)
    _run(world, player, 30, forward=1.0)
    peak = 0.0
    for _ in range(180):
        step(world, player, MoveInput(forward=1.0, jump=True), 1 / 60)
        peak = max(peak, _speed(player))
    assert peak <= MOVE_SPEED + 1e-6


def test_a_standing_jump_earns_no_boost():
    """The window is measured from a *landing*. A body resting on the floor dips
    below it under gravity every frame, and treating that as a landing would
    reset the window continuously — making the timing free."""
    world = flat_world(96, floor=0, ceil=24)
    player = PlayerState(x=12.0, y=12.0, z=0.0, on_ground=True)
    _run(world, player, 120, forward=1.0, strafe=1.0)  # long since any landing
    before = _speed(player)
    step(world, player, MoveInput(forward=1.0, strafe=1.0, jump=True), 1 / 60)
    assert _speed(player) <= before + 1e-6


def test_standing_still_never_reports_a_landing():
    """`fall_speed` is an output of one step. A resting body must not report an
    impact, or standing on the floor would cost health continuously."""
    world = flat_world(16, floor=0, ceil=24)
    player = PlayerState(x=8.0, y=8.0, z=0.0, on_ground=True)
    for _ in range(60):
        step(world, player, MoveInput(), 1 / 60)
        assert player.fall_speed == 0.0


# ---------------------------------------------------------------------------
# Crouching
# ---------------------------------------------------------------------------


def test_crouching_shortens_the_body_and_the_eye():
    world = flat_world(16, floor=0, ceil=24)
    player = PlayerState(x=8.0, y=8.0, z=0.0, on_ground=True)
    assert body_height(player) == pytest.approx(STANDING_HEIGHT)
    _run(world, player, 30, crouch=True)
    assert player.crouch == pytest.approx(1.0)
    assert body_height(player) == pytest.approx(CROUCH_HEIGHT)
    assert eye_height(player) < PLAYER_EYE_HEIGHT


def test_crouching_on_the_ground_costs_speed():
    world = flat_world(64, floor=0, ceil=24)
    player = PlayerState(x=8.0, y=8.0, z=0.0, on_ground=True)
    _run(world, player, 120, forward=1.0, crouch=True)
    assert _speed(player) == pytest.approx(MOVE_SPEED * CROUCH_SPEED_SCALE, abs=1e-3)


def test_crouching_in_mid_air_does_not_cost_speed():
    """AC's `crouchedinair` exemption — what makes a crouch-jump a way to clear a
    gap rather than a way to fall short of it."""
    world = flat_world(64, floor=0, ceil=64)
    player = PlayerState(x=8.0, y=8.0, z=0.0, on_ground=True)
    _run(world, player, 60, forward=1.0)
    step(world, player, MoveInput(forward=1.0, jump=True), 1 / 60)
    _run(world, player, 20, forward=1.0, crouch=True)
    assert player.crouch == pytest.approx(1.0)
    assert _speed(player) > MOVE_SPEED * 0.95


def test_a_crouched_body_fits_where_a_standing_one_does_not():
    world = flat_world(16, floor=0, ceil=5)
    assert STANDING_HEIGHT > 5 > CROUCH_HEIGHT
    assert not can_stand(world, 8.0, 8.0, 0.0, STANDING_HEIGHT)
    assert can_stand(world, 8.0, 8.0, 0.0, CROUCH_HEIGHT)


def test_you_cannot_stand_up_under_a_low_ceiling():
    """Releasing crouch with no headroom has to be refused, or the body pops
    through the roof — and then `_support` shoves it back down forever."""
    world = flat_world(16, floor=0, ceil=5)
    player = PlayerState(x=8.0, y=8.0, z=0.0, on_ground=True, crouch=1.0)
    _run(world, player, 60, crouch=False)
    assert player.crouch == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Impulses and fall damage
# ---------------------------------------------------------------------------


def test_an_upward_impulse_leaves_the_ground():
    """Clearing `on_ground` is the whole trick: without it the next step's
    vertical resolve lands the player again before the velocity moved them."""
    world = flat_world(16, floor=0, ceil=64)
    player = PlayerState(x=8.0, y=8.0, z=0.0, on_ground=True)
    apply_impulse(player, 0.0, 0.0, 14.0)
    assert player.on_ground is False
    _run(world, player, 10)
    assert player.z > 1.0


def test_a_shoot_jump_gains_height_a_plain_jump_cannot():
    world = flat_world(16, floor=0, ceil=120)
    plain = PlayerState(x=8.0, y=8.0, z=0.0, on_ground=True)
    boosted = PlayerState(x=8.0, y=8.0, z=0.0, on_ground=True)
    peak_plain = 0.0
    peak_boosted = 0.0
    step(world, plain, MoveInput(jump=True), 1 / 60)
    step(world, boosted, MoveInput(jump=True), 1 / 60)
    # Fired straight down at the top of the arc, which is where it pays best.
    apply_impulse(boosted, 0.0, 0.0, 12.0)
    for _ in range(120):
        step(world, plain, MoveInput(), 1 / 60)
        step(world, boosted, MoveInput(), 1 / 60)
        peak_plain = max(peak_plain, plain.z)
        peak_boosted = max(peak_boosted, boosted.z)
    assert peak_boosted > peak_plain * 1.5


def test_a_flat_jump_never_costs_health():
    """The threshold has to sit above what ordinary movement produces, or the
    game charges you for playing it."""
    world = flat_world(16, floor=0, ceil=64)
    player = PlayerState(x=8.0, y=8.0, z=0.0, on_ground=True)
    step(world, player, MoveInput(jump=True), 1 / 60)
    worst = 0.0
    for _ in range(120):
        step(world, player, MoveInput(), 1 / 60)
        worst = max(worst, player.fall_speed)
    assert worst > 0.0  # it did land
    assert fall_damage(worst) == 0.0


def test_a_long_drop_costs_health_once():
    world = flat_world(16, floor=0, ceil=120)
    player = PlayerState(x=8.0, y=8.0, z=100.0)
    impacts = []
    for _ in range(240):
        step(world, player, MoveInput(), 1 / 60)
        if player.fall_speed > 0:
            impacts.append(player.fall_speed)
    # Exactly one landing, and it hurt.
    assert len(impacts) == 1
    assert impacts[0] > FALL_SAFE_SPEED
    assert fall_damage(impacts[0]) > 0.0


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
        vel_x=start.get("vel_x", 0.0),
        vel_y=start.get("vel_y", 0.0),
        vel_z=start.get("vel_z", 0.0),
        yaw=start.get("yaw", 0.0),
        pitch=start.get("pitch", 0.0),
        on_ground=start.get("on_ground", False),
        crouch=start.get("crouch", 0.0),
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
                crouch=raw.get("crouch", False),
            ),
            raw["dt"],
        )
        # After the step, which is where the match server applies weapon recoil
        # (`simulate` steps, then `_handle_combat` fires).
        if "impulse" in raw:
            apply_impulse(player, *raw["impulse"])
    expect = case["expect"]
    tol = data["tolerance"]
    assert player.x == pytest.approx(expect["x"], abs=tol), case["name"]
    assert player.y == pytest.approx(expect["y"], abs=tol), case["name"]
    assert player.z == pytest.approx(expect["z"], abs=tol), case["name"]
    assert player.vel_x == pytest.approx(expect["velX"], abs=tol), case["name"]
    assert player.vel_y == pytest.approx(expect["velY"], abs=tol), case["name"]
    assert player.vel_z == pytest.approx(expect["velZ"], abs=tol), case["name"]
    assert player.crouch == pytest.approx(expect["crouch"], abs=tol), case["name"]
    assert player.on_ground == expect["onGround"], case["name"]


@pytest.mark.parametrize("index", range(len(_load_vectors()["spawns"])))
def test_conformance_spawn(index: int):
    """Spawn placement, replayed by the vitest file too.

    It lives in the fixture for the same reason `step` does: it is one rule with
    two implementations, and a disagreement about where a player starts is a
    desync from the very first frame.
    """
    data = _load_vectors()
    case = data["spawns"][index]
    world = build_world(data["worlds"][case["world"]])
    entity = case["entity"]
    placed = spawn_at(
        world, _Spawn(entity["x"], entity["y"], entity["z"], entity.get("yaw", 0.0))
    )
    expect = case["expect"]
    tol = data["tolerance"]
    assert placed.x == pytest.approx(expect["x"], abs=tol), case["name"]
    assert placed.y == pytest.approx(expect["y"], abs=tol), case["name"]
    assert placed.z == pytest.approx(expect["z"], abs=tol), case["name"]
    assert placed.yaw == pytest.approx(expect["yaw"], abs=tol), case["name"]
    assert placed.on_ground == expect["onGround"], case["name"]


@pytest.mark.parametrize("index", range(len(_load_vectors()["traces"])))
def test_conformance_shot_trace(index: int):
    """The shot DDA, replayed by the vitest file too.

    `trace.ts` exists because the training range has no server to ask where a
    shot stopped, which makes shot geometry a third rule with two
    implementations. An off-by-one on a cell boundary there stops shots a
    fraction early and reports nothing at all, so it is pinned here.
    """
    data = _load_vectors()
    case = data["traces"][index]
    world = build_world(data["worlds"][case["world"]])
    direction = aim_vector(case["yaw"], case["pitch"])
    origin = (case["origin"][0], case["origin"][1], case["origin"][2])
    distance, face = raycast_world_face(
        world, origin, direction, case["max_distance"]
    )
    assert distance == pytest.approx(case["expect"], abs=data["tolerance"]), case[
        "name"
    ]
    # The face is on the wire and both clients orient a bullet mark from it. A
    # port that got the sign backwards draws every mark on the *inside* of the
    # wall it hit, which is invisible and reports nothing — so it is pinned here
    # rather than left to be noticed.
    #
    # `None` is a case the generator found genuinely ambiguous — a shot into a
    # cell corner, where the answer is a coin flip decided by whose `cos` ran.
    if case["face"] is not None:
        assert face == case["face"], case["name"]


def _boxed_room(ssize: int = 24, ceil: int = 16) -> World:
    """An open room inside solid rock, the shape `build_world` makes.

    Two cells of margin on every side, so a shot in any direction has a wall to
    find and the border the engine guarantees stays intact.
    """
    return build_world(
        {
            "ssize": ssize,
            "rects": [
                {
                    "x0": 2,
                    "y0": 2,
                    "x1": ssize - 3,
                    "y1": ssize - 3,
                    "type": SPACE,
                    "floor": 0,
                    "ceil": ceil,
                }
            ],
        }
    )


def test_the_face_is_the_normal_pointing_back_at_the_shooter():
    """The one thing about a face index that is easy to get exactly backwards.

    A mark drawn on the wrong side of its wall is inside the wall, so the failure
    has no symptom at all — it looks like the decals were never implemented.
    """
    world = _boxed_room()
    origin = (12.0, 12.0, 4.0)

    # East into the far wall: its west face, whose normal points back west at us.
    _, face = raycast_world_face(world, origin, aim_vector(0.0, 0.0), 60.0)
    assert face == FACE_NX
    assert FACE_NORMALS[face] == (-1.0, 0.0, 0.0)

    # West: the opposite wall's east face.
    _, face = raycast_world_face(world, origin, aim_vector(math.pi, 0.0), 60.0)
    assert face == FACE_PX

    # North and south, so all four side faces are covered rather than assumed
    # by symmetry.
    _, face = raycast_world_face(world, origin, aim_vector(math.pi / 2, 0.0), 60.0)
    assert face == FACE_NY
    _, face = raycast_world_face(world, origin, aim_vector(-math.pi / 2, 0.0), 60.0)
    assert face == FACE_PY

    # Down onto the floor: the floor faces up.
    _, face = raycast_world_face(world, origin, aim_vector(0.0, -math.pi / 2), 60.0)
    assert face == FACE_PZ
    assert FACE_NORMALS[face] == (0.0, 0.0, 1.0)

    # Up into the ceiling: it faces down.
    _, face = raycast_world_face(world, origin, aim_vector(0.0, math.pi / 2), 60.0)
    assert face == FACE_NZ


def test_a_shot_with_nothing_to_stop_it_claims_no_face():
    """Reaching the range limit is not hitting anything.

    `FACE_NONE` is negative rather than a sixth value precisely so a client that
    forgets to check cannot index a table with it and quietly draw a mark facing
    `+x` in mid-air.
    """
    world = _boxed_room()
    distance, face = raycast_world_face(
        world, (12.0, 12.0, 4.0), aim_vector(0.0, 0.0), 3.0
    )
    assert distance == pytest.approx(3.0)
    assert face == FACE_NONE
    assert face < 0
    assert all(0 <= f < len(FACE_NORMALS) for f in range(len(FACE_NORMALS)))


def test_a_muzzle_inside_geometry_claims_no_face():
    """Standing in a wall: the shot stops at zero, and there is no surface it
    arrived at from anywhere."""
    world = _boxed_room()
    # (0.5, 0.5) is inside the border the room is carved out of.
    distance, face = raycast_world_face(
        world, (0.5, 0.5, 4.0), aim_vector(0.0, 0.0), 60.0
    )
    assert distance == 0.0
    assert face == FACE_NONE


def test_every_pellet_reports_its_own_face():
    """Parallel to `endpoints`, not filtered.

    The case that matters is the shotgun that lands one pellet in a body and the
    rest in the wall behind it: a compacted list loses which was which, and the
    debris for those wall hits used to disappear entirely because the *shot*
    counted as a hit.
    """
    # Close to the wall on purpose: a shotgun's range is short, and pellets that
    # simply ran out of it would report `FACE_NONE` for a reason unrelated to
    # what this is testing.
    world = _boxed_room(ssize=24)
    shotgun = weapons.WEAPON_BY_ID["shotgun"]
    result = weapons.resolve_shot(
        world,
        shotgun,
        (12.0, 12.0, 4.0),
        aim_vector(0.0, 0.0),
        {},
        random.Random(7),
    )
    assert len(result.faces) == len(result.endpoints) == shotgun.pellets
    # Every pellet went into the same wall, so every face is the same one.
    assert set(result.faces) == {FACE_NX}


def test_a_pellet_that_stops_in_a_body_has_no_face():
    """A body is not a surface. The wall behind it is still there, but the pellet
    never reached it, and a mark on it would be a lie about where the shot went."""
    world = _boxed_room(ssize=64)
    sniper = weapons.WEAPON_BY_ID["sniper"]
    result = weapons.resolve_shot(
        world,
        sniper,
        (12.0, 32.0, 4.0),
        aim_vector(0.0, 0.0),
        {"victim": (24.0, 32.0, 0.0)},
        random.Random(3),
    )
    assert result.hits, "the test shot was supposed to connect"
    assert result.faces == [FACE_NONE]


@pytest.mark.parametrize("index", range(len(_load_vectors()["sprays"])))
def test_conformance_spray(index: int):
    """The recoil pattern's *application*, replayed by all three clients.

    The offsets themselves are served, so there is one copy of them by
    construction. What can drift is what each port does with one — and the
    mistake that matters is silent: the table is absolute and the camera
    accumulates, so applying the absolute walks the crosshair away by the running
    sum and reads as a tuning problem rather than as a bug.
    """
    data = _load_vectors()
    case = data["sprays"][index]
    weapon = weapons.WEAPON_BY_ID[case["weapon"]]
    offset = weapons.spray_offset(weapon, case["index"])
    yaw, pitch = weapons.apply_spray(case["yaw"], case["pitch"], offset)
    expect = case["expect"]
    tol = data["tolerance"]
    assert offset[0] == pytest.approx(expect["offset"][0], abs=tol), case["name"]
    assert offset[1] == pytest.approx(expect["offset"][1], abs=tol), case["name"]
    assert yaw == pytest.approx(expect["yaw"], abs=tol), case["name"]
    assert pitch == pytest.approx(expect["pitch"], abs=tol), case["name"]
    cone = weapons.residual_spread(weapon, case.get("scoped", 0))
    assert cone == pytest.approx(expect["cone"], abs=tol), case["name"]


def test_the_pattern_holds_at_its_last_entry_rather_than_wrapping():
    """A pattern that restarted mid-magazine would be unlearnable, which defeats
    the only reason it is a pattern."""
    rifle = weapons.WEAPON_BY_ID["assault"]
    last = weapons.spray_offset(rifle, len(rifle.spray) - 1)
    assert weapons.spray_offset(rifle, len(rifle.spray)) == last
    assert weapons.spray_offset(rifle, 10_000) == last
    # And a negative index is the first shot, not the end of the table.
    assert weapons.spray_offset(rifle, -3) == rifle.spray[0]


def test_the_pattern_covers_a_whole_magazine():
    """Twenty rounds and twenty entries, so a full spray never has to hold."""
    rifle = weapons.WEAPON_BY_ID["assault"]
    assert len(rifle.spray) >= rifle.mag


def test_only_the_automatic_weapon_has_a_pattern():
    """A pattern on a 62 rpm sniper never survives its own reset gate, and one on
    an 8-pellet shotgun would be fighting a cone twenty times its size."""
    for weapon in weapons.WEAPONS:
        if weapon.spray:
            assert weapon.auto, f"{weapon.id} has a pattern but is not automatic"


def test_the_residual_cone_is_smaller_but_never_zero():
    """Zero makes the rifle a laser at two hundred cubes and erases the sniper. A
    residual is what keeps a pattern counterable rather than solved."""
    rifle = weapons.WEAPON_BY_ID["assault"]
    cone = weapons.residual_spread(rifle)
    assert 0 < cone < rifle.spread


def test_a_weapon_with_no_pattern_behaves_exactly_as_before():
    """Four of the five weapons are untouched by any of this."""
    for weapon in weapons.WEAPONS:
        if weapon.spray:
            continue
        assert weapons.spray_offset(weapon, 7) == (0.0, 0.0)
        assert weapons.apply_spray(0.4, 0.1, weapons.spray_offset(weapon, 7)) == (
            0.4,
            0.1,
        )
        for scoped in (0, 1, 2):
            assert weapons.residual_spread(weapon, scoped) == weapons.effective_spread(
                weapon, scoped
            )


def test_apply_spray_cannot_flip_the_aim_over_the_pole():
    """A real pattern is a small climb and can never reach vertical, but a table
    edited to something silly should bend the aim rather than invert it."""
    _, pitch = weapons.apply_spray(0.0, 1.5, (0.0, 5.0))
    assert pitch < math.pi / 2
    _, pitch = weapons.apply_spray(0.0, -1.5, (0.0, -5.0))
    assert pitch > -math.pi / 2


@pytest.mark.parametrize("index", range(len(_load_vectors()["bodies"])))
def test_conformance_body_hit(index: int):
    """The cylinder test, replayed by the vitest file too.

    A `None` expectation is a clean miss and is asserted as one: a port that
    returned 0.0 instead would read as a point-blank hit on every shot that
    should have missed, which is the exact opposite of the bug and just as quiet.
    """
    data = _load_vectors()
    case = data["bodies"][index]
    direction = aim_vector(case["yaw"], case["pitch"])
    origin = (case["origin"][0], case["origin"][1], case["origin"][2])
    feet = (case["feet"][0], case["feet"][1], case["feet"][2])
    hit = ray_hits_body(origin, direction, feet, height=case.get("height", BODY_HEIGHT))
    if case["expect"] is None:
        assert hit is None, case["name"]
    else:
        assert hit is not None, case["name"]
        assert hit == pytest.approx(case["expect"], abs=data["tolerance"]), case["name"]


# ---------------------------------------------------------------------------
# Where a spawn entity actually puts you
# ---------------------------------------------------------------------------


def test_the_feet_land_on_the_ground_however_high_the_entity_sits():
    """A `playerstart`'s `z` is the mapper's *eye* at placement time, and AC's
    editor flies — so it is not a ground height and cannot be used as one. Read
    as a lower bound (`max(floor, z)`) it put all 1741 official spawns in
    mid-air, because it is above the floor at all but six of them."""
    world = flat_world(32, floor=3, ceil=24)
    for entity_z in (-20, 0, 3, 7, 40):
        placed = spawn_at(world, _Spawn(12, 12, entity_z))
        assert placed.z == pytest.approx(3.0), f"entity z {entity_z}"


def test_a_spawn_is_the_fixed_point_of_the_first_simulated_step():
    """The whole point of resolving against `_support`: simulating a spawned
    player must not move them. A drop on every spawn and respawn is what the old
    behaviour looked like from inside the game."""
    world = flat_world(32, floor=5, ceil=24)
    placed = spawn_at(world, _Spawn(12, 12, 17))
    before = (placed.x, placed.y, placed.z)
    step(world, placed, MoveInput(), 1 / 60)
    assert (placed.x, placed.y, placed.z) == pytest.approx(before)
    assert placed.on_ground is True


def test_a_spawn_puts_the_eye_under_the_ceiling():
    """The old placement could leave the eye *above* the cell ceiling — on
    ac_desert, feet at 12 with a ceiling of 16 puts the eye at 16.5 — which makes
    `raycast_world` report an immediate block for anything fired on that frame."""
    world = flat_world(32, floor=0, ceil=16)
    placed = spawn_at(world, _Spawn(12, 12, 12))
    assert placed.z + PLAYER_EYE_HEIGHT < world.ceil_at(12, 12)


def test_a_spawn_stands_on_the_highest_floor_under_the_body():
    """The body is 2.2 cubes wide, so it can straddle a step. `_support` takes
    the highest floor beneath it — the same rule `step` resolves against, which
    is what stops the two disagreeing."""
    n = 32 * 32
    types = bytearray([SPACE]) * n
    floor = bytearray(n)
    for y in range(32):
        for x in range(16, 32):
            floor[y * 32 + x] = 2
    world = World(
        ssize=32,
        type=bytes(types),
        floor=bytes(floor),
        ceil=bytes([24]) * n,
        vdelta=bytes(n),
    )
    # Centre at 15.5 with radius 1.1 reaches cell 16, which is two units up.
    assert spawn_at(world, _Spawn(15, 8, 40)).z == pytest.approx(2.0)


def test_spawn_yaw_is_converted_from_degrees():
    world = flat_world(32)
    assert spawn_at(world, _Spawn(12, 12, 0, yaw=90.0)).yaw == pytest.approx(
        math.pi / 2
    )
    assert spawn_at(world, _Spawn(12, 12, 0, yaw=None)).yaw == pytest.approx(0.0)


def test_a_spawn_sealed_in_solid_geometry_still_places_the_player():
    """No official map manages it, but a community one might, and refusing to
    place anyone would turn an odd map into an unjoinable one."""
    n = 16 * 16
    world = World(
        ssize=16,
        type=bytes([SOLID]) * n,
        floor=bytes([7]) * n,
        ceil=bytes([16]) * n,
        vdelta=bytes(n),
    )
    assert spawn_at(world, _Spawn(8, 8, 30)).z == pytest.approx(7.0)


# ---- water ------------------------------------------------------------------------


def _pool(waterlevel: float, ssize: int = 32) -> World:
    """A flat room with a water plane over it."""
    world = flat_world(ssize, floor=0, ceil=32)
    world.waterlevel = waterlevel
    return world


def _walk(world: World, player: PlayerState, steps: int, **kw) -> None:
    for _ in range(steps):
        step(world, player, MoveInput(dt=1 / 60, **kw), 1 / 60)


def test_water_is_read_at_the_feet_and_swimming_at_the_eye():
    """The two states differ across the eye line, not the feet: a body with its
    head out has footing, sight and a jump, and one with its head under has
    none of them."""
    world = _pool(3.0)
    player = PlayerState(x=8.0, y=8.0, z=0.0)
    assert in_water(world, player)
    assert not submerged(world, player)

    world.waterlevel = 6.0
    assert submerged(world, player)


def test_wading_is_slower_than_walking():
    dry = PlayerState(x=8.0, y=8.0, z=0.0, on_ground=True)
    wet = PlayerState(x=8.0, y=8.0, z=0.0, on_ground=True)
    _walk(flat_world(32, floor=0, ceil=32), dry, 60, forward=1.0)
    _walk(_pool(3.0), wet, 60, forward=1.0)
    assert wet.x < dry.x
    assert math.hypot(wet.vel_x, wet.vel_y) == pytest.approx(
        MOVE_SPEED * WATER_SPEED_SCALE, abs=0.5
    )


def test_a_crouched_swimmer_is_not_penalised_twice():
    """Water replaces the crouch scale rather than multiplying with it. Stacked,
    a crouched swimmer would move at a quarter of walking pace."""
    world = _pool(9.0)
    player = PlayerState(x=8.0, y=8.0, z=0.0, on_ground=True)
    _walk(world, player, 90, forward=1.0, crouch=True)
    assert math.hypot(player.vel_x, player.vel_y) == pytest.approx(
        MOVE_SPEED * WATER_SPEED_SCALE, abs=0.5
    )


def test_jump_is_the_swim_control_when_submerged():
    """Standing on the bottom of deep water, `jump` must not fire a full jump —
    the difference between the two readings is nineteen cubes a second."""
    world = _pool(9.0)
    player = PlayerState(x=8.0, y=8.0, z=0.0, on_ground=True)
    step(world, player, MoveInput(jump=True, dt=1 / 60), 1 / 60)
    assert player.vel_z < SWIM_SPEED
    assert player.vel_z != pytest.approx(JUMP_SPEED)


def test_a_swimmer_rises_holding_jump_and_dives_holding_crouch():
    world = _pool(20.0)
    up = PlayerState(x=8.0, y=8.0, z=6.0)
    down = PlayerState(x=8.0, y=8.0, z=6.0)
    _walk(world, up, 60, jump=True)
    _walk(world, down, 60, crouch=True)
    assert up.z > 6.0
    assert down.z < 6.0
    # Diving is slower than surfacing: nothing about water is a fast way to get
    # anywhere.
    assert (6.0 - down.z) < (up.z - 6.0)


def test_a_body_left_alone_in_water_sinks_slowly():
    world = _pool(20.0)
    swimmer = PlayerState(x=8.0, y=8.0, z=15.0)
    faller = PlayerState(x=8.0, y=8.0, z=15.0)
    _walk(world, swimmer, 30)
    _walk(flat_world(32, floor=0, ceil=32), faller, 30)
    assert swimmer.z < 15.0, "a neutral body would make water a place to get stuck"
    assert swimmer.z > faller.z


def test_water_takes_the_fall_out_of_a_long_drop():
    """The point of water. Dry, this landing is lethal."""
    world = _pool(12.0, ssize=32)
    world.ceil = bytes([120]) * (32 * 32)
    wet = PlayerState(x=8.0, y=8.0, z=100.0)
    for _ in range(600):
        step(world, wet, MoveInput(dt=1 / 60), 1 / 60)
        if wet.on_ground:
            break
    assert wet.on_ground
    assert fall_damage(wet.fall_speed) == 0.0


def test_shallow_water_helps_in_proportion_to_its_depth():
    """Water is not a switch. The drag only has as long as the body spends under
    the surface, so a puddle takes a little off a fall and a pool takes all of
    it — and an inch of water is not a total fall-damage negator, which would put
    shoot-jumping back on a free ride."""
    deep = _pool(12.0, ssize=32)
    deep.ceil = bytes([120]) * (32 * 32)
    shallow = _pool(0.5, ssize=32)
    shallow.ceil = bytes([120]) * (32 * 32)

    landings = []
    for world in (shallow, deep):
        player = PlayerState(x=8.0, y=8.0, z=100.0)
        for _ in range(600):
            step(world, player, MoveInput(dt=1 / 60), 1 / 60)
            if player.on_ground:
                break
        assert player.on_ground
        landings.append(player.fall_speed)

    shallow_impact, deep_impact = landings
    assert shallow_impact > 0.0, "an inch of water must not erase a lethal drop"
    assert deep_impact == 0.0


# ---- ladders ----------------------------------------------------------------------


def _climb_world(height: int = 20, ssize: int = 32) -> World:
    world = flat_world(ssize, floor=0, ceil=60)
    world.ladders = ladders_from(ssize, world.floor_at, [_LadderEntity(16, 16, height)])
    return world


def test_a_ladder_entity_becomes_a_span_resting_on_its_own_floor():
    world = flat_world(32, floor=6, ceil=60)
    (ladder,) = ladders_from(32, world.floor_at, [_LadderEntity(16, 16, 10)])
    assert (ladder.x, ladder.y) == (16.5, 16.5)
    assert (ladder.base, ladder.top) == (6.0, 16.0)


def test_a_ladder_with_no_height_is_dropped():
    """A mapper who never set the attribute meant "I did not finish this", and a
    ladder of unbounded height is a hole in the map's physics."""
    world = flat_world(32)
    assert ladders_from(32, world.floor_at, [_LadderEntity(16, 16, 0)]) == ()


def test_climbing_needs_the_body_to_be_facing_the_ladder():
    world = _climb_world()
    facing = PlayerState(x=15.5, y=16.5, z=0.0, yaw=0.0, on_ground=True)
    sideways = PlayerState(x=15.5, y=16.5, z=0.0, yaw=math.pi / 2, on_ground=True)
    _walk(world, facing, 30, forward=1.0)
    _walk(world, sideways, 30, forward=1.0)
    assert facing.z > 2.0
    # Running *past* a ladder must not launch you up it.
    assert sideways.z == pytest.approx(0.0)


def test_a_ladder_holds_you_where_you_are_with_no_input():
    world = _climb_world()
    player = PlayerState(x=15.5, y=16.5, z=8.0, yaw=0.0)
    _walk(world, player, 60)
    assert player.z == pytest.approx(8.0)


def test_pressing_back_descends_instead_of_walking_off():
    """The grip is what makes descending possible at all: without it the input
    that climbs down also walks you out of a two-cube volume."""
    world = _climb_world()
    player = PlayerState(x=15.5, y=16.5, z=12.0, yaw=0.0)
    _walk(world, player, 30, forward=-1.0)
    assert player.z < 12.0
    assert math.hypot(player.x - 15.5, player.y - 16.5) < 0.5


def test_the_climb_stops_at_the_top_rung():
    world = _climb_world(height=12)
    player = PlayerState(x=15.5, y=16.5, z=10.0, yaw=0.0)
    _walk(world, player, 120, forward=1.0)
    assert player.z <= 12.0 + 1e-9


def test_a_climber_never_accrues_air_time():
    """So stepping off the top falls at plain gravity rather than at whatever the
    ramp had reached, and a long climb is never charged as a fall."""
    world = _climb_world()
    player = PlayerState(x=15.5, y=16.5, z=0.0, yaw=0.0, on_ground=True)
    _walk(world, player, 60, forward=1.0)
    assert player.time_in_air == 0.0
    assert player.fall_speed == 0.0


def test_letting_go_at_the_top_does_not_ride_the_ladder_back_down():
    """Walking forward off the top carries the body across the ladder's centre,
    where "toward the ladder" reverses. Still attached, the input that was
    climbing up would start climbing down and take the player back to the
    bottom."""
    world = _climb_world(height=12)
    player = PlayerState(x=15.5, y=16.5, z=12.0, yaw=0.0)
    _walk(world, player, 40, forward=1.0)
    assert player.x > 16.5, "never crossed the ladder"
    assert player.z < 12.0, "rode the ladder instead of stepping off it"

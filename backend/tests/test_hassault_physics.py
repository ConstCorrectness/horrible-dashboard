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
    CROUCH_HEIGHT,
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
    World,
    apply_impulse,
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
    return World(
        ssize=ssize,
        type=bytes(types),
        floor=bytes(floor),
        ceil=bytes(ceil),
        vdelta=bytes(vdelta),
    )


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

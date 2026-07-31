"""Weapons, hit registration, and lag compensation.

Hermetic, like the rest of the hassault suite: every world here is a synthetic
`flat_world`, because AssaultCube content is copyright and cannot live in this
repo.

The tests that matter most are the ones pinning behaviour that is invisible when
it is wrong and infuriating when it is subtly wrong — the rewind clamp (an
unclamped one is a cheat, not a bug), cover (a body behind a wall is not a
target), and the fire-rate clock (gating on wall time silently halves a fast
weapon's rate, because commands arrive in batches).
"""

from __future__ import annotations

import math
import random
import time

import pytest

from backend.modules.hassault import weapons
from backend.modules.hassault.match import BUDGET_CEILING, Command, MatchRoom
from backend.modules.hassault.physics import PLAYER_EYE_HEIGHT, flat_world
from backend.modules.hassault.weapons import (
    MAX_REWIND_MS,
    PositionHistory,
    aim_vector,
    damage_at,
    ray_hits_body,
    raycast_world,
    resolve_shot,
    spread_vector,
)


class Spawn:
    """The two fields `physics.spawn_at` reads off a map entity."""

    def __init__(
        self, x: float, y: float, z: float = 0.0, yaw: float = 0.0, team: int = 0
    ) -> None:
        self.x = x
        self.y = y
        self.z = z
        self.yaw = yaw
        self.attr2 = team


def make_room(room_id: str = "w1") -> MatchRoom:
    world = flat_world(48, floor=0, ceil=16)
    return MatchRoom(
        room_id, "testmap", world, [Spawn(8, 8, team=0), Spawn(20, 20, team=1)]
    )


def place(player, x: float, y: float, z: float = 0.0, yaw: float = 0.0) -> None:
    player.state.x = x
    player.state.y = y
    player.state.z = z
    player.state.yaw = yaw
    player.state.pitch = 0.0
    # Spawn protection is granted on join; every test that shoots someone has to
    # get past it, and doing that explicitly is clearer than sleeping.
    player.protected_until = 0.0


def shot(
    seq: int, yaw: float, view_t: float | None = None, pitch: float = 0.0
) -> Command:
    return Command(
        seq=seq,
        forward=0.0,
        strafe=0.0,
        jump=False,
        yaw=yaw,
        pitch=pitch,
        dt=1 / 60,
        fire=True,
        view_t=view_t,
    )


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def test_aim_vector_matches_the_movement_convention():
    """A shot down the barrel has to leave along the same axis walking forward
    moves, or every weapon fires ninety degrees off what the player sees."""
    x, y, z = aim_vector(0.0, 0.0)
    assert (x, y, z) == pytest.approx((1.0, 0.0, 0.0))
    x, y, z = aim_vector(math.pi / 2, 0.0)
    assert (x, y, z) == pytest.approx((0.0, 1.0, 0.0), abs=1e-9)
    # Positive pitch looks up, matching the camera.
    assert aim_vector(0.0, math.pi / 4)[2] == pytest.approx(math.sqrt(0.5))


def test_a_ray_across_open_floor_reaches_its_range():
    world = flat_world(48)
    assert raycast_world(world, (10.0, 10.0, 3.0), (1.0, 0.0, 0.0), 20.0) == 20.0


def test_a_ray_stops_at_the_solid_border():
    """`flat_world` has a two-cell solid rim, and out of bounds counts as solid —
    the same rule the movement code leans on."""
    world = flat_world(48)
    distance = raycast_world(world, (10.0, 10.0, 3.0), (-1.0, 0.0, 0.0), 40.0)
    assert distance == pytest.approx(8.0)


def test_a_ray_stops_at_the_floor_and_the_ceiling():
    world = flat_world(48, floor=0, ceil=16)
    down = raycast_world(world, (10.0, 10.0, 4.0), (0.0, 0.0, -1.0), 40.0)
    assert down == pytest.approx(4.0)
    up = raycast_world(world, (10.0, 10.0, 4.0), (0.0, 0.0, 1.0), 40.0)
    assert up == pytest.approx(12.0)


def test_a_ray_that_never_leaves_its_cell_terminates():
    """Straight up in a room with no ceiling above the range: the DDA never
    crosses a boundary, so only the step bound ends the loop."""
    world = flat_world(48, floor=0, ceil=127)
    assert raycast_world(world, (10.0, 10.0, 4.0), (0.0, 0.0, 1.0), 20.0) == 20.0


def test_a_body_in_the_path_is_hit_and_one_beside_it_is_not():
    origin = (10.0, 10.0, PLAYER_EYE_HEIGHT)
    ahead = ray_hits_body(origin, (1.0, 0.0, 0.0), (20.0, 10.0, 0.0))
    assert ahead == pytest.approx(8.9, abs=0.05)  # 10 cubes minus the radius
    assert ray_hits_body(origin, (1.0, 0.0, 0.0), (20.0, 14.0, 0.0)) is None


def test_a_body_behind_the_shooter_is_not_hit():
    origin = (10.0, 10.0, PLAYER_EYE_HEIGHT)
    assert ray_hits_body(origin, (1.0, 0.0, 0.0), (2.0, 10.0, 0.0)) is None


def test_standing_inside_someone_is_a_point_blank_hit_not_a_miss():
    origin = (10.0, 10.0, PLAYER_EYE_HEIGHT)
    assert ray_hits_body(origin, (1.0, 0.0, 0.0), (10.0, 10.0, 0.0)) == 0.0


def test_a_shot_over_their_head_misses():
    origin = (10.0, 10.0, PLAYER_EYE_HEIGHT)
    up = aim_vector(0.0, 0.6)
    assert ray_hits_body(origin, up, (20.0, 10.0, 0.0)) is None


def test_damage_falls_off_with_distance_and_never_past_half():
    sniper = weapons.WEAPON_BY_ID["sniper"]
    assault = weapons.WEAPON_BY_ID["assault"]
    assert damage_at(assault, 10.0) == assault.damage
    assert damage_at(assault, assault.range) == pytest.approx(assault.damage * 0.5)
    assert damage_at(assault, 120.0) < assault.damage
    # A sniper's falloff starts at its range, so it never tapers at all.
    assert damage_at(sniper, sniper.range) == sniper.damage


def test_spread_stays_inside_its_cone_and_is_not_the_same_twice():
    rng = random.Random(7)
    base = (1.0, 0.0, 0.0)
    angles = []
    for _ in range(200):
        out = spread_vector(base, 0.075, rng)
        assert sum(v * v for v in out) == pytest.approx(1.0)
        angles.append(math.acos(max(-1.0, min(1.0, out[0]))))
    assert max(angles) <= 0.0751
    assert len(set(angles)) > 190


def test_zero_spread_is_left_exactly_alone():
    """A sniper rifle that wanders by a rounding error is a sniper rifle you
    cannot trust at 300 cubes."""
    rng = random.Random(1)
    assert spread_vector((0.0, 1.0, 0.0), 0.0, rng) == (0.0, 1.0, 0.0)


def test_a_shotgun_traces_one_ray_per_pellet():
    world = flat_world(48)
    result = resolve_shot(
        world,
        weapons.WEAPON_BY_ID["shotgun"],
        (10.0, 10.0, PLAYER_EYE_HEIGHT),
        (1.0, 0.0, 0.0),
        {},
        random.Random(3),
    )
    assert len(result.endpoints) == 8
    assert not result.hits


def test_cover_works_a_body_behind_a_wall_is_not_a_target():
    """The `<` in the wall comparison is the whole of cover. Without it every map
    is a shooting gallery with decorative geometry."""
    world = flat_world(48)
    # Straight into the solid rim; a body placed beyond it is out of bounds and
    # unreachable, which is exactly the case a wall makes.
    result = resolve_shot(
        world,
        weapons.WEAPON_BY_ID["sniper"],
        (10.0, 10.0, PLAYER_EYE_HEIGHT),
        (-1.0, 0.0, 0.0),
        {"victim": (0.5, 10.0, 0.0)},
        random.Random(1),
    )
    assert not result.hits


def test_a_hit_high_on_the_body_is_a_headshot():
    world = flat_world(48)
    origin = (10.0, 10.0, PLAYER_EYE_HEIGHT)
    result = resolve_shot(
        world,
        weapons.WEAPON_BY_ID["sniper"],
        origin,
        aim_vector(0.0, 0.0),
        {"victim": (20.0, 10.0, 0.0)},
        random.Random(1),
    )
    assert len(result.hits) == 1
    # Fired dead level from eye height at someone standing on the same floor, so
    # it lands at their eye — which is inside the head band.
    assert result.hits[0].head is True
    assert result.hits[0].damage > weapons.WEAPON_BY_ID["sniper"].damage


def test_a_hit_low_on_the_body_is_not_a_headshot():
    world = flat_world(48)
    result = resolve_shot(
        world,
        weapons.WEAPON_BY_ID["sniper"],
        (10.0, 10.0, PLAYER_EYE_HEIGHT),
        aim_vector(0.0, -0.35),
        {"victim": (16.0, 10.0, 0.0)},
        random.Random(1),
    )
    assert len(result.hits) == 1
    assert result.hits[0].head is False


# ---------------------------------------------------------------------------
# Lag compensation
# ---------------------------------------------------------------------------


def test_history_interpolates_between_recorded_frames():
    history = PositionHistory()
    history.record(1000.0, {"p": (0.0, 0.0, 0.0)})
    history.record(1100.0, {"p": (10.0, 0.0, 0.0)})
    assert history.rewind(1050.0)["p"][0] == pytest.approx(5.0)


def test_history_outside_its_range_holds_the_nearest_frame():
    history = PositionHistory()
    history.record(1000.0, {"p": (0.0, 0.0, 0.0)})
    history.record(1100.0, {"p": (10.0, 0.0, 0.0)})
    assert history.rewind(900.0)["p"][0] == 0.0
    assert history.rewind(9000.0)["p"][0] == 10.0


def test_an_empty_history_says_so_rather_than_guessing():
    """`None` means "resolve live" — with nothing recorded there is no better
    answer, and refusing the shot would be worse than taking it at face value."""
    assert PositionHistory().rewind(1000.0) is None


def test_history_is_trimmed_to_its_window():
    history = PositionHistory(seconds=1.0)
    for i in range(100):
        history.record(1000.0 + i * 50.0, {"p": (float(i), 0.0, 0.0)})
    assert len(history) <= 22


def test_the_rewind_target_is_clamped_and_that_is_the_security_boundary():
    """A client picks the instant its shot is judged at. Unclamped, it can pick
    one from ten seconds ago and shoot people where they used to be."""
    history = PositionHistory()
    now = 10_000.0
    assert history.clamp(now - 10_000.0, now) == now - MAX_REWIND_MS
    # Nor may it claim to be seeing the future, which would let it shoot at
    # extrapolated positions.
    assert history.clamp(now + 5_000.0, now) == now
    assert history.clamp(now - 80.0, now) == now - 80.0
    # No claim at all resolves live.
    assert history.clamp(None, now) == now
    assert history.clamp(float("nan"), now) == now


def test_a_shot_is_judged_where_the_shooter_saw_the_target():
    """The whole point of the rewind: the victim has already run out of the line
    of fire by the time the packet lands, and the shot still counts."""
    room = make_room()
    shooter = room.add("a", None, team=0)
    victim = room.add("b", None, team=1)
    place(shooter, 10.0, 10.0)
    place(victim, 24.0, 10.0)
    shooter.weapon = weapons.WEAPONS.index(weapons.WEAPON_BY_ID["sniper"])

    room.history.record(1000.0, {victim.id: (24.0, 10.0, 0.0)})
    room.history.record(1100.0, {victim.id: (24.0, 30.0, 0.0)})
    victim.state.y = 30.0  # long gone, on the server's clock

    room._fire(shooter, shot(1, yaw=0.0, view_t=1000.0), time.monotonic(), 1100.0)
    assert victim.health < weapons.MAX_HEALTH


def test_without_a_rewind_the_same_shot_misses():
    """The control for the test above — proof it is the rewind doing the work
    and not the shot being lucky."""
    room = make_room()
    shooter = room.add("a", None, team=0)
    victim = room.add("b", None, team=1)
    place(shooter, 10.0, 10.0)
    place(victim, 24.0, 30.0)
    shooter.weapon = weapons.WEAPONS.index(weapons.WEAPON_BY_ID["sniper"])

    room.history.record(1000.0, {victim.id: (24.0, 30.0, 0.0)})
    room.history.record(1100.0, {victim.id: (24.0, 30.0, 0.0)})

    room._fire(shooter, shot(1, yaw=0.0, view_t=1100.0), time.monotonic(), 1100.0)
    assert victim.health == weapons.MAX_HEALTH


# ---------------------------------------------------------------------------
# Combat through the match server
# ---------------------------------------------------------------------------


def fire_once(room: MatchRoom, shooter, seq: int, yaw: float = 0.0) -> None:
    room.enqueue(shooter, shot(seq, yaw))
    room.simulate(0.05)


def duel(room_id: str = "d1"):
    room = make_room(room_id)
    a = room.add("a", None, team=0)
    b = room.add("b", None, team=1)
    place(a, 10.0, 10.0)
    place(b, 20.0, 10.0)
    a.weapon = weapons.WEAPONS.index(weapons.WEAPON_BY_ID["assault"])
    return room, a, b


def test_firing_at_someone_damages_them_and_spends_a_round():
    room, a, b = duel()
    before = a.ammo[a.weapon]
    fire_once(room, a, 1)
    assert b.health < weapons.MAX_HEALTH
    assert a.ammo[a.weapon] == before - 1


def test_a_teammate_cannot_be_shot():
    room = make_room()
    a = room.add("a", None, team=0)
    b = room.add("b", None, team=0)
    place(a, 10.0, 10.0)
    place(b, 20.0, 10.0)
    fire_once(room, a, 1)
    assert b.health == weapons.MAX_HEALTH


def test_spawn_protection_absorbs_a_shot_and_is_forfeited_by_firing():
    room, a, b = duel()
    b.protected_until = time.monotonic() + 5.0
    fire_once(room, a, 1)
    assert b.health == weapons.MAX_HEALTH
    assert a.protected is False  # shooting gave up our own


def test_the_fire_rate_is_measured_in_simulated_time_not_wall_clock():
    """Commands arrive in batches, so several trigger pulls are consumed in one
    tick. Gating on real time would drop all but the first and silently halve a
    700 rpm rifle."""
    room, a, b = duel()
    interval = weapons.weapon_at(a.weapon).interval
    for seq in range(1, 6):
        room.enqueue(
            room.players[a.id],
            Command(
                seq=seq,
                forward=0.0,
                strafe=0.0,
                jump=False,
                yaw=0.0,
                pitch=0.0,
                dt=interval,
                fire=True,
            ),
        )
    # One tick. How many of those commands actually run is decided by the time
    # budget, not by the fire rate — which is the correct cap and the one the
    # movement tests already pin.
    expected = int(BUDGET_CEILING // interval)
    assert expected >= 2, "this test is only meaningful with room for two shots"
    room.simulate(1.0)
    assert a.ammo[a.weapon] == weapons.weapon_at(a.weapon).mag - expected


def test_pulling_the_trigger_faster_than_the_weapon_allows_is_ignored():
    room, a, b = duel()
    for seq in range(1, 11):
        room.enqueue(
            room.players[a.id],
            Command(
                seq=seq,
                forward=0.0,
                strafe=0.0,
                jump=False,
                yaw=0.0,
                pitch=0.0,
                dt=0.001,
                fire=True,
            ),
        )
    room.simulate(1.0)
    assert a.ammo[a.weapon] == weapons.weapon_at(a.weapon).mag - 1


def test_an_empty_magazine_starts_a_reload_rather_than_doing_nothing():
    room, a, _ = duel()
    a.ammo[a.weapon] = 0
    fire_once(room, a, 1)
    assert a.reload_until > a.sim_time


def test_a_reload_refills_from_the_reserve():
    room, a, _ = duel()
    weapon = weapons.weapon_at(a.weapon)
    a.ammo[a.weapon] = 3
    a.reserve[a.weapon] = 40
    room._begin_reload(a)
    a.sim_time += weapon.reload_time + 0.01
    room._finish_reload(a)
    assert a.ammo[a.weapon] == weapon.mag
    assert a.reserve[a.weapon] == 40 - (weapon.mag - 3)


def test_a_reload_with_an_empty_reserve_does_nothing():
    room, a, _ = duel()
    a.ammo[a.weapon] = 0
    a.reserve[a.weapon] = 0
    room._begin_reload(a)
    assert a.reload_until <= -900


def test_switching_weapons_cancels_a_reload():
    room, a, _ = duel()
    a.ammo[a.weapon] = 1
    room._begin_reload(a)
    assert a.reload_until > a.sim_time
    room._handle_combat(
        a,
        Command(
            seq=99,
            forward=0.0,
            strafe=0.0,
            jump=False,
            yaw=0.0,
            pitch=0.0,
            dt=0.016,
            weapon=1,
        ),
        time.monotonic(),
        time.time() * 1000,
    )
    assert a.weapon == 1
    assert a.reload_until <= -900


def test_enough_hits_kill_and_the_scoreboard_moves():
    room, a, b = duel()
    a.weapon = weapons.WEAPONS.index(weapons.WEAPON_BY_ID["sniper"])
    b.health = 20
    fire_once(room, a, 1)
    assert b.alive is False
    assert b.health == 0
    assert a.kills == 1
    assert b.deaths == 1
    assert room.scores[a.team] == 1
    assert any(fx["kind"] == "kill" for fx in room.fx)


def kill(player) -> None:
    """Put someone in the state a fatal hit leaves them in.

    The respawn deadline matters: `respawn_at` is only meaningful while dead, so
    a test that sets `alive = False` and nothing else describes a player the tick
    loop will revive on the spot.
    """
    player.alive = False
    player.health = 0
    player.respawn_at = time.monotonic() + 30.0


def test_a_dead_player_is_not_simulated_and_cannot_shoot_back():
    room, a, b = duel()
    kill(b)
    start = b.state.x
    room.enqueue(
        b,
        Command(
            seq=1,
            forward=1.0,
            strafe=0.0,
            jump=False,
            yaw=0.0,
            pitch=0.0,
            dt=0.05,
            fire=True,
        ),
    )
    ammo = b.ammo[b.weapon]
    room.simulate(0.05)
    assert b.state.x == start
    assert b.ammo[b.weapon] == ammo
    # The ack still moves: their client is predicting and needs to know what was
    # consumed, or it replays an ever-growing tail.
    assert b.ack == 1


def test_a_dead_player_is_not_a_target():
    room, a, b = duel()
    kill(b)
    fire_once(room, a, 1)
    assert b.health == 0
    assert a.kills == 0


def test_the_dead_respawn_on_the_wall_clock_and_come_back_whole():
    """On the wall clock deliberately: a dead player stops sending commands, so a
    respawn measured on their simulated time would never arrive."""
    room, a, b = duel()
    kill(b)
    b.respawn_at = time.monotonic() - 0.01
    room.simulate(0.05)
    assert b.alive is True
    assert b.health == weapons.MAX_HEALTH
    assert b.ammo[b.weapon] == weapons.weapon_at(b.weapon).mag


def test_a_hitmarker_reaches_the_shooter_and_only_the_shooter():
    room, a, b = duel()
    fire_once(room, a, 1)
    assert a.pending_hits and a.pending_hits[0]["victim"] == b.id
    assert not b.pending_hits


def test_the_private_view_drains_hitmarkers_so_they_are_shown_once():
    room, a, _ = duel()
    fire_once(room, a, 1)
    assert a.private_view(time.monotonic())["hits"]
    assert a.private_view(time.monotonic())["hits"] == []


def test_ammo_is_private_and_health_is_public():
    """Everyone needs to see a wounded enemy; nobody needs sixteen extra numbers
    per packet telling them how full your magazine is."""
    room, a, _ = duel()
    row = a.snapshot(time.monotonic())
    assert "hp" in row and "alive" in row
    assert "ammo" not in row
    assert "ammo" in a.private_view(time.monotonic())


def test_shots_are_batched_into_the_snapshot_rather_than_sent_as_they_happen():
    room, a, _ = duel()
    fire_once(room, a, 1)
    shots = [fx for fx in room.fx if fx["kind"] == "shot"]
    assert len(shots) == 1
    assert shots[0]["id"] == a.id
    assert shots[0]["hit"] is True
    rows = [p.snapshot(time.monotonic()) for p in room.players.values()]
    assert room.snapshot_for(a, time.time(), rows)["data"]["fx"] == room.fx


def test_a_nonsense_weapon_slot_is_clamped_not_obeyed():
    assert weapons.weapon_at(-5).id == weapons.WEAPONS[0].id
    assert weapons.weapon_at(9999).id == weapons.WEAPONS[-1].id


# -- the sniper's scope ------------------------------------------------------
#
# The scope is the one weapon mechanic whose *accuracy* half is server-side, so
# these pin the boundary rather than the feel: what a client is allowed to claim,
# and what the claim is worth once the server has clamped it.


def test_only_the_sniper_carries_a_scope():
    scoped = [w.id for w in weapons.WEAPONS if w.zoom_levels]
    assert scoped == ["sniper"]
    assert weapons.WEAPON_BY_ID["sniper"].zoom_levels == (2.0, 4.0)


def test_a_scoped_shot_uses_the_tight_cone_and_a_hip_shot_does_not():
    sniper = weapons.WEAPON_BY_ID["sniper"]
    assert weapons.effective_spread(sniper, 1) == sniper.spread
    assert weapons.effective_spread(sniper, 2) == sniper.spread
    assert weapons.effective_spread(sniper, 0) == sniper.hipfire_spread
    assert sniper.hipfire_spread > sniper.spread


def test_a_weapon_without_a_scope_aims_the_same_however_scoped_it_claims_to_be():
    """The clamp is what makes this true: switching from the sniper to the
    shotgun must not carry a zoom level onto a weapon that has no scope, or the
    shotgun's cone would silently collapse."""
    shotgun = weapons.WEAPON_BY_ID["shotgun"]
    assert weapons.clamp_zoom(shotgun, 2) == 0
    for claim in (0, 1, 2, 99):
        assert weapons.effective_spread(shotgun, claim) == shotgun.spread


def test_a_zoom_step_beyond_the_scope_is_clamped_not_obeyed():
    sniper = weapons.WEAPON_BY_ID["sniper"]
    assert weapons.clamp_zoom(sniper, 99) == 2
    assert weapons.clamp_zoom(sniper, -3) == 0


def test_the_scope_is_served_so_the_client_never_hardcodes_the_magnification():
    """`zoomLevels` divides both the client's FOV and its mouse sensitivity; a
    second copy in TypeScript is an aim that is wrong only while scoped."""
    served = weapons.WEAPON_BY_ID["sniper"].to_dict()
    assert served["zoomLevels"] == [2.0, 4.0]
    assert served["hipfireSpread"] == pytest.approx(0.055)
    knife = weapons.WEAPON_BY_ID["knife"].to_dict()
    assert knife["zoomLevels"] == []
    # Reported as a real number rather than null, so the client has one rule.
    assert knife["hipfireSpread"] == knife["spread"]


def test_a_hip_fired_sniper_actually_scatters_and_a_scoped_one_does_not():
    """The numbers reaching `resolve_shot`, not just the numbers on the dataclass:
    this is the wiring that a spread override could be plumbed past."""
    world = flat_world(32)
    sniper = weapons.WEAPON_BY_ID["sniper"]
    origin = (16.0, 16.0, PLAYER_EYE_HEIGHT)
    direction = aim_vector(0.0, 0.0)

    def ends(spread: float) -> list[tuple[float, float, float]]:
        rng = random.Random(7)
        return [
            weapons.resolve_shot(
                world, sniper, origin, direction, {}, rng, spread=spread
            ).endpoints[0]
            for _ in range(40)
        ]

    scoped = ends(weapons.effective_spread(sniper, 1))
    hip = ends(weapons.effective_spread(sniper, 0))
    # Compared against each other rather than against a magic constant: what
    # matters is that the hip shot is meaningfully looser, not its exact cone.
    scoped_spread_y = max(e[1] for e in scoped) - min(e[1] for e in scoped)
    hip_spread_y = max(e[1] for e in hip) - min(e[1] for e in hip)
    assert hip_spread_y > scoped_spread_y * 5


def test_the_wire_floors_a_zoom_claim_but_leaves_the_ceiling_to_the_weapon():
    """Mirrors the `view_t` split: the parser clamps what it can judge without
    knowing the simulation, and no more."""
    from backend.modules.hassault.channel import _parse_command

    base = {"seq": 1, "forward": 0, "strafe": 0, "yaw": 0, "pitch": 0, "dt": 0.016}
    assert _parse_command({**base, "scoped": 2}).scoped == 2
    assert _parse_command({**base, "scoped": -5}).scoped == 0
    assert _parse_command(base).scoped == 0
    # Not rejected here — `clamp_zoom` owns the ceiling, because only the
    # simulation knows which weapon this command lands on.
    assert _parse_command({**base, "scoped": 999}).scoped == 999


def test_the_route_actually_publishes_the_scope_not_just_the_dataclass():
    """The response model is a second gate, and it fails silently.

    `to_dict` carrying a field is not the same as the browser receiving it:
    `WeaponOut` is a `response_model`, so a field missing from *it* is dropped
    from the JSON with no error anywhere — the client then reads `undefined`,
    the scope never opens, and every test that only checked `to_dict` still
    passes. This asserts the wire, which is the thing the client actually gets.
    """
    from fastapi.testclient import TestClient

    from backend.app import app

    with TestClient(app) as client:
        res = client.get("/api/hassault/weapons")
        assert res.status_code == 200
        served = {w["id"]: w for w in res.json()}

    assert served["sniper"]["zoomLevels"] == [2.0, 4.0]
    assert served["sniper"]["hipfireSpread"] == pytest.approx(0.055)
    # And every other weapon reports a usable pair rather than a missing one.
    for wid, weapon in served.items():
        assert "zoomLevels" in weapon, wid
        assert "hipfireSpread" in weapon, wid
        if not weapon["zoomLevels"]:
            assert weapon["hipfireSpread"] == weapon["spread"], wid

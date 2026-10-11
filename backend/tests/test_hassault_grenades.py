"""Thrown utility: the throw, the bounce, and the four ways one resolves.

`weapons.TACTICALS` used to be a table of grenade numbers served over a route
with no simulation behind it — no throw, no fuse, no detonation. These tests
exist because the replacement has to be checked at the seams where a grenade
system is normally wrong:

- a blast that goes **through walls**, which is the single most common radius bug
- a flash that ignores which way somebody was **looking**, which makes the one
  counter every player knows do nothing
- a smoke that only **humans** respect, because the bots ask a different question
- utility counts and blindness leaking into the **shared** rows, which is the
  same wall-hack-in-the-packet mistake `noise.py` exists to avoid
"""

from __future__ import annotations

import math

import pytest

from backend.modules.hassault import grenades
from backend.modules.hassault.cgz import SOLID, SPACE
from backend.modules.hassault.match import Command, MatchRoom
from backend.modules.hassault.physics import World, flat_world


class Spawn:
    def __init__(self, x: float, y: float, z: float = 0.0, team: int = 0) -> None:
        self.x = x
        self.y = y
        self.z = z
        self.yaw = 0.0
        self.attr2 = team


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    yield


def walled_world(ssize: int, wall_x: int, y0: int, y1: int) -> World:
    """A flat room with one solid pillar across it.

    `World.type` is `bytes` — the same buffer the `/cubes` route serves — so a
    test cannot poke a wall into `flat_world`'s grid after the fact. Building the
    plane is the honest way, and it keeps the test reading against the same World
    the server runs on.
    """
    n = ssize * ssize
    types = bytearray([SPACE]) * n
    for i in range(ssize):
        for j in (0, 1, ssize - 2, ssize - 1):
            types[i * ssize + j] = SOLID
            types[j * ssize + i] = SOLID
    for cy in range(y0, y1 + 1):
        types[cy * ssize + wall_x] = SOLID
    return World(
        ssize=ssize,
        type=bytes(types),
        floor=bytes(n),
        ceil=bytes([16]) * n,
        vdelta=bytes(n),
    )


def make_room() -> MatchRoom:
    world = flat_world(64, floor=0, ceil=16)
    return MatchRoom(
        "r1", "hd_pit", world, [Spawn(8, 8, team=0), Spawn(40, 40, team=1)]
    )


def place(player, x: float, y: float, yaw: float = 0.0) -> None:
    player.state.x = x
    player.state.y = y
    player.state.z = 0.0
    player.state.yaw = yaw
    player.state.on_ground = True


def throw(seq: int, slot: int, yaw: float = 0.0, pitch: float = 0.0, **kw) -> Command:
    kw.setdefault("forward", 0.0)
    kw.setdefault("strafe", 0.0)
    kw.setdefault("jump", False)
    kw.setdefault("dt", 1 / 60)
    return Command(seq=seq, yaw=yaw, pitch=pitch, throw=True, nade=slot, **kw)


SLOT = {g.id: i for i, g in enumerate(grenades.GRENADES)}


# ---------------------------------------------------------------------------
# The projectile
# ---------------------------------------------------------------------------


def test_a_dropped_grenade_comes_to_rest_on_the_floor():
    """It has to *settle*. A grenade that jitters forever never rests, and a
    smoke whose centre keeps moving is a smoke that drifts out of the doorway it
    was placed in."""
    world = flat_world(64, floor=0, ceil=16)
    nade = grenades.Grenade(
        id="n",
        spec=grenades.BY_ID["he"],
        owner="a",
        team=0,
        x=8,
        y=8,
        z=6,
        vx=0,
        vy=0,
        vz=0,
        fuse=99,
    )
    for _ in range(180):
        grenades.step_grenade(world, nade, 1 / 60)
    assert nade.resting
    assert nade.z == pytest.approx(0.0, abs=0.2)


def test_a_grenade_bounces_off_a_wall_instead_of_passing_through_it():
    """The wall is the only thing that makes bouncing one round a corner a skill
    rather than a wish."""
    # A pillar directly in the path.
    world = walled_world(64, wall_x=20, y0=10, y1=14)

    nade = grenades.Grenade(
        id="n",
        spec=grenades.BY_ID["he"],
        owner="a",
        team=0,
        x=8,
        y=12,
        z=4,
        vx=30,
        vy=0,
        vz=0,
        fuse=99,
    )
    for _ in range(120):
        grenades.step_grenade(world, nade, 1 / 60)
    # Stopped short of the pillar and turned around, rather than ending up past it.
    assert nade.x < 20


def test_the_fuse_counts_down_on_the_rooms_clock():
    world = flat_world(64, floor=0, ceil=16)
    nade = grenades.Grenade(
        id="n",
        spec=grenades.BY_ID["he"],
        owner="a",
        team=0,
        x=8,
        y=8,
        z=4,
        vx=0,
        vy=0,
        vz=0,
        fuse=1.0,
    )
    for _ in range(30):
        grenades.step_grenade(world, nade, 1 / 60)
    assert nade.fuse == pytest.approx(0.5, abs=0.02)


def test_a_lob_lands_shorter_than_a_full_throw():
    """The short throw is what makes a smoke placeable at your own feet."""
    flat = grenades.throw_velocity(0.0, 0.0, lob=False)
    lobbed = grenades.throw_velocity(0.0, 0.0, lob=True)
    assert math.hypot(*lobbed[:2]) < math.hypot(*flat[:2])


def test_a_throw_goes_where_the_crosshair_points():
    # Yaw 90 degrees: down +y, not +x. A grenade that ignored the view angles
    # would still travel and still look plausible, which is why this is pinned.
    vx, vy, _ = grenades.throw_velocity(math.pi / 2, 0.0, lob=False)
    assert vy > 20
    assert abs(vx) < 1e-6


# ---------------------------------------------------------------------------
# HE
# ---------------------------------------------------------------------------


def test_an_he_hurts_more_the_closer_you_are():
    world = flat_world(64, floor=0, ceil=16)
    nade = grenades.Grenade(
        id="n",
        spec=grenades.BY_ID["he"],
        owner="a",
        team=0,
        x=10,
        y=10,
        z=1,
        vx=0,
        vy=0,
        vz=0,
        fuse=0,
    )
    hits = {
        h.victim: h.damage
        for h in grenades.resolve_blast(
            world, nade, {"near": (11.0, 10.0, 1.0), "far": (16.0, 10.0, 1.0)}
        )
    }
    assert hits["near"] > hits["far"] > 0


def test_an_he_does_not_reach_through_a_wall():
    """The bug this whole file is most about: a radius test with no line of sight
    kills people through floors."""
    world = walled_world(64, wall_x=12, y0=8, y1=12)

    nade = grenades.Grenade(
        id="n",
        spec=grenades.BY_ID["he"],
        owner="a",
        team=0,
        x=10,
        y=10,
        z=1,
        vx=0,
        vy=0,
        vz=0,
        fuse=0,
    )
    # Both are inside the blast radius; only one of them can see it.
    victims = {"exposed": (11.0, 10.0, 1.0), "covered": (14.0, 10.0, 1.0)}
    hit = {h.victim for h in grenades.resolve_blast(world, nade, victims)}
    assert hit == {"exposed"}


def test_beyond_the_radius_is_nothing_at_all():
    world = flat_world(64, floor=0, ceil=16)
    nade = grenades.Grenade(
        id="n",
        spec=grenades.BY_ID["he"],
        owner="a",
        team=0,
        x=10,
        y=10,
        z=1,
        vx=0,
        vy=0,
        vz=0,
        fuse=0,
    )
    spec = grenades.BY_ID["he"]
    far = (10.0 + spec.radius + 1.0, 10.0, 1.0)
    assert grenades.resolve_blast(world, nade, {"v": far}) == []


# ---------------------------------------------------------------------------
# Flash
# ---------------------------------------------------------------------------


def test_looking_away_from_a_flash_is_the_counter_it_is_supposed_to_be():
    world = flat_world(64, floor=0, ceil=16)
    nade = grenades.Grenade(
        id="n",
        spec=grenades.BY_ID["flash"],
        owner="a",
        team=0,
        x=20,
        y=10,
        z=4,
        vx=0,
        vy=0,
        vz=0,
        fuse=0,
    )
    # Standing in the same place, one facing it and one facing away.
    at_it = grenades.flash_effect(world, nade, 10, 10, 4.5, 0.0, 0.0)
    away = grenades.flash_effect(world, nade, 10, 10, 4.5, math.pi, 0.0)
    # Facing it is a white-out held for a while; turned round, it never reaches
    # white and is never held. That gap is the counter.
    assert at_it.peak == 1.0 and at_it.hold > 0.5
    assert away.peak < 0.9 and away.hold == 0.0
    assert at_it.dose > away.dose * 5
    # Not nothing: a bang at your heels still rattles you, or turning round would
    # be a perfect counter rather than a good one.
    assert away.fade > 0


def test_a_wall_stops_a_flash_because_it_is_light():
    world = walled_world(64, wall_x=15, y0=8, y1=12)
    nade = grenades.Grenade(
        id="n",
        spec=grenades.BY_ID["flash"],
        owner="a",
        team=0,
        x=20,
        y=10,
        z=4,
        vx=0,
        vy=0,
        vz=0,
        fuse=0,
    )
    assert (
        grenades.flash_effect(world, nade, 10, 10, 4.5, 0.0, 0.0) == grenades.NO_FLASH
    )


def test_a_flash_across_the_map_does_nothing():
    world = flat_world(200, floor=0, ceil=16)
    nade = grenades.Grenade(
        id="n",
        spec=grenades.BY_ID["flash"],
        owner="a",
        team=0,
        x=150,
        y=10,
        z=4,
        vx=0,
        vy=0,
        vz=0,
        fuse=0,
    )
    assert (
        grenades.flash_effect(world, nade, 10, 10, 4.5, 0.0, 0.0) == grenades.NO_FLASH
    )


def _flash_at(x: float, y: float, z: float = 4.0) -> grenades.Grenade:
    return grenades.Grenade(
        id="n",
        spec=grenades.BY_ID["flash"],
        owner="a",
        team=0,
        x=x,
        y=y,
        z=z,
        vx=0,
        vy=0,
        vz=0,
        fuse=0,
    )


@pytest.mark.parametrize(
    ("degrees", "peak", "held"),
    [
        (0, 1.0, True),
        (45, 1.0, True),
        (60, 1.0, True),
        (85, 0.9, True),
        (135, 0.5, False),
        (180, 0.5, False),
    ],
)
def test_the_facing_bands_are_sharp_like_counter_strikes(degrees, peak, held):
    """The counter has to be sharp: on screen is a white-out, off the edge of the
    screen a flicker, behind you a jolt that never reaches white."""
    world = flat_world(64, floor=0, ceil=16)
    nade = _flash_at(20, 10, 4.5)
    effect = grenades.flash_effect(world, nade, 10, 10, 4.5, math.radians(degrees), 0.0)
    assert effect.peak == pytest.approx(peak)
    assert (effect.hold > 0) is held


def test_the_side_band_is_a_flicker_not_a_blind():
    world = flat_world(64, floor=0, ceil=16)
    nade = _flash_at(20, 10, 4.5)
    front = grenades.flash_effect(world, nade, 10, 10, 4.5, 0.0, 0.0)
    side = grenades.flash_effect(world, nade, 10, 10, 4.5, math.radians(85), 0.0)
    assert side.hold < front.hold * 0.1


def test_distance_shortens_a_flash_but_does_not_grey_it():
    world = flat_world(64, floor=0, ceil=16)
    near = grenades.flash_effect(world, _flash_at(14, 10, 4.5), 10, 10, 4.5, 0.0, 0.0)
    far = grenades.flash_effect(world, _flash_at(34, 10, 4.5), 10, 10, 4.5, 0.0, 0.0)
    assert near.peak == far.peak == 1.0
    assert near.hold > far.hold > 0


def test_point_blank_cannot_be_turned_away_from():
    world = flat_world(64, floor=0, ceil=16)
    effect = grenades.flash_effect(
        world, _flash_at(11, 10, 4.5), 10, 10, 4.5, math.pi, 0.0
    )
    assert effect.peak == 1.0
    assert effect.hold > 0


def test_a_flash_half_round_a_corner_is_half_a_flash():
    """Light is sampled from several points round the bang. A single ray to its
    centre made a lip of floor between you and it a hard zero."""
    world = flat_world(64, floor=0, ceil=16)
    clear = grenades.flash_light_fraction(world, _flash_at(20, 10, 4.5), (10, 10, 4.5))
    assert clear == 1.0
    # Just behind the edge of a pillar: the ray to the centre is blocked, the one
    # to the side of the bang that clears the edge is not.
    pillar = walled_world(64, wall_x=15, y0=8, y1=10)
    edge = grenades.flash_light_fraction(
        pillar, _flash_at(20, 10.9, 4.5), (10, 10.9, 4.5)
    )
    assert 0.0 < edge < 1.0


def _smoke(x: float, y: float, z: float, r: float = 3.0) -> grenades.Zone:
    return grenades.Zone(
        id="z",
        kind="smoke",
        owner="a",
        team=0,
        x=x,
        y=y,
        z=z,
        radius=r,
        remaining=10.0,
        duration=10.0,
        damage_per_second=0.0,
    )


def test_a_smoke_between_you_and_a_flash_stops_it():
    world = flat_world(64, floor=0, ceil=16)
    nade = _flash_at(20, 10, 4.5)
    effect = grenades.flash_effect(
        world, nade, 10, 10, 4.5, 0.0, 0.0, zones=[_smoke(15, 10, 4.5)]
    )
    assert effect == grenades.NO_FLASH


def test_a_flash_inside_your_own_smoke_still_blinds_you():
    """You are in the light's room: the cloud is around you, not between."""
    world = flat_world(64, floor=0, ceil=16)
    nade = _flash_at(12, 10, 4.5)
    effect = grenades.flash_effect(
        world, nade, 10, 10, 4.5, 0.0, 0.0, zones=[_smoke(11, 10, 4.5, r=4.0)]
    )
    assert effect.peak == 1.0


def test_a_cleared_smoke_lets_the_flash_through():
    world = flat_world(64, floor=0, ceil=16)
    zone = _smoke(15, 10, 4.5)
    zone.cleared_until = 100.0
    effect = grenades.flash_effect(
        world, _flash_at(20, 10, 4.5), 10, 10, 4.5, 0.0, 0.0, zones=[zone], now=50.0
    )
    assert effect.peak == 1.0


def test_the_curve_holds_then_fades_to_nothing():
    assert grenades.flash_alpha(1.0, 0.5, 2.0, 2.0) == 1.0
    assert grenades.flash_alpha(1.0, 0.0, 2.0, 2.0) == pytest.approx(1.0)
    middle = grenades.flash_alpha(1.0, 0.0, 1.0, 2.0)
    assert 0.0 < middle < 1.0
    assert grenades.flash_alpha(1.0, 0.0, 0.0, 2.0) == 0.0
    assert grenades.flash_alpha(0.0, 1.0, 1.0, 1.0) == 0.0


# ---------------------------------------------------------------------------
# Smoke
# ---------------------------------------------------------------------------


def test_a_smoke_blocks_the_line_it_stands_on():
    zone = grenades.Zone(
        id="z",
        kind="smoke",
        owner="a",
        team=0,
        x=15,
        y=10,
        z=4,
        radius=7.5,
        remaining=10,
        duration=10,
        damage_per_second=0,
    )
    assert grenades.sight_blocked_by([zone], (10, 10, 4), (20, 10, 4))
    # A cloud off to one side blocks nothing.
    assert not grenades.sight_blocked_by([zone], (10, 40, 4), (20, 40, 4))


def test_a_smoke_behind_you_blocks_nothing():
    """The segment is clamped for this: an unclamped closest-point test finds the
    cloud at your back and reports it as cover for a shot going the other way."""
    zone = grenades.Zone(
        id="z",
        kind="smoke",
        owner="a",
        team=0,
        x=0,
        y=10,
        z=4,
        radius=6,
        remaining=10,
        duration=10,
        damage_per_second=0,
    )
    assert not grenades.sight_blocked_by([zone], (20, 10, 4), (40, 10, 4))


def test_only_smoke_blocks_sight_not_fire():
    """Fire is bright, not opaque. A fire zone that blocked vision would be cover
    you could stand behind, which is the opposite of what it is for."""
    fire = grenades.Zone(
        id="z",
        kind="fire",
        owner="a",
        team=0,
        x=15,
        y=10,
        z=4,
        radius=8,
        remaining=10,
        duration=10,
        damage_per_second=20,
    )
    assert not grenades.sight_blocked_by([fire], (10, 10, 4), (20, 10, 4))


# ---------------------------------------------------------------------------
# Through the room: the wire, the inventory, and who is told what
# ---------------------------------------------------------------------------


def test_throwing_spends_one_and_puts_it_in_the_air():
    room = make_room()
    player = room.add("@rob", None)
    place(player, 10, 10)
    before = player.nades.to_dict()["smoke"]

    room.enqueue(player, throw(1, SLOT["smoke"]))
    room.simulate(1 / 60)

    assert len(room.nades) == 1
    assert player.nades.to_dict()["smoke"] == before - 1


def test_a_held_throw_key_does_not_empty_the_pouch():
    """`throw` is a flag on a movement command, so without a cooldown a client
    sending 120 commands a second throws 120 grenades a second."""
    room = make_room()
    player = room.add("@rob", None)
    place(player, 10, 10)
    for seq in range(1, 6):
        room.enqueue(player, throw(seq, SLOT["flash"]))
    room.simulate(0.1)
    assert len(room.nades) == 1


def test_you_cannot_throw_what_you_are_not_carrying():
    room = make_room()
    player = room.add("@rob", None)
    place(player, 10, 10)
    player.nades.counts[SLOT["smoke"]] = 0
    room.enqueue(player, throw(1, SLOT["smoke"]))
    room.simulate(1 / 60)
    assert room.nades == []


def test_a_nonsense_slot_throws_nothing():
    room = make_room()
    player = room.add("@rob", None)
    place(player, 10, 10)
    room.enqueue(player, throw(1, 99))
    room.simulate(1 / 60)
    assert room.nades == []


def test_a_smoke_becomes_a_cloud_and_the_cloud_expires():
    room = make_room()
    player = room.add("@rob", None)
    place(player, 10, 10)
    room.enqueue(player, throw(1, SLOT["smoke"], pitch=-1.2, lob=True))
    room.simulate(1 / 60)

    for _ in range(140):
        room.simulate(1 / 60)
    assert len(room.zones) == 1
    assert room.zones[0].kind == "smoke"

    # Run past its duration.
    for _ in range(int(grenades.BY_ID["smoke"].duration / 0.05) + 4):
        room.simulate(0.05)
    assert room.zones == []


def test_an_he_detonating_hurts_an_enemy_standing_on_it():
    room = make_room()
    thrower = room.add("@rob", None)
    victim = room.add("@vic", None)
    victim.team = 1 - thrower.team
    place(thrower, 10, 10)
    place(victim, 12, 10)
    victim.protected_until = 0.0
    thrower.protected_until = 0.0

    room.enqueue(thrower, throw(1, SLOT["he"], pitch=-1.35, lob=True))
    room.simulate(1 / 60)
    for _ in range(150):
        room.simulate(1 / 60)

    assert room.nades == []
    assert victim.health < 100


def test_a_teammate_is_not_hurt_by_your_he():
    """Friendly fire is off for bullets; a grenade cannot be the exception, or
    every team fight is decided by whoever throws first."""
    room = make_room()
    thrower = room.add("@rob", None)
    mate = room.add("@mate", None)
    mate.team = thrower.team
    place(thrower, 10, 10)
    place(mate, 12, 10)
    mate.protected_until = 0.0
    thrower.protected_until = 0.0

    room.enqueue(thrower, throw(1, SLOT["he"], pitch=-1.35, lob=True))
    for _ in range(150):
        room.simulate(1 / 60)
    assert mate.health == 100


def test_your_own_he_hurts_you_and_credits_nobody():
    """A grenade at your own feet is your own fault. Routed through the
    no-killer path, so it never puts you on your own scoreboard line."""
    room = make_room()
    thrower = room.add("@rob", None)
    place(thrower, 10, 10)
    thrower.protected_until = 0.0

    # Straight down at your feet.
    room.enqueue(thrower, throw(1, SLOT["he"], pitch=-1.5, lob=True))
    for _ in range(150):
        room.simulate(1 / 60)

    assert thrower.health < 100
    assert thrower.kills == 0


def test_utility_counts_and_blindness_never_reach_the_shared_rows():
    """The `noise.py` rule, applied to grenades: what is in the shared rows is
    what everyone may know. How much utility somebody has left, and how blind
    they are, are not on that list."""
    room = make_room()
    player = room.add("@rob", None)
    place(player, 10, 10)
    player.flash = 0.8

    row = player.snapshot(0.0)
    assert "nades" not in row
    assert "flash" not in row

    private = player.private_view(0.0)
    assert private["nades"]["smoke"] >= 0
    assert private["flash"] == pytest.approx(0.8)
    # The durations that let a client run the curve itself, as seconds left.
    assert private["flashPeak"] == pytest.approx(0.8)
    assert private["flashFade"] > 0
    for key in ("flashPeak", "flashHold", "flashFade", "flashFadeTotal"):
        assert key not in row


def test_a_grenade_in_the_air_is_public():
    """Unlike a footstep. A grenade is a thing on your screen, and hiding one you
    can see would be worse than useless."""
    room = make_room()
    player = room.add("@rob", None)
    place(player, 10, 10)
    room.enqueue(player, throw(1, SLOT["he"]))
    room.simulate(1 / 60)

    rows = [p.snapshot(0.0) for p in room.players.values()]
    packet = room.snapshot_for(player, 0.0, rows)["data"]
    assert len(packet["nades"]) == 1
    assert packet["nades"][0]["kind"] == "he"
    assert "zones" in packet


def test_a_flash_blinds_the_player_who_threw_it_badly():
    room = make_room()
    thrower = room.add("@rob", None)
    place(thrower, 10, 10)
    thrower.protected_until = 0.0

    # Straight at the floor in front of them.
    room.enqueue(thrower, throw(1, SLOT["flash"], pitch=-1.35, lob=True))
    for _ in range(140):
        room.simulate(1 / 60)
    assert thrower.flash > 0


def test_being_flashed_is_announced_once_and_only_to_you():
    """The client's trigger for the afterimage and the ringing. Drained by the
    private view, so it arrives exactly once."""
    room = make_room()
    thrower = room.add("@rob", None)
    place(thrower, 10, 10)
    thrower.protected_until = 0.0
    room.enqueue(thrower, throw(1, SLOT["flash"], pitch=-1.35, lob=True))
    for _ in range(140):
        room.simulate(1 / 60)
    first = thrower.private_view(0.0)
    assert len(first["flashed"]) == 1
    assert first["flashed"][0]["peak"] > 0
    assert thrower.private_view(0.0)["flashed"] == []


def test_a_second_flash_replaces_a_weaker_one_and_never_adds():
    player_room = make_room()
    player = player_room.add("@rob", None)
    big = grenades.FlashEffect(peak=1.0, hold=2.0, fade=3.0)
    small = grenades.FlashEffect(peak=0.5, hold=0.0, fade=0.5)
    assert player.apply_flash(big)
    assert not player.apply_flash(small)
    assert player.flash_hold == pytest.approx(2.0)
    assert player.apply_flash(grenades.FlashEffect(peak=1.0, hold=2.5, fade=3.0))
    assert player.flash_hold == pytest.approx(2.5)


def test_the_hold_runs_out_before_the_fade_starts():
    room = make_room()
    player = room.add("@rob", None)
    player.apply_flash(grenades.FlashEffect(peak=1.0, hold=0.5, fade=1.0))
    player.step_flash(0.4)
    assert player.flash == 1.0
    player.step_flash(0.3)
    assert player.flash_hold == 0.0
    assert player.flash_fade == pytest.approx(0.8)
    assert 0.0 < player.flash < 1.0
    player.step_flash(5.0)
    assert player.flash == 0.0 and player.flash_peak == 0.0


def test_a_blind_ally_paints_nothing_on_the_radar():
    world = flat_world(64, floor=0, ceil=16)
    room = MatchRoom(
        "r1", "hd_pit", world, [Spawn(8, 8, team=0), Spawn(40, 40, team=1)]
    )
    ally = room.add("@ally", None)
    enemy = room.add("@enemy", None)
    ally.team, enemy.team = 0, 1
    place(ally, 10, 10, yaw=0.0)
    place(enemy, 20, 10)
    assert enemy.id in room.spotted_for_team(0)
    ally.apply_flash(grenades.FlashEffect(peak=1.0, hold=2.0, fade=2.0))
    assert enemy.id not in room.spotted_for_team(0)


def test_a_blind_bot_sees_nobody():
    from backend.modules.hassault.bots import BotBrain

    world = flat_world(64, floor=0, ceil=16)
    room = MatchRoom(
        "r1", "hd_pit", world, [Spawn(8, 8, team=0), Spawn(40, 40, team=1)]
    )
    bot = room.add("@bot", None)
    enemy = room.add("@enemy", None)
    bot.team, enemy.team = 0, 1
    place(bot, 10, 10, yaw=0.0)
    place(enemy, 20, 10)
    brain = BotBrain()
    assert brain._visible(room, bot, enemy)
    bot.apply_flash(grenades.FlashEffect(peak=1.0, hold=2.0, fade=2.0))
    assert not brain._visible(room, bot, enemy)


def test_blindness_fades_on_its_own():
    """It has to fade on the room's clock: a blinded player who stops sending
    input must still recover, or being flashed while alt-tabbed is permanent."""
    room = make_room()
    player = room.add("@rob", None)
    place(player, 10, 10)
    player.flash = 1.0
    for _ in range(int(grenades.FLASH_MAX / 0.05) + 4):
        room.simulate(0.05)
    assert player.flash == 0.0


def test_a_fire_burns_whoever_stands_in_it():
    room = make_room()
    thrower = room.add("@rob", None)
    victim = room.add("@vic", None)
    victim.team = 1 - thrower.team
    place(thrower, 10, 10)
    place(victim, 12, 10)
    victim.protected_until = 0.0
    thrower.protected_until = 0.0

    room.enqueue(thrower, throw(1, SLOT["molotov"], pitch=-1.2, lob=True))
    for _ in range(60):
        room.simulate(1 / 60)
    assert any(z.kind == "fire" for z in room.zones)

    before = victim.health
    for _ in range(30):
        room.simulate(1 / 60)
    assert victim.health < before


def test_an_incendiary_resolves_where_it_lands_not_on_a_timer():
    """Its defining property: it is aimed at a *place*, so a good throw is not
    then spoiled by the grenade rolling somewhere else."""
    spec = grenades.BY_ID["molotov"]
    assert spec.impact
    # And the fuse is a backstop rather than the mechanism, so it is long.
    assert spec.fuse > 4


# ---------------------------------------------------------------------------
# The radar's spotting rule
# ---------------------------------------------------------------------------


def test_a_teammate_is_always_on_the_radar_and_an_unseen_enemy_is_not():
    room = make_room()
    me = room.add("@rob", None)
    mate = room.add("@mate", None)
    mate.team = me.team
    enemy = room.add("@enemy", None)
    enemy.team = 1 - me.team
    place(me, 10, 10, yaw=0.0)
    place(mate, 12, 10)
    # Behind me, well outside the cone.
    place(enemy, 4, 10)

    spotted = room.spotted_by(me)
    # Teammates are never in this list — they are unconditional, so the client
    # does not need telling.
    assert mate.id not in spotted
    assert enemy.id not in spotted


def test_an_enemy_in_front_of_you_is_spotted():
    room = make_room()
    me = room.add("@rob", None)
    enemy = room.add("@enemy", None)
    enemy.team = 1 - me.team
    place(me, 10, 10, yaw=0.0)
    place(enemy, 20, 10)
    assert enemy.id in room.spotted_by(me)


def test_a_wall_stops_a_spot():
    world = walled_world(64, wall_x=15, y0=8, y1=12)
    room = MatchRoom("r2", "m", world, [Spawn(8, 8, team=0), Spawn(40, 40, team=1)])
    me = room.add("@rob", None)
    enemy = room.add("@enemy", None)
    enemy.team = 1 - me.team
    place(me, 10, 10, yaw=0.0)
    place(enemy, 20, 10)
    assert enemy.id not in room.spotted_by(me)


def test_a_smoke_hides_you_from_the_radar_too():
    """Most of the reason to throw one. A cloud that blocked eyes but not the
    minimap would be worth almost nothing."""
    room = make_room()
    me = room.add("@rob", None)
    enemy = room.add("@enemy", None)
    enemy.team = 1 - me.team
    place(me, 10, 10, yaw=0.0)
    place(enemy, 20, 10)
    assert enemy.id in room.spotted_by(me)

    room.zones.append(
        grenades.Zone(
            id="z",
            kind="smoke",
            owner=me.id,
            team=me.team,
            x=15,
            y=10,
            z=4.5,
            radius=6,
            remaining=10,
            duration=10,
            damage_per_second=0,
        )
    )
    assert enemy.id not in room.spotted_by(me)


def test_a_teammate_looking_at_them_paints_them_for_you():
    """The whole point of a shared radar: what one of you saw, all of you know."""
    room = make_room()
    me = room.add("@rob", None)
    mate = room.add("@mate", None)
    mate.team = me.team
    enemy = room.add("@enemy", None)
    enemy.team = 1 - me.team
    # I am facing away; my teammate is not.
    place(me, 10, 10, yaw=math.pi)
    place(mate, 30, 10, yaw=0.0)
    place(enemy, 40, 10)
    assert enemy.id in room.spotted_by(me)


def test_every_player_on_a_team_gets_the_identical_radar():
    """The invariant the per-team computation rests on.

    An enemy is painted when *anybody* on your side can see them, so the answer
    depends on the viewer only through their team. If that ever stopped being
    true, computing it once per team would start handing players a radar that is
    not theirs — so it is asserted rather than assumed.
    """
    room = make_room()
    me = room.add("@rob", None)
    mate = room.add("@mate", None)
    blind = room.add("@blind", None)
    mate.team = blind.team = me.team
    enemy = room.add("@enemy", None)
    enemy.team = 1 - me.team
    # Only the mate can see the enemy; the other two are facing away.
    place(me, 10, 10, yaw=math.pi)
    place(blind, 11, 10, yaw=math.pi)
    place(mate, 30, 10, yaw=0.0)
    place(enemy, 40, 10)

    assert room.spotted_by(me) == room.spotted_by(mate) == room.spotted_by(blind)
    assert enemy.id in room.spotted_by(blind)


def test_each_team_gets_its_own_radar_and_not_the_other_ones():
    """The failure mode worth guarding: computing per team must not become
    computing one radar and giving it to everybody.

    Set up so the two sides genuinely differ — team 0 is looking at team 1, and
    team 1 is looking away — and assert the sighted side sees and the blind side
    does not.
    """
    room = make_room()
    seer = room.add("@seer", None)
    seer.team = 0
    hidden = room.add("@hidden", None)
    hidden.team = 1
    place(seer, 10, 10, yaw=0.0)
    place(hidden, 30, 10, yaw=0.0)  # facing further away, not back at the seer

    by_team = room.spotted_by_team()
    assert by_team[0] == [hidden.id]
    assert by_team[1] == []
    # And the per-team map agrees with asking each player individually, which is
    # the equivalence the refactor has to preserve.
    for player in room.players.values():
        assert by_team[player.team] == room.spotted_by(player)


def test_a_team_with_nobody_on_it_has_no_radar_entry():
    """`private_view_for` falls back rather than assuming the key is there."""
    room = make_room()
    solo = room.add("@solo", None)
    solo.team = 0
    place(solo, 10, 10)
    by_team = room.spotted_by_team()
    assert set(by_team) == {0}
    # The fallback path, exercised through the packet a player actually receives.
    assert room.private_view_for(solo, by_team)["spotted"] == []


def test_broadcast_gives_each_player_their_own_teams_radar():
    """End to end through `_broadcast`, which is what computes the map once."""
    room = make_room()
    seer = room.add("@seer", None)
    seer.team = 0
    hidden = room.add("@hidden", None)
    hidden.team = 1
    place(seer, 10, 10, yaw=0.0)
    place(hidden, 30, 10, yaw=0.0)

    spotted = room.spotted_by_team()
    rows = [p.snapshot(0.0) for p in room.players.values()]
    shared = room.shared_view()
    seer_packet = room.snapshot_message(
        0.0, rows, shared, seer.ack, room.private_view_for(seer, spotted)
    )
    hidden_packet = room.snapshot_message(
        0.0, rows, shared, hidden.ack, room.private_view_for(hidden, spotted)
    )
    assert seer_packet["data"]["you"]["spotted"] == [hidden.id]
    assert hidden_packet["data"]["you"]["spotted"] == []


def test_spotting_is_per_recipient_and_never_shared():
    room = make_room()
    me = room.add("@rob", None)
    enemy = room.add("@enemy", None)
    enemy.team = 1 - me.team
    place(me, 10, 10, yaw=0.0)
    place(enemy, 20, 10)

    rows = [p.snapshot(0.0) for p in room.players.values()]
    packet = room.snapshot_for(me, 0.0, rows)["data"]
    assert enemy.id in packet["you"]["spotted"]
    # Not in the shared half, which every client gets a copy of.
    assert all("spotted" not in row for row in packet["players"])


# ---------------------------------------------------------------------------
# The throw constants, served
# ---------------------------------------------------------------------------


def test_the_throw_route_publishes_every_constant_the_arc_needs():
    """The `response_model` gate, on the route a trajectory preview reads.

    Missing from `ThrowPhysicsOut`, a constant reaches the client as `undefined`
    and the preview integrates with a zero — a straight line into the floor, or a
    grenade that never falls. An aiming aid that is confidently wrong is worse
    than none, so this asserts the **wire** rather than the module.
    """
    from fastapi.testclient import TestClient

    from backend.app import app

    with TestClient(app) as client:
        res = client.get("/api/hassault/throw")
        assert res.status_code == 200
        served = res.json()

    for key in (
        "gravity",
        "throwSpeed",
        "lobScale",
        "throwInherit",
        "throwForward",
        "throwDrop",
        "restSpeed",
        "substep",
        "maxSubsteps",
    ):
        assert key in served, key


def test_the_served_constants_are_the_modules_own():
    """Read from `grenades`/`physics`, never retyped.

    Compared by identity against the modules rather than against literals: a
    literal here is a *third* copy, and it would pass while the route quietly
    served a stale one.
    """
    from fastapi.testclient import TestClient

    from backend.app import app
    from backend.modules.hassault import grenades, physics

    with TestClient(app) as client:
        served = client.get("/api/hassault/throw").json()

    assert served["gravity"] == physics.GRAVITY
    assert served["throwSpeed"] == grenades.THROW_SPEED
    assert served["lobScale"] == grenades.LOB_SCALE
    assert served["throwInherit"] == grenades.THROW_INHERIT
    assert served["throwForward"] == grenades.THROW_FORWARD
    assert served["throwDrop"] == grenades.THROW_DROP
    assert served["restSpeed"] == grenades.REST_SPEED
    assert served["substep"] == grenades.SUBSTEP
    assert served["maxSubsteps"] == grenades.MAX_SUBSTEPS


def test_tacticals_is_still_a_bare_list():
    """The reason `/throw` is its own route.

    `/tacticals` is `response_model=list[TacticalOut]`, and every installed
    native client deserialises it as a list. Reshaping it into an object to carry
    the throw constants would make all of them see **no grenades at all**, with
    no error anywhere — so the shape is pinned rather than left to a future
    refactor's judgement.
    """
    from fastapi.testclient import TestClient

    from backend.app import app

    with TestClient(app) as client:
        served = client.get("/api/hassault/tacticals").json()

    assert isinstance(served, list)
    assert served, "the grenade catalogue is empty"


def test_running_and_jumping_actually_change_where_a_grenade_goes():
    """`THROW_INHERIT` — the thing the trajectory preview exists to make visible.

    The server has done this since grenades existed and nothing on screen ever
    said so; a preview that did not reproduce it would be an aiming aid that is
    wrong exactly when a player is moving, which is most of the time.
    """
    still = grenades.throw_velocity(0.0, 0.0, False, (0.0, 0.0, 0.0))
    running = grenades.throw_velocity(0.0, 0.0, False, (20.0, 0.0, 0.0))
    jumping = grenades.throw_velocity(0.0, 0.0, False, (0.0, 0.0, 22.0))

    assert running[0] > still[0]
    assert jumping[2] > still[2]
    # At a fraction, not in full: at 1.0 a player running backwards can drop a
    # grenade that never leaves them, which reads as the throw having failed.
    assert running[0] - still[0] < 20.0


def test_throw_velocity_power_scaling():
    """Overhand 1.0, medium 0.72, underhand 0.42."""
    v_over = grenades.throw_velocity(0.0, 0.0, False, power=1.0)
    v_med = grenades.throw_velocity(0.0, 0.0, True, power=0.72)
    v_under = grenades.throw_velocity(0.0, 0.0, True, power=0.42)

    assert v_over[0] == pytest.approx(grenades.THROW_SPEED * 1.0)
    assert v_med[0] == pytest.approx(grenades.THROW_SPEED * 0.72)
    assert v_under[0] == pytest.approx(grenades.THROW_SPEED * 0.42)
    assert v_over[0] > v_med[0] > v_under[0]


def test_he_blast_disperses_smoke_temporarily():
    """HE grenade clears smoke line-of-sight for 2.5s."""
    from backend.modules.hassault.modes import Deathmatch

    world = flat_world(32)
    m = MatchRoom(
        room_id="test-room",
        map_name="test",
        world=world,
        mode=Deathmatch(),
        spawns=[Spawn(10, 10), Spawn(20, 20)],
    )

    smoke_zone = grenades.Zone(
        id="smoke-1",
        kind="smoke",
        owner="p1",
        team=0,
        x=15.0,
        y=15.0,
        z=1.0,
        radius=10.0,
        remaining=15.0,
        duration=15.0,
        damage_per_second=0.0,
    )
    m.zones.append(smoke_zone)

    # Smoke blocks sight initially
    assert m.smoked((10.0, 15.0, 1.0), (20.0, 15.0, 1.0)) is True

    # Detonate HE grenade within radius
    he_spec = grenades.BY_ID["he"]
    he_nade = grenades.Grenade(
        id="he-1",
        spec=he_spec,
        owner="p1",
        team=0,
        x=15.0,
        y=15.0,
        z=1.0,
        vx=0.0,
        vy=0.0,
        vz=0.0,
        fuse=0.0,
    )
    m._detonate(he_nade, now=10.0)

    # Smoke zone has cleared_until set to 10.0 + 2.5 = 12.5
    assert smoke_zone.cleared_until == pytest.approx(12.5)

    # During cleared window (now = 11.0), sight is NOT blocked!
    m.now = 11.0
    assert m.smoked((10.0, 15.0, 1.0), (20.0, 15.0, 1.0)) is False

    # After cleared window (now = 13.0), smoke blocks sight again!
    m.now = 13.0
    assert m.smoked((10.0, 15.0, 1.0), (20.0, 15.0, 1.0)) is True


def test_smoke_extinguishes_fire_and_fire_in_smoke():
    """Smoke puts out fire on detonation; fire landing in smoke is extinguished."""
    from backend.modules.hassault.modes import Deathmatch

    world = flat_world(32)
    m = MatchRoom(
        room_id="test-room-2",
        map_name="test",
        world=world,
        mode=Deathmatch(),
        spawns=[Spawn(10, 10), Spawn(20, 20)],
    )

    fire_zone = grenades.Zone(
        id="fire-1",
        kind="fire",
        owner="p1",
        team=0,
        x=15.0,
        y=15.0,
        z=1.0,
        radius=8.0,
        remaining=10.0,
        duration=10.0,
        damage_per_second=50.0,
    )
    m.zones.append(fire_zone)

    # Detonate smoke nearby
    smoke_spec = grenades.BY_ID["smoke"]
    smoke_nade = grenades.Grenade(
        id="smoke-nade",
        spec=smoke_spec,
        owner="p2",
        team=1,
        x=16.0,
        y=15.0,
        z=1.0,
        vx=0.0,
        vy=0.0,
        vz=0.0,
        fuse=0.0,
    )
    m._detonate(smoke_nade, now=1.0)

    # Fire zone was extinguished (remaining set to 0.0)
    assert fire_zone.remaining == 0.0

    # Clean up spent zones
    m.zones = [z for z in m.zones if z.remaining > 0]
    assert len(m.zones) == 1
    assert m.zones[0].kind == "smoke"

    # Now throw molotov into the active smoke
    molotov_spec = grenades.BY_ID["molotov"]
    molotov_nade = grenades.Grenade(
        id="molo-nade",
        spec=molotov_spec,
        owner="p1",
        team=0,
        x=16.0,
        y=15.0,
        z=1.0,
        vx=0.0,
        vy=0.0,
        vz=0.0,
        fuse=0.0,
    )
    m._detonate(molotov_nade, now=2.0)

    # No new fire zone was added because it was extinguished on impact with smoke!
    assert not any(z.kind == "fire" for z in m.zones)


def test_the_flash_curve_matches_the_shared_fixture():
    """`flash-vectors.json` binds this curve to `flashbang.ts` and
    `flashbang.rs`. If this fails, the generator was not re-run after a change
    to `flash_alpha` or `MatchPlayer.step_flash`."""
    import json
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[2]
        / "packages/core/src/modules/hassault/__tests__/flash-vectors.json"
    )
    vectors = json.loads(path.read_text(encoding="utf-8"))
    tol = vectors["tolerance"]
    assert vectors["blind"] == grenades.FLASH_BLIND
    for case in vectors["alpha"]:
        got = grenades.flash_alpha(
            case["peak"], case["hold"], case["fade"], case["fadeTotal"]
        )
        assert got == pytest.approx(case["alpha"], abs=tol)
    room = make_room()
    player = room.add("@rob", None)
    for series in vectors["steps"]:
        player.apply_flash(grenades.NO_FLASH)
        player.flash_peak = series["peak"]
        player.flash_hold = series["hold"]
        player.flash_fade = series["fade"]
        player.flash_fade_total = series["fade"]
        for want in series["series"]:
            player.step_flash(series["dt"])
            assert player.flash_hold == pytest.approx(want["hold"], abs=tol)
            assert player.flash_fade == pytest.approx(want["fade"], abs=tol)
            assert player.flash == pytest.approx(want["alpha"], abs=tol)

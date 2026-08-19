"""Noise, weapon kickback, and fall damage — the three mechanics that make
movement cost something.

Hermetic, like the rest of the hassault suite: every world is a synthetic
`flat_world`, because AssaultCube content is copyright and cannot live here.

The tests that matter most are the ones pinning behaviour that is invisible when
it is wrong: that a noise envelope **omits what a listener cannot hear** (the
alternative is a wall hack made of sound), that crouching is genuinely silent (the
whole reason its speed penalty is a trade), and that a resting player is never
charged fall damage.
"""

from __future__ import annotations

import math

import pytest

from backend.modules.hassault import noise, physics, weapons
from backend.modules.hassault.match import Command, MatchRoom
from backend.modules.hassault.noise import Noise
from backend.modules.hassault.physics import (
    CROUCH_HEIGHT,
    STANDING_HEIGHT,
    flat_world,
)


class Spawn:
    def __init__(
        self, x: float, y: float, z: float = 0.0, yaw: float = 0.0, team: int = 0
    ) -> None:
        self.x = x
        self.y = y
        self.z = z
        self.yaw = yaw
        self.attr2 = team


def make_room(room_id: str = "n1", ssize: int = 64) -> MatchRoom:
    world = flat_world(ssize, floor=0, ceil=16)
    return MatchRoom(
        room_id, "testmap", world, [Spawn(8, 8, team=0), Spawn(24, 24, team=1)]
    )


def place(player, x: float, y: float, z: float = 0.0, yaw: float = 0.0) -> None:
    player.state.x = x
    player.state.y = y
    player.state.z = z
    player.state.yaw = yaw
    player.state.on_ground = True


def move(seq: int, **kw) -> Command:
    kw.setdefault("forward", 0.0)
    kw.setdefault("strafe", 0.0)
    kw.setdefault("jump", False)
    kw.setdefault("yaw", 0.0)
    kw.setdefault("pitch", 0.0)
    kw.setdefault("dt", 1 / 60)
    return Command(seq=seq, **kw)


# ---------------------------------------------------------------------------
# Audibility
# ---------------------------------------------------------------------------


def test_a_noise_fades_to_nothing_at_its_radius():
    world = flat_world(64, floor=0, ceil=16)
    here = Noise(kind="step", source="them", x=10.0, y=10.0, z=0.0, loudness=40.0)
    near = noise.hear(world, (12.0, 10.0, 4.5), here)
    far = noise.hear(world, (40.0, 10.0, 4.5), here)
    assert near is not None and far is not None
    assert near[0] > far[0]
    # Past the radius it is not "very quiet", it is absent.
    assert noise.hear(world, (60.0, 10.0, 4.5), here) is None


def test_a_wall_muffles_rather_than_silences():
    """Hearing someone through a wall is most of what listening is for. An
    occlusion test that muted them outright would make every corner a hard cut and
    reward nobody for paying attention."""
    world = flat_world(64, floor=0, ceil=16)
    types = bytearray(world.type)
    for y in range(64):
        types[y * 64 + 20] = 0  # SOLID: a north-south wall at x=20
    walled = physics.World(
        ssize=64,
        type=bytes(types),
        floor=world.floor,
        ceil=world.ceil,
        vdelta=world.vdelta,
    )
    here = Noise(kind="step", source="them", x=14.0, y=30.0, z=0.0, loudness=60.0)
    open_ear = noise.hear(world, (26.0, 30.0, 4.5), here)
    walled_ear = noise.hear(walled, (26.0, 30.0, 4.5), here)
    assert open_ear is not None and walled_ear is not None
    assert walled_ear[0] < open_ear[0]
    assert walled_ear[0] == pytest.approx(open_ear[0] * noise.WALL_MUFFLE, rel=1e-6)


def test_the_bearing_points_at_the_source():
    world = flat_world(64, floor=0, ceil=16)
    east = Noise(kind="step", source="them", x=30.0, y=20.0, z=0.0, loudness=60.0)
    heard = noise.hear(world, (20.0, 20.0, 4.5), east)
    assert heard is not None
    assert heard[1] == pytest.approx(0.0, abs=1e-6)

    north = Noise(kind="step", source="them", x=20.0, y=30.0, z=0.0, loudness=60.0)
    heard = noise.hear(world, (20.0, 20.0, 4.5), north)
    assert heard is not None
    assert heard[1] == pytest.approx(math.pi / 2, abs=1e-6)


def test_the_envelope_carries_a_bearing_and_never_a_position():
    """The security property, and the reason audibility is resolved server-side at
    all: a packet must not contain the coordinates of someone you cannot hear —
    nor, for someone you can, coordinates precise enough to draw."""
    world = flat_world(64, floor=0, ceil=16)
    audible = Noise(kind="step", source="them", x=24.0, y=20.0, z=0.0, loudness=60.0)
    inaudible = Noise(kind="step", source="far", x=62.0, y=62.0, z=0.0, loudness=8.0)
    out = noise.envelope(world, (20.0, 20.0, 4.5), "me", [audible, inaudible])
    assert len(out) == 1
    assert set(out[0]) == {"kind", "volume", "bearing", "up"}


def test_a_shot_says_which_weapon_and_a_footstep_does_not():
    """The listener hears *which* gun, which is what makes a sniper round two
    rooms away a decision rather than a noise. A footstep has no weapon, and
    sending an empty string rather than omitting the key would make every client
    special-case a value the wire had no reason to carry."""
    world = flat_world(64, floor=0, ceil=16)
    shot = Noise(
        kind="shot", source="them", x=24.0, y=20.0, z=0.0, loudness=90.0, weapon="sniper"
    )
    step = Noise(kind="step", source="them", x=24.0, y=20.0, z=0.0, loudness=60.0)
    out = noise.envelope(world, (20.0, 20.0, 4.5), "me", [shot, step])
    assert out[0]["weapon"] == "sniper"
    assert "weapon" not in out[1]


def test_your_own_noises_are_not_sent_back_to_you():
    """They need no round trip — the client makes them locally — and a footstep
    that arrives 50 ms late does not sound like a footstep."""
    world = flat_world(64, floor=0, ceil=16)
    mine = Noise(kind="step", source="me", x=20.5, y=20.0, z=0.0, loudness=60.0)
    assert noise.envelope(world, (20.0, 20.0, 4.5), "me", [mine]) == []


def test_a_noise_above_and_below_is_flagged():
    world = flat_world(64, floor=0, ceil=64)
    upstairs = Noise(kind="step", source="them", x=21.0, y=20.0, z=20.0, loudness=60.0)
    heard = noise.hear(world, (20.0, 20.0, 4.5), upstairs)
    assert heard is not None and heard[2] == 1


def test_a_knife_is_quieter_than_a_rifle():
    """Swinging a knife quietly is the entire reason to carry one."""
    knife = weapons.WEAPON_BY_ID["knife"]
    sniper = weapons.WEAPON_BY_ID["sniper"]
    assert noise.shot_loudness(knife) < noise.shot_loudness(sniper) / 4


# ---------------------------------------------------------------------------
# What movement announces
# ---------------------------------------------------------------------------


def _run(room: MatchRoom, player, frames: int, **kw) -> list[Noise]:
    """Simulate `frames` commands and return every noise they produced."""
    heard: list[Noise] = []
    seq = player.high_seq
    for _ in range(frames):
        seq += 1
        room.enqueue(player, move(seq, **kw))
        player.budget = 1.0
        room.simulate(1 / 60)
        heard.extend(room.noises)
        room.noises.clear()
    return heard


def test_running_makes_footsteps():
    room = make_room()
    player = room.add("runner", None)
    place(player, 20.0, 20.0)
    made = _run(room, player, 90, forward=1.0)
    assert [n for n in made if n.kind == "step"]


def test_crouching_makes_none():
    """The crouch payoff. Without this, AC's 40% speed penalty is a tax rather than
    a trade, and there is no reason to ever press the key."""
    room = make_room()
    player = room.add("sneak", None)
    place(player, 20.0, 20.0)
    made = _run(room, player, 120, forward=1.0, crouch=True)
    assert [n for n in made if n.kind == "step"] == []


def test_footsteps_are_paced_by_distance_not_time():
    """A player barely moving is barely audible, which is what makes creeping
    forward an option even standing up."""
    room = make_room()
    fast = room.add("fast", None)
    slow = room.add("slow", None)
    place(fast, 20.0, 20.0)
    place(slow, 40.0, 40.0)
    quick = [n for n in _run(room, fast, 120, forward=1.0) if n.source == fast.id]
    # A tenth of the input, so a tenth of the ground and far fewer steps.
    crawl = [n for n in _run(room, slow, 120, forward=0.1) if n.source == slow.id]
    assert len([n for n in quick if n.kind == "step"]) > len(
        [n for n in crawl if n.kind == "step"]
    )


def test_jumping_and_landing_are_both_audible():
    room = make_room()
    player = room.add("hopper", None)
    place(player, 20.0, 20.0)
    made = _run(room, player, 1, jump=True) + _run(room, player, 60)
    kinds = {n.kind for n in made}
    assert "jump" in kinds
    assert "land" in kinds


def test_firing_is_the_loudest_thing_you_can_do():
    room = make_room()
    player = room.add("shooter", None)
    place(player, 20.0, 20.0)
    made = _run(room, player, 1, fire=True)
    shots = [n for n in made if n.kind == "shot"]
    assert shots
    assert shots[0].loudness > noise.STRIDE_LOUDNESS


# ---------------------------------------------------------------------------
# Weapon kickback
# ---------------------------------------------------------------------------


def test_kickback_pushes_opposite_the_aim():
    shotgun = weapons.WEAPON_BY_ID["shotgun"]
    # Aiming straight down (pitch -90°) must push straight up: the shoot-jump.
    kick = weapons.kick_vector(shotgun, 0.0, -math.pi / 2)
    assert kick[2] == pytest.approx(shotgun.kickback, abs=1e-6)
    # Aiming east pushes west.
    kick = weapons.kick_vector(shotgun, 0.0, 0.0)
    assert kick[0] == pytest.approx(-shotgun.kickback, abs=1e-6)


def test_crouching_braces_the_shot():
    shotgun = weapons.WEAPON_BY_ID["shotgun"]
    standing = weapons.kick_vector(shotgun, 0.0, 0.0, crouching=False)
    crouched = weapons.kick_vector(shotgun, 0.0, 0.0, crouching=True)
    assert abs(crouched[0]) == pytest.approx(
        abs(standing[0]) * weapons.CROUCH_KICK_SCALE, rel=1e-6
    )


def test_a_knife_has_no_kickback():
    assert weapons.kick_vector(weapons.WEAPON_BY_ID["knife"], 0.0, -1.5) == (
        0.0,
        0.0,
        0.0,
    )


def test_firing_down_launches_the_shooter():
    """The shoot-jump, end to end through the match server rather than through
    `apply_impulse` alone — this is the path a real shot takes."""
    room = make_room()
    player = room.add("jumper", None)
    place(player, 20.0, 20.0)
    player.weapon = 3  # shotgun
    player.ammo[3] = 7
    room.enqueue(player, move(1, fire=True, pitch=-math.pi / 2))
    player.budget = 1.0
    room.simulate(1 / 60)
    assert player.state.vel_z > 0
    assert player.state.on_ground is False


def test_an_automatic_cannot_fly_because_gravity_outpaces_it():
    """Why kickback needs no special-case cap: at 700 rpm the rifle gives back
    less per shot than gravity takes between them."""
    rifle = weapons.WEAPON_BY_ID["assault"]
    per_shot = rifle.kickback
    lost_between_shots = physics.GRAVITY * rifle.interval
    assert lost_between_shots > per_shot


# ---------------------------------------------------------------------------
# Fall damage
# ---------------------------------------------------------------------------


def test_a_resting_player_is_never_charged_fall_damage():
    room = make_room()
    player = room.add("stander", None)
    place(player, 20.0, 20.0)
    _run(room, player, 120)
    assert player.health == weapons.MAX_HEALTH


def test_a_plain_jump_is_free():
    room = make_room()
    player = room.add("hopper", None)
    place(player, 20.0, 20.0)
    _run(room, player, 1, jump=True)
    _run(room, player, 90)
    assert player.state.on_ground
    assert player.health == weapons.MAX_HEALTH


def test_a_long_drop_hurts_and_says_so_in_the_private_view():
    world = flat_world(64, floor=0, ceil=120)
    room = MatchRoom("drop", "testmap", world, [Spawn(20, 20)])
    player = room.add("faller", None)
    place(player, 20.0, 20.0, z=90.0)
    player.state.on_ground = False
    _run(room, player, 240)
    assert player.health < weapons.MAX_HEALTH
    # Reported once, to the player it happened to, and then drained.
    view = player.private_view(0.0)
    assert view["fell"] > 0
    assert player.private_view(0.0)["fell"] == 0


def test_a_fatal_fall_has_no_killer():
    """A death by map is a death with no kill. `_apply_damage` cannot express that
    — it needs an attacker for the feed, the hitmarker and the score."""
    world = flat_world(64, floor=0, ceil=120)
    room = MatchRoom("drop2", "testmap", world, [Spawn(20, 20)])
    player = room.add("faller", None)
    place(player, 20.0, 20.0, z=110.0)
    player.state.on_ground = False
    _run(room, player, 300)
    assert player.alive is False
    assert player.deaths == 1
    assert room.scores == [0, 0]
    kills = [f for f in room.fx if f["kind"] == "kill"]
    assert kills and kills[0]["killer"] == ""
    assert kills[0]["weapon"] == "fall"


# ---------------------------------------------------------------------------
# Crouching on the wire
# ---------------------------------------------------------------------------


def test_a_crouched_player_presents_a_shorter_hitbox():
    room = make_room()
    shooter = room.add("shooter", None, team=0)
    victim = room.add("victim", None, team=1)
    place(shooter, 20.0, 20.0)
    place(victim, 30.0, 20.0)
    victim.state.crouch = 1.0

    # Aimed at where a standing head would be, which is now above them.
    origin = weapons.eye_position(20.0, 20.0, 0.0)
    high = STANDING_HEIGHT - 0.2
    direction = (1.0, 0.0, (high - origin[2]) / 10.0)
    length = math.hypot(direction[0], direction[2])
    direction = (direction[0] / length, 0.0, direction[2] / length)

    standing = weapons.resolve_shot(
        room.world,
        weapons.WEAPON_BY_ID["sniper"],
        origin,
        direction,
        {victim.id: (30.0, 20.0, 0.0)},
        room.rng,
    )
    crouched = weapons.resolve_shot(
        room.world,
        weapons.WEAPON_BY_ID["sniper"],
        origin,
        direction,
        {victim.id: (30.0, 20.0, 0.0)},
        room.rng,
        heights={victim.id: CROUCH_HEIGHT},
    )
    assert standing.hits
    assert not crouched.hits


def test_the_crouch_rewind_uses_the_height_the_shooter_saw():
    """The same reason positions are rewound: a shooter who hit a standing head
    must not be told they missed because the target crouched since."""
    history = weapons.PositionHistory()
    history.record(1000.0, {"p": (0.0, 0.0, 0.0)}, {"p": STANDING_HEIGHT})
    history.record(1100.0, {"p": (0.0, 0.0, 0.0)}, {"p": CROUCH_HEIGHT})
    assert history.rewind_heights(1000.0)["p"] == pytest.approx(STANDING_HEIGHT)
    assert history.rewind_heights(1050.0)["p"] == pytest.approx(
        (STANDING_HEIGHT + CROUCH_HEIGHT) / 2
    )


def test_heights_are_optional_and_default_to_standing():
    """Frames recorded without them predate crouch and are standing frames."""
    history = weapons.PositionHistory()
    history.record(1000.0, {"p": (0.0, 0.0, 0.0)})
    assert history.rewind_heights(1000.0) == {}


def test_a_crouched_shot_leaves_from_the_lower_eye():
    """Otherwise a crouched player fires through their own cover."""
    room = make_room()
    player = room.add("crouched", None)
    place(player, 20.0, 20.0)
    player.state.crouch = 1.0
    assert physics.eye_height(player.state) < physics.PLAYER_EYE_HEIGHT

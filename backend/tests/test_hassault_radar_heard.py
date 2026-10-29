"""Gunfire on the radar, and the two envelope keys the sound blobs are drawn from.

The rule under test is the CoD/Battlefield one: an **unsuppressed** shot puts the
shooter's origin on the other side's radar for `HEARD_SECONDS`, but only if
somebody on that side could actually hear it. Each test pins a way of getting
that wrong which still produces a plausible-looking radar:

- the dot reaching a side nobody on which heard the shot (a map-wide wall hack);
- a suppressor that quietens the shot and still paints the dot;
- the shooter's own side seeing their own shot as a foe;
- a dot that never expires, or that tracks the shooter instead of the origin.

Hermetic, like the rest of the suite: every world is a synthetic `flat_world`.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.modules.hassault import match, modes, noise, physics
from backend.modules.hassault.match import Command, MatchRoom
from backend.modules.hassault.noise import Noise
from backend.modules.hassault.physics import flat_world


class Spawn:
    def __init__(self, x: float, y: float, team: int) -> None:
        self.x = x
        self.y = y
        self.z = 0.0
        self.yaw = 0.0
        self.attr2 = team


def make_room(ssize: int = 128) -> MatchRoom:
    return MatchRoom(
        "radar",
        "testmap",
        flat_world(ssize, floor=0, ceil=16),
        [Spawn(8, 8, 0), Spawn(100, 100, 1)],
        mode=modes.build("tdm"),
    )


def place(player, x: float, y: float) -> None:
    player.state.x = x
    player.state.y = y
    player.state.z = 0.0
    player.state.on_ground = True


def fire(room: MatchRoom, player) -> None:
    """One trigger pull, through the real command path."""
    seq = player.high_seq + 1
    room.enqueue(
        player,
        Command(
            seq=seq,
            forward=0.0,
            strafe=0.0,
            jump=False,
            yaw=0.0,
            pitch=0.0,
            dt=1 / 60,
            fire=True,
        ),
    )
    player.budget = 1.0
    room.simulate(1 / 60)


def setup(shooter_xy=(20.0, 20.0), listener_xy=(30.0, 20.0)):
    room = make_room()
    listener = room.add("listener", None, team=0)
    shooter = room.add("shooter", None, team=1)
    place(listener, *listener_xy)
    place(shooter, *shooter_xy)
    return room, listener, shooter


def test_an_unsuppressed_shot_in_earshot_puts_its_origin_on_the_radar():
    room, listener, shooter = setup()
    fire(room, shooter)
    heard = room.private_view_for(listener)["heard"]
    assert [h["id"] for h in heard] == [shooter.id]
    assert heard[0]["x"] == pytest.approx(20.0)
    assert heard[0]["y"] == pytest.approx(20.0)
    assert set(heard[0]) == {"id", "x", "y", "age"}


def test_the_dot_stays_where_the_shot_was_not_where_the_shooter_went():
    """A dot that followed the shooter would be a wall hack on a timer."""
    room, listener, shooter = setup()
    fire(room, shooter)
    place(shooter, 60.0, 60.0)
    heard = room.private_view_for(listener)["heard"]
    assert (heard[0]["x"], heard[0]["y"]) == (pytest.approx(20.0), pytest.approx(20.0))


def test_a_suppressed_shot_is_quieter_and_never_on_the_radar():
    room, listener, shooter = setup()
    shooter.attachments = {"muzzle": "suppressor"}
    fire(room, shooter)
    shots = [n for n in room.noises if n.kind == "shot"]
    assert shots, "a suppressed shot still makes a sound"
    room2, listener2, loud = setup()
    fire(room2, loud)
    plain = [n for n in room2.noises if n.kind == "shot"]
    assert shots[0].loudness == pytest.approx(plain[0].loudness * 0.40)
    assert room.private_view_for(listener)["heard"] == []


def test_a_shot_nobody_on_the_side_could_hear_paints_nothing():
    """The radar shares what the side heard, not what the server knows."""
    room, listener, shooter = setup(shooter_xy=(5.0, 5.0), listener_xy=(125.0, 125.0))
    fire(room, shooter)
    assert room.private_view_for(listener)["heard"] == []


def test_a_teammate_hearing_it_is_enough():
    """Shared like `spotted`: the teammate's ears paint it for the whole side."""
    room, far, shooter = setup(shooter_xy=(5.0, 5.0), listener_xy=(125.0, 125.0))
    mate = room.add("mate", None, team=0)
    place(mate, 12.0, 5.0)
    fire(room, shooter)
    assert [h["id"] for h in room.private_view_for(far)["heard"]] == [shooter.id]


def test_your_own_side_does_not_see_your_shot_as_a_foe():
    room, listener, shooter = setup()
    ally = room.add("ally", None, team=1)
    place(ally, 22.0, 20.0)
    fire(room, shooter)
    assert room.private_view_for(ally)["heard"] == []
    assert room.private_view_for(shooter)["heard"] == []


def test_the_dot_expires():
    room, listener, shooter = setup()
    fire(room, shooter)
    shot_at = room.heard_shots[0].at
    later = room.heard_by_side(now=shot_at + match.HEARD_SECONDS + 0.01)
    assert later[room.side_of(listener)] == []
    assert room.heard_shots == []


def test_a_knife_swing_is_not_gunfire():
    room, listener, shooter = setup()
    shooter.weapon = next(
        i for i, w in enumerate(match.weapons.WEAPONS) if w.id == "knife"
    )
    fire(room, shooter)
    assert room.private_view_for(listener)["heard"] == []


def test_firing_keeps_the_shooter_in_pvs_rows():
    """`cull_pvs_snapshot_rows` asked about `last_fire_time`, which nothing set,
    so a shooter behind a wall was culled like a silent one."""
    room, listener, shooter = setup()
    room.pvs_culling = True
    row = {"id": shooter.id, "team": 1, "x": 20.0, "y": 20.0, "z": 0.0}
    culled = room.cull_pvs_snapshot_rows(listener, [dict(row)], {})
    assert culled[0].get("occluded") is True
    fire(room, shooter)
    culled = room.cull_pvs_snapshot_rows(listener, [dict(row)], {})
    assert "occluded" not in culled[0]


# ---------------------------------------------------------------------------
# The envelope keys the sound blobs are drawn from
# ---------------------------------------------------------------------------


def test_foe_is_set_only_for_the_other_side():
    world = flat_world(64, floor=0, ceil=16)
    enemy = Noise(
        kind="step", source="e", x=24.0, y=20.0, z=0.0, loudness=42.0, source_team=1
    )
    mate = Noise(
        kind="step", source="m", x=24.0, y=20.0, z=0.0, loudness=42.0, source_team=0
    )
    out = noise.envelope(
        world, (20.0, 20.0, 4.5), "me", [enemy, mate], listener_team=0, teams=True
    )
    assert out[0].get("foe") is True
    assert "foe" not in out[1]


def test_without_teams_everybody_else_is_a_foe():
    world = flat_world(64, floor=0, ceil=16)
    other = Noise(
        kind="step", source="o", x=24.0, y=20.0, z=0.0, loudness=42.0, source_team=0
    )
    out = noise.envelope(
        world, (20.0, 20.0, 4.5), "me", [other], listener_team=0, teams=False
    )
    assert out[0].get("foe") is True


def test_est_is_coarse():
    """`est` is a distance band, never a coordinate."""
    world = flat_world(64, floor=0, ceil=16)
    for x in (22.0, 27.3, 33.9, 41.0):
        here = Noise(kind="step", source="e", x=x, y=20.0, z=0.0, loudness=42.0)
        out = noise.envelope(world, (20.0, 20.0, 4.5), "me", [here])
        assert out, x
        assert out[0]["est"] % noise.EST_STEP == 0


def test_a_wall_makes_a_noise_sound_farther():
    """The blob is a drawing of what you heard: muffled reads as distant."""
    world = flat_world(64, floor=0, ceil=16)
    types = bytearray(world.type)
    for y in range(64):
        types[y * 64 + 20] = 0
    walled = physics.World(
        ssize=64,
        type=bytes(types),
        floor=world.floor,
        ceil=world.ceil,
        vdelta=world.vdelta,
    )
    here = Noise(kind="step", source="e", x=14.0, y=30.0, z=0.0, loudness=60.0)
    open_est = noise.envelope(world, (26.0, 30.0, 4.5), "me", [here])[0]["est"]
    wall_est = noise.envelope(walled, (26.0, 30.0, 4.5), "me", [here])[0]["est"]
    assert wall_est > open_est


# ---------------------------------------------------------------------------
# The own-noise ring's radii, pinned against the browser's copy
# ---------------------------------------------------------------------------

RADAR_VECTORS = (
    Path(__file__).resolve().parents[2]
    / "packages/core/src/modules/hassault/__tests__/radar-vectors.json"
)


def test_the_radars_own_noise_radii_are_the_servers():
    """`radar-view.ts` copies these to size the ring round your arrow. A ring
    that said you carry less far than you do teaches the wrong habit."""
    v = json.loads(RADAR_VECTORS.read_text(encoding="utf-8"))["noise"]
    assert v["ownRadii"] == {
        "step": noise.STRIDE_LOUDNESS,
        "land": noise.LAND_LOUDNESS,
        "jump": noise.JUMP_LOUDNESS,
        "reload": noise.RELOAD_LOUDNESS,
        "splash": noise.SPLASH_LOUDNESS,
        "pickup": noise.PICKUP_LOUDNESS,
    }
    for row in v["shots"]:
        gun = SimpleNamespace(kickback=1.0, range=100.0, damage=row["damage"])
        assert noise.shot_loudness(gun) == pytest.approx(row["radius"])
    knife = SimpleNamespace(kickback=0.0, range=2.0, damage=50)
    assert noise.shot_loudness(knife) == pytest.approx(v["knife"])
    assert v_heard_seconds() == match.HEARD_SECONDS


def v_heard_seconds() -> float:
    return json.loads(RADAR_VECTORS.read_text(encoding="utf-8"))["constants"][
        "heardSeconds"
    ]

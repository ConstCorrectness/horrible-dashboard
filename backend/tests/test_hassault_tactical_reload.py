"""Tests for tactical (retaining chambered round) vs dry (empty magazine) reloads."""

from backend.modules.hassault import weapons
from backend.modules.hassault.match import Command, MatchPlayer, MatchRoom
from backend.modules.hassault.physics import flat_world


class Spawn:
    def __init__(self, x: float, y: float, team: int = 0) -> None:
        self.x = x
        self.y = y
        self.z = 0.0
        self.yaw = 0.0
        self.attr2 = team


def _room_with_player() -> tuple[MatchRoom, MatchPlayer]:
    world = flat_world(32, floor=0, ceil=16)
    spawns = [Spawn(8, 8, team=0), Spawn(20, 20, team=1)]
    room = MatchRoom("tactical_test", "duel", world, spawns)
    player = room.add("Player1", None)
    return room, player


def cmd(seq: int = 1, dt: float = 0.016, reload: bool = False, weapon: int = -1) -> Command:
    return Command(
        seq=seq,
        forward=0.0,
        strafe=0.0,
        jump=False,
        yaw=0.0,
        pitch=0.0,
        dt=dt,
        reload=reload,
        weapon=weapon,
    )


def test_tactical_reload_timing_and_state():
    room, player = _room_with_player()
    player.weapon = 2
    w = weapons.weapon_at(2)

    player.ammo[2] = 5
    player.reserve[2] = 60

    room.enqueue(player, cmd(seq=1, reload=True, dt=0.016))
    room.simulate(0.016)

    assert player.reload_until > player.sim_time
    expected_duration = w.reload_time
    actual_duration = player.reload_until - (player.sim_time - 0.016)
    assert abs(actual_duration - expected_duration) < 0.02
    assert not player.reloading_empty

    state = player.private_view(0.0)
    assert state["reloading"] is True
    assert state["reloadingEmpty"] is False


def test_empty_reload_timing_and_state():
    room, player = _room_with_player()
    player.weapon = 2
    w = weapons.weapon_at(2)

    player.ammo[2] = 0
    player.reserve[2] = 60

    room.enqueue(player, cmd(seq=1, reload=True, dt=0.016))
    room.simulate(0.016)

    assert player.reload_until > player.sim_time
    expected_duration = w.reload_time * 1.25
    actual_duration = player.reload_until - (player.sim_time - 0.016)
    assert abs(actual_duration - expected_duration) < 0.02
    assert player.reloading_empty is True

    state = player.private_view(0.0)
    assert state["reloading"] is True
    assert state["reloadingEmpty"] is True


def test_empty_reload_completes():
    room, player = _room_with_player()
    player.weapon = 2
    w = weapons.weapon_at(2)

    player.ammo[2] = 0
    player.reserve[2] = 60

    room.enqueue(player, cmd(seq=1, reload=True, dt=0.016))
    room.simulate(0.016)

    duration = w.reload_time * 1.25
    steps = int(duration / 0.05) + 3
    for s in range(steps):
        room.enqueue(player, cmd(seq=s + 2, dt=0.05))
        room.simulate(0.05)

    assert player.ammo[2] == w.mag
    assert player.reloading_empty is False
    state = player.private_view(0.0)
    assert state["reloading"] is False
    assert state["reloadingEmpty"] is False


def test_switch_weapon_cancels_empty_reload():
    room, player = _room_with_player()
    player.weapon = 2

    player.ammo[2] = 0
    player.reserve[2] = 60

    room.enqueue(player, cmd(seq=1, reload=True, dt=0.016))
    room.simulate(0.016)
    assert player.reloading_empty is True

    room.enqueue(player, cmd(seq=2, weapon=1, dt=0.016))
    room.simulate(0.016)

    assert player.reloading_empty is False
    assert player.reload_until == -999.0

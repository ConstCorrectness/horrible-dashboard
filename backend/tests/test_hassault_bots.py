"""Bot players.

The load-bearing claim of `bots.py` is that a bot is not a second kind of entity:
its input goes through `MatchRoom.enqueue` and is validated, budgeted and
simulated exactly like a browser's. Several tests here exist only to pin that —
if a future change lets bots bypass the queue, they stop being players and start
being a second physics implementation, which is the mistake the whole module is
arranged to avoid.

Hermetic, like the rest of the suite: synthetic worlds only.
"""

from __future__ import annotations

import math
import time

import pytest

from backend.modules.hassault import bots, weapons
from backend.modules.hassault.cgz import SOLID
from backend.modules.hassault.match import (
    MAX_PLAYERS,
    MAX_QUEUED_COMMANDS,
    MatchRoom,
)
from backend.modules.hassault.physics import MAX_STEP_DT, MOVE_SPEED, World, flat_world


class Spawn:
    def __init__(
        self, x: float, y: float, z: float = 0.0, yaw: float = 0.0, team: int = 0
    ) -> None:
        self.x = x
        self.y = y
        self.z = z
        self.yaw = yaw
        self.attr2 = team


def walled_world(ssize: int = 48, wall_x: int = 16) -> World:
    """A flat room split by a solid wall, so line of sight can be broken."""
    world = flat_world(ssize)
    types = bytearray(world.type)
    for y in range(ssize):
        types[y * ssize + wall_x] = SOLID
    return World(
        ssize=ssize,
        type=bytes(types),
        floor=world.floor,
        ceil=world.ceil,
        vdelta=world.vdelta,
    )


def make_room(world: World | None = None, room_id: str = "b1") -> MatchRoom:
    return MatchRoom(
        room_id,
        "testmap",
        world or flat_world(48),
        [Spawn(8, 8, team=0), Spawn(30, 30, team=1), Spawn(8, 30, team=0)],
    )


def place(player, x: float, y: float, yaw: float = 0.0) -> None:
    player.state.x = x
    player.state.y = y
    player.state.z = 0.0
    player.state.yaw = yaw
    player.state.pitch = 0.0
    player.protected_until = 0.0


# ---------------------------------------------------------------------------
# Fielding them
# ---------------------------------------------------------------------------


def test_bots_are_named_and_balanced_like_arriving_players():
    room = make_room()
    human = room.add("me", None)
    added = bots.add_bots(room, 2)
    assert [b.name for b in added] == ["[bot] Rook", "[bot] Vex"]
    # One enemy and one ally, which is what balancing means with a human already
    # on team 0.
    assert added[0].team != human.team
    assert added[1].team == human.team


def test_a_forced_team_stacks_them_all_one_way():
    room = make_room()
    room.add("me", None, team=0)
    added = bots.add_bots(room, 3, team=1)
    assert {b.team for b in added} == {1}


def test_bots_cannot_overfill_a_match():
    room = make_room()
    added = bots.add_bots(room, MAX_PLAYERS + 10)
    assert len(added) == MAX_PLAYERS
    assert len(room.players) == MAX_PLAYERS


def test_bots_alone_do_not_keep_a_room_alive():
    """A match with nobody in it is a screensaver. The empty clock tracks humans,
    not bodies, or a room seeded with bots would never be retired."""
    room = make_room()
    bots.add_bots(room, 3)
    assert room.empty_since is not None
    assert room.humans == []
    human = room.add("me", None)
    assert room.empty_since is None
    room.remove(human.id)
    assert room.empty_since is not None


def test_removing_bots_takes_the_newest_first():
    """The undo of "add three more" is removing those three, not the ones who
    have been playing since the match opened."""
    room = make_room()
    first = bots.add_bots(room, 1)[0]
    time.sleep(0.002)
    second = bots.add_bots(room, 1)[0]
    assert room.remove_bots(1) == 1
    assert second.id not in room.players
    assert first.id in room.players


def test_removing_bots_with_no_count_removes_all_of_them():
    room = make_room()
    human = room.add("me", None)
    bots.add_bots(room, 4)
    assert room.remove_bots() == 4
    assert list(room.players) == [human.id]


def test_an_unknown_skill_falls_back_rather_than_failing():
    brain = bots.BotBrain(skill="nightmare")
    assert brain.skill.name == bots.DEFAULT_SKILL


# ---------------------------------------------------------------------------
# What a bot produces
# ---------------------------------------------------------------------------


def test_a_bot_produces_input_a_browser_could_have_sent():
    """The axes are clamped on the wire for humans; a bot that exceeded them
    would be moving faster than a player can, through the same physics."""
    room = make_room()
    bot = bots.add_bots(room, 1)[0]
    command = bot.brain.think(room, bot, 1 / 20)
    assert command is not None
    assert -1.0 <= command.forward <= 1.0
    assert -1.0 <= command.strafe <= 1.0
    assert 0 < command.dt <= MAX_STEP_DT
    assert command.seq > 0
    # No rewind: its input is produced on this tick, so the world it saw is the
    # world as it is.
    assert command.view_t is None


def test_a_bots_sequence_numbers_advance_so_the_queue_accepts_them():
    """`enqueue` drops anything at or below the highest sequence seen. A bot that
    reused a number would move once and then stand still forever."""
    room = make_room()
    bot = bots.add_bots(room, 1)[0]
    seqs = [bot.brain.think(room, bot, 1 / 20).seq for _ in range(5)]
    assert seqs == sorted(set(seqs))
    for seq in seqs:
        room.enqueue(bot, _cmd(bot, seq))
    assert len(bot.queue) == len(seqs)


def _cmd(bot, seq: int):
    from backend.modules.hassault.match import Command

    return Command(
        seq=seq, forward=0.0, strafe=0.0, jump=False, yaw=0.0, pitch=0.0, dt=1 / 20
    )


def test_a_dead_bot_says_nothing():
    room = make_room()
    bot = bots.add_bots(room, 1)[0]
    bot.alive = False
    assert bot.brain.think(room, bot, 1 / 20) is None


def test_a_bot_walks_when_it_is_ticked_through_the_room():
    room = make_room()
    bot = bots.add_bots(room, 1)[0]
    place(bot, 24.0, 24.0)
    start = (bot.state.x, bot.state.y)
    for _ in range(20):
        room.simulate(1 / 20)
    assert math.dist(start, (bot.state.x, bot.state.y)) > 2.0


def test_a_bot_is_bound_by_the_same_time_budget_as_a_player():
    """The reason bots go through `enqueue` at all. A second input path is a
    second place for the speed cap to not exist."""
    room = make_room()
    bot = bots.add_bots(room, 1)[0]
    place(bot, 24.0, 24.0)
    start = (bot.state.x, bot.state.y)
    ticks, dt = 20, 1 / 20
    for _ in range(ticks):
        room.simulate(dt)
    travelled = math.dist(start, (bot.state.x, bot.state.y))
    # Generous: it turns, it strafes, it avoids walls. What it cannot do is cover
    # more ground than a player running in a straight line for the same time.
    assert travelled <= MOVE_SPEED * ticks * dt * 1.2


def test_a_bots_queue_cannot_grow_without_bound():
    room = make_room()
    bot = bots.add_bots(room, 1)[0]
    # Think many times without simulating, as a stalled loop would.
    for _ in range(MAX_QUEUED_COMMANDS + 40):
        command = bot.brain.think(room, bot, 1 / 20)
        room.enqueue(bot, command)
    assert len(bot.queue) == MAX_QUEUED_COMMANDS


# ---------------------------------------------------------------------------
# Fighting
# ---------------------------------------------------------------------------


def test_a_bot_turns_towards_an_enemy_it_can_see():
    room = make_room()
    target = room.add("victim", None, team=1)
    bot = bots.add_bots(room, 1, skill="hard", team=0)[0]
    place(bot, 10.0, 10.0, yaw=0.3)
    place(target, 30.0, 10.0)
    before = abs(bot.state.yaw)
    for _ in range(8):
        command = bot.brain.think(room, bot, 1 / 20)
        bot.state.yaw = command.yaw
    # The target is due east, at yaw 0.
    assert abs(bot.state.yaw) < before


def test_a_bot_eventually_shoots_an_enemy_standing_in_front_of_it():
    room = make_room()
    target = room.add("victim", None, team=1)
    bot = bots.add_bots(room, 1, skill="hard", team=0)[0]
    place(bot, 10.0, 10.0)
    place(target, 26.0, 10.0)
    for _ in range(40):
        place(target, 26.0, 10.0)  # hold them still; this is an aim test
        room.simulate(1 / 20)
        if target.health < weapons.MAX_HEALTH:
            break
    assert target.health < weapons.MAX_HEALTH


def test_a_bot_does_not_shoot_through_a_wall():
    """It checks line of sight with the same raycast a shot traces, so what it
    believes it can hit and what it can hit are the same thing."""
    room = make_room(walled_world())
    target = room.add("victim", None, team=1)
    bot = bots.add_bots(room, 1, skill="hard", team=0)[0]
    place(bot, 10.0, 10.0)
    place(target, 24.0, 10.0)
    for _ in range(40):
        place(target, 24.0, 10.0)
        room.simulate(1 / 20)
    assert target.health == weapons.MAX_HEALTH
    assert bot.brain.target_id is None


def test_a_bot_ignores_an_enemy_behind_it():
    room = make_room()
    target = room.add("victim", None, team=1)
    bot = bots.add_bots(room, 1, skill="hard", team=0)[0]
    place(bot, 10.0, 10.0, yaw=math.pi)  # facing away
    place(target, 30.0, 10.0)
    assert bot.brain._visible(room, bot, target) is False


def test_a_bot_never_targets_a_teammate():
    room = make_room()
    friend = room.add("friend", None, team=0)
    bot = bots.add_bots(room, 1, skill="hard", team=0)[0]
    place(bot, 10.0, 10.0)
    place(friend, 26.0, 10.0)
    for _ in range(10):
        bot.brain.think(room, bot, 1 / 20)
    assert bot.brain.target_id is None


def test_a_bot_reloads_an_empty_weapon():
    room = make_room()
    target = room.add("victim", None, team=1)
    bot = bots.add_bots(room, 1, skill="hard", team=0)[0]
    place(bot, 10.0, 10.0)
    place(target, 26.0, 10.0)
    bot.ammo[bot.weapon] = 0
    command = None
    for _ in range(10):
        command = bot.brain.think(room, bot, 1 / 20)
        if command.reload:
            break
    assert command is not None and command.reload is True


def test_a_bot_steers_around_a_wall_instead_of_into_it():
    """No navmesh; it probes ahead with the movement code's own `can_stand`, so
    the heading it commits to is one it can actually walk."""
    room = make_room(walled_world(wall_x=14))
    bot = bots.add_bots(room, 1)[0]
    place(bot, 10.0, 10.0)
    bot.brain.roam = (40.0, 10.0)  # straight through the wall
    bot.brain.roam_in = 60.0
    for _ in range(40):
        room.simulate(1 / 20)
    # It must not have ended up inside the wall, and it must not have simply
    # stood there grinding against it.
    assert bot.state.x < 14.0
    assert math.dist((10.0, 10.0), (bot.state.x, bot.state.y)) > 1.0


def test_roaming_heads_for_the_enemy_half():
    """Four bots wandering uniformly on a 256-cube map can spend a minute never
    meeting, which is the opposite of what "add some bots" is for. Enemy spawns
    are map knowledge, not player knowledge — nobody is being tracked."""
    room = MatchRoom(
        "r",
        "testmap",
        flat_world(64),
        [Spawn(10, 10, team=0), Spawn(12, 12, team=0), Spawn(50, 50, team=1)],
    )
    bot = bots.add_bots(room, 1, team=0)[0]
    place(bot, 10.0, 10.0)
    for _ in range(6):
        bot.brain._pick_roam(room, bot)
        assert bot.brain.roam == (50.5, 50.5)


def test_roaming_falls_back_when_a_map_has_no_enemy_spawns():
    """Deathmatch spawns are `attr2 == 100` in AC, and a few community maps have
    no team spawns at all. Both have to land somewhere walkable."""
    room = MatchRoom(
        "r",
        "testmap",
        flat_world(64),
        [Spawn(10, 10, team=100), Spawn(50, 50, team=100)],
    )
    bot = bots.add_bots(room, 1, team=0)[0]
    place(bot, 10.0, 10.0)
    bot.brain._pick_roam(room, bot)
    assert bot.brain.roam == (50.5, 50.5)


def test_a_map_with_no_spawns_at_all_still_gives_a_bot_somewhere_to_go():
    room = MatchRoom("r", "bare", flat_world(64), [])
    bot = bots.add_bots(room, 1)[0]
    place(bot, 30.0, 30.0)
    bot.brain._pick_roam(room, bot)
    assert bot.brain.roam is not None


def test_a_failing_brain_does_not_take_the_match_down_with_it():
    """A bot is a convenience; the match is not. An exception in one brain must
    cost that bot a tick, not everybody the room."""
    room = make_room()
    human = room.add("me", None)
    bot = bots.add_bots(room, 1)[0]

    def boom(*_args, **_kwargs):
        raise RuntimeError("bad think")

    bot.brain.think = boom  # type: ignore[method-assign]
    room.simulate(1 / 20)
    assert human.id in room.players
    assert bot.id in room.players


def test_bot_shots_are_resolved_live_rather_than_rewound():
    """A bot has no latency to compensate for, and letting it name a rewind
    instant would be handing the server's own code the one lever the clamp
    exists to police."""
    room = make_room()
    bot = bots.add_bots(room, 1)[0]
    command = bot.brain.think(room, bot, 1 / 20)
    assert command.view_t is None
    assert room.history.clamp(command.view_t, 1000.0) == 1000.0


def test_the_skill_levels_are_ordered_the_way_their_names_claim():
    easy, normal, hard = (bots.SKILLS[k] for k in ("easy", "normal", "hard"))
    assert easy.aim_error > normal.aim_error > hard.aim_error
    assert easy.turn_rate < normal.turn_rate < hard.turn_rate
    assert easy.reaction > normal.reaction > hard.reaction
    assert easy.view_range < normal.view_range < hard.view_range


def test_bots_appear_in_the_lobby_listing_as_bots():
    from backend.modules.hassault.match import MatchServer

    server = MatchServer()
    room = make_room()
    server.rooms[room.id] = room
    room.add("me", None)
    bots.add_bots(room, 2)
    row = server.listing()[0]
    assert row["players"] == 3
    assert row["bots"] == 2


def test_a_bot_row_is_flagged_on_the_wire():
    room = make_room()
    bot = bots.add_bots(room, 1)[0]
    assert bot.snapshot(time.monotonic())["bot"] is True
    assert room.add("me", None).snapshot(time.monotonic())["bot"] is False


@pytest.mark.parametrize("skill", sorted(bots.SKILLS))
def test_every_skill_level_produces_usable_input(skill: str):
    room = make_room()
    target = room.add("victim", None, team=1)
    bot = bots.add_bots(room, 1, skill=skill, team=0)[0]
    place(bot, 10.0, 10.0)
    place(target, 26.0, 10.0)
    for _ in range(20):
        room.simulate(1 / 20)
    assert bot.alive
    assert 0 <= bot.weapon < len(weapons.WEAPONS)

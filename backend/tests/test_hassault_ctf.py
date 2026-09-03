"""Capture the flag: the state machine, and the rules that were bugs first.

Driven on `hd_atrium` rather than a synthetic world, because the mode's whole
subject is *where things are* — a flag stand, a floor to drop onto, a distance to
cover — and a flat 32-cube test world would let every rule pass for the wrong
reason.
"""

from __future__ import annotations

import time

import pytest

from backend.modules.hassault import assets, modes, physics, pickups, weapons
from backend.modules.hassault.match import MatchRoom
from backend.modules.hassault.modes import objectives
from backend.modules.hassault.modes.ctf import DROP_RETURN_AFTER


def ctf_room(captures_to_win: int = 3) -> MatchRoom:
    cmap = assets.load_map("hd_atrium")
    world = physics.World.from_map(cmap)
    mode = modes.build("ctf")
    mode.captures_to_win = captures_to_win
    return MatchRoom(
        "t",
        "hd_atrium",
        world,
        cmap.spawns(),
        pickups.place(world, cmap.entities),
        mode=mode,
        objectives=objectives.place(world, cmap),
    )


def stand_on(player, where) -> None:
    """Put a player exactly on a position, wherever it is."""
    player.state.x, player.state.y, player.state.z = where


def kill(room: MatchRoom, victim, killer) -> None:
    victim.protected_until = 0.0
    room._apply_damage(
        victim, killer, 500.0, False, weapons.weapon_at(0), time.monotonic()
    )


@pytest.fixture
def game():
    room = ctf_room()
    alice = room.add("alice", None, team=0)
    bob = room.add("bob", None, team=1)
    return room, alice, bob, room.mode


# ---------------------------------------------------------------------------
# Taking, carrying, capturing
# ---------------------------------------------------------------------------


def test_a_flag_starts_home_on_its_own_stand():
    room = ctf_room()
    for team, flag in room.mode.flags.items():
        assert flag.at_home
        assert flag.position(room) == (flag.home.x, flag.home.y, flag.home.z)
        assert flag.team == team


def test_walking_onto_the_enemy_flag_takes_it_without_pressing_anything(game):
    """Automatic on contact, deliberately. A player who has to press a key for a
    flag they just ran onto will not notice the flag is there."""
    room, alice, _bob, mode = game
    enemy = mode.flags[1]
    stand_on(alice, (enemy.home.x, enemy.home.y, enemy.home.z))
    room.simulate(0.05)
    assert enemy.state == "carried"
    assert enemy.carrier == alice.id
    assert [f["kind"] for f in room.fx] == ["flag_take"]


def test_a_carried_flag_is_wherever_its_carrier_is(game):
    """Derived rather than stored: a stored copy of a position that already moves
    twenty times a second disagrees for a tick every time somebody is killed."""
    room, alice, _bob, mode = game
    enemy = mode.flags[1]
    stand_on(alice, (enemy.home.x, enemy.home.y, enemy.home.z))
    room.simulate(0.05)
    stand_on(alice, (63.5, 40.0, 0.0))
    assert enemy.position(room) == (63.5, 40.0, 0.0)


def test_carrying_it_to_your_own_stand_scores(game):
    room, alice, _bob, mode = game
    enemy, mine = mode.flags[1], mode.flags[0]
    stand_on(alice, (enemy.home.x, enemy.home.y, enemy.home.z))
    room.simulate(0.05)
    room.fx.clear()
    stand_on(alice, (mine.home.x, mine.home.y, mine.home.z))
    room.simulate(0.05)
    assert room.scores[0] == 1
    assert alice.objectives == 1
    assert enemy.at_home, "the captured flag did not go back to its stand"
    assert "capture" in [f["kind"] for f in room.fx]


def test_you_cannot_capture_while_your_own_flag_is_out(game):
    """Without this rule the mode is two teams running past each other, and the
    faster one wins every time with no defence played at all."""
    room, alice, bob, mode = game
    enemy, mine = mode.flags[1], mode.flags[0]

    # Bob takes ours first.
    stand_on(bob, (mine.home.x, mine.home.y, mine.home.z))
    room.simulate(0.05)
    assert mine.state == "carried"

    # Alice takes theirs and runs it home anyway.
    stand_on(alice, (enemy.home.x, enemy.home.y, enemy.home.z))
    room.simulate(0.05)
    stand_on(alice, (mine.home.x, mine.home.y, mine.home.z))
    room.simulate(0.05)
    assert room.scores[0] == 0, "a capture landed with our own flag out"
    assert enemy.carrier == alice.id, "she should still be holding it"


# ---------------------------------------------------------------------------
# Dropping and returning
# ---------------------------------------------------------------------------


def test_being_killed_drops_the_flag_where_you_fell(game):
    """And it must be dropped *at the moment of death*, not in the next tick.

    `simulate` runs `_respawn_due` before `mode.tick`, so a player whose respawn
    timer has elapsed is alive again — and standing somewhere else — by the time
    a poll would see them. The flag would stay carried by a dead player, or land
    at their spawn.
    """
    room, alice, bob, mode = game
    enemy = mode.flags[1]
    stand_on(alice, (enemy.home.x, enemy.home.y, enemy.home.z))
    room.simulate(0.05)
    room.fx.clear()

    stand_on(alice, (63.5, 40.0, 0.0))
    kill(room, alice, bob)
    assert enemy.state == "dropped"
    assert (round(enemy.x, 1), round(enemy.y, 1)) == (63.5, 40.0)
    assert "flag_drop" in [f["kind"] for f in room.fx]


def test_walking_onto_your_own_dropped_flag_returns_it(game):
    room, alice, bob, mode = game
    enemy = mode.flags[1]
    stand_on(alice, (enemy.home.x, enemy.home.y, enemy.home.z))
    room.simulate(0.05)
    stand_on(alice, (63.5, 40.0, 0.0))
    kill(room, alice, bob)
    room.fx.clear()

    stand_on(bob, (enemy.x, enemy.y, enemy.z))
    room.simulate(0.05)
    assert enemy.at_home
    assert "flag_return" in [f["kind"] for f in room.fx]


def test_an_enemy_can_pick_a_dropped_flag_straight_back_up(game):
    """A dropped flag is a fight, which is the entire reason it lies there rather
    than teleporting home."""
    room, alice, bob, mode = game
    enemy = mode.flags[1]
    stand_on(alice, (enemy.home.x, enemy.home.y, enemy.home.z))
    room.simulate(0.05)
    stand_on(alice, (63.5, 40.0, 0.0))
    kill(room, alice, bob)

    carol = room.add("carol", None, team=0)
    stand_on(carol, (enemy.x, enemy.y, enemy.z))
    room.simulate(0.05)
    assert enemy.state == "carried"
    assert enemy.carrier == carol.id


def test_a_dropped_flag_returns_itself_eventually(game):
    """Left forever, a flag parked in a corner of the map stalls the match."""
    room, alice, bob, mode = game
    enemy = mode.flags[1]
    stand_on(alice, (enemy.home.x, enemy.home.y, enemy.home.z))
    room.simulate(0.05)
    stand_on(alice, (63.5, 40.0, 0.0))
    kill(room, alice, bob)
    assert enemy.state == "dropped"

    enemy.return_at = time.monotonic() - 1.0
    # Nobody near it, so this is the timer and not a touch.
    stand_on(alice, (10.0, 10.0, 0.0))
    stand_on(bob, (110.0, 110.0, 0.0))
    room.simulate(0.05)
    assert enemy.at_home
    assert enemy.return_at == 0.0
    assert DROP_RETURN_AFTER > 0


def test_a_carrier_who_quits_drops_the_flag_rather_than_taking_it_with_them(game):
    """Deleting it would make quitting-while-carrying a way to deny a flag
    outright, and the players still in the room are owed the fight over it."""
    room, alice, _bob, mode = game
    enemy = mode.flags[1]
    stand_on(alice, (enemy.home.x, enemy.home.y, enemy.home.z))
    room.simulate(0.05)
    stand_on(alice, (63.5, 40.0, 0.0))

    room.remove(alice.id)
    assert enemy.state == "dropped"
    assert (round(enemy.x, 1), round(enemy.y, 1)) == (63.5, 40.0)


# ---------------------------------------------------------------------------
# Winning
# ---------------------------------------------------------------------------


def test_the_match_ends_at_the_capture_limit():
    room = ctf_room(captures_to_win=1)
    alice = room.add("alice", None, team=0)
    room.add("bob", None, team=1)
    mode = room.mode
    enemy, mine = mode.flags[1], mode.flags[0]

    stand_on(alice, (enemy.home.x, enemy.home.y, enemy.home.z))
    room.simulate(0.05)
    stand_on(alice, (mine.home.x, mine.home.y, mine.home.z))
    room.simulate(0.05)
    assert mode.over
    assert "match_over" in [f["kind"] for f in room.fx]


def test_a_kill_does_not_move_the_capture_score(game):
    """The base `on_kill` adds to the killer's team score, which is right for
    deathmatch and wrong under a scoreboard labelled "Captures".

    Inherited, it silently mixed the two: a bot match read 9-0 off six captures
    and three kills. Both numbers were real, which is exactly why it looked
    plausible — the only way to catch it is to check that the score *is* the
    capture count.
    """
    room, alice, bob, mode = game
    alice.protected_until = 0.0
    kill(room, alice, bob)
    assert room.scores == [0, 0], "a kill scored in a mode that counts captures"
    assert bob.kills == 1, "the kill itself must still be credited"


def test_the_score_equals_the_captures_over_a_whole_match(game):
    """The property the per-kill test above states one instance of."""
    room, alice, bob, mode = game
    mode.captures_to_win = 99
    enemy, mine = mode.flags[1], mode.flags[0]
    for _ in range(3):
        stand_on(alice, (enemy.home.x, enemy.home.y, enemy.home.z))
        room.simulate(0.05)
        stand_on(alice, (mine.home.x, mine.home.y, mine.home.z))
        room.simulate(0.05)
        kill(room, bob, alice)
    assert room.scores[0] == 3
    assert alice.objectives == 3


def test_the_mvp_is_the_best_contribution_not_the_most_kills(game):
    """In a mode about carrying a flag, the player who took every one of them and
    died doing it did more than the one who camped a corridor."""
    room, alice, bob, mode = game
    carol = room.add("carol", None, team=0)
    room.scores[:] = [3, 0]
    alice.objectives, alice.kills = 3, 1
    carol.objectives, carol.kills = 0, 5

    assert mode.outcome_for(room, alice) == (True, True)
    assert mode.outcome_for(room, carol)[0] is True
    assert mode.outcome_for(room, carol)[1] is False
    assert mode.outcome_for(room, bob) == (False, False)


# ---------------------------------------------------------------------------
# Wire
# ---------------------------------------------------------------------------


def test_the_stands_ride_once_with_the_welcome_and_the_flags_ride_per_tick(game):
    """Static configuration in the welcome, moving state in the shared view — the
    same split `items` and `itemsOut` already make."""
    room, _alice, _bob, _mode = game
    welcome = room.state_payload()["mode"]
    assert welcome["id"] == "ctf"
    assert welcome["scoreLabel"] == "Captures"
    assert len(welcome["stands"]) == 2
    shared = room.shared_view()["mode"]
    assert "stands" not in shared
    assert len(shared["flags"]) == 2


def test_the_carrier_is_public_because_the_flags_position_already_is(game):
    room, alice, _bob, mode = game
    enemy = mode.flags[1]
    stand_on(alice, (enemy.home.x, enemy.home.y, enemy.home.z))
    room.simulate(0.05)
    row = next(f for f in room.shared_view()["mode"]["flags"] if f["team"] == 1)
    assert row["state"] == "carried"
    assert row["by"] == alice.id


def test_whether_you_are_carrying_is_answered_in_your_own_envelope(game):
    room, alice, bob, mode = game
    enemy = mode.flags[1]
    stand_on(alice, (enemy.home.x, enemy.home.y, enemy.home.z))
    room.simulate(0.05)
    assert room.private_view_for(alice)["mode"]["carrying"] is True
    assert room.private_view_for(bob)["mode"]["carrying"] is False


# ---------------------------------------------------------------------------
# Bots
# ---------------------------------------------------------------------------


def test_a_bot_goes_for_the_enemy_flag_and_then_carries_it_home(game):
    room, alice, _bob, mode = game
    enemy, mine = mode.flags[1], mode.flags[0]

    goal = mode.bot_goal(room, alice)
    assert goal is not None
    assert (round(goal.x, 1), round(goal.y, 1)) == (enemy.home.x, enemy.home.y)

    stand_on(alice, (enemy.home.x, enemy.home.y, enemy.home.z))
    room.simulate(0.05)
    goal = mode.bot_goal(room, alice)
    assert goal is not None
    assert (round(goal.x, 1), round(goal.y, 1)) == (mine.home.x, mine.home.y)


def test_a_bot_chases_the_enemy_carrier_rather_than_their_stand(game):
    """Their position is public, so this is not the bot knowing something a
    player could not."""
    room, alice, bob, mode = game
    mine = mode.flags[0]
    stand_on(bob, (mine.home.x, mine.home.y, mine.home.z))
    room.simulate(0.05)
    assert mine.carrier == bob.id

    stand_on(bob, (70.0, 70.0, 0.0))
    goal = mode.bot_goal(room, alice)
    assert goal is not None
    assert (round(goal.x), round(goal.y)) == (70, 70)


def test_every_bot_goal_carries_a_timeout(game):
    """Without one, a bot stuck on concave geometry parks a whole team against a
    wall for the match — which reads as "the mode is broken" rather than as "the
    pathing is dumb"."""
    room, alice, _bob, mode = game
    goal = mode.bot_goal(room, alice)
    assert goal is not None and goal.expires_in > 0

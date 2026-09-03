"""Bomb defuse: the phase machine, and the round reset that is easy to get wrong.

Split in two on purpose, mirroring the code. `advance` is a pure function and is
tested as one — no room, no clock, no randomness — which is also what would let a
shared conformance fixture drive it from three languages. The wrapper is tested
against a real room on a real map, because what it does is exactly the part that
touches the world.
"""

from __future__ import annotations

import time
from dataclasses import replace

import pytest

from backend.modules.hassault import assets, modes, physics, pickups, weapons
from backend.modules.hassault.match import Command, MatchRoom
from backend.modules.hassault.modes import objectives
from backend.modules.hassault.modes.defuse import (
    DEFUSE_TIME,
    FREEZE,
    FREEZE_TIME,
    FUSE_TIME,
    HALF_AT,
    LIVE,
    OVER,
    PLANT_TIME,
    POST,
    POST_TIME,
    ROUND_TIME,
    ROUNDS_TO_WIN,
    WARMUP,
    Facts,
    RoundState,
    advance,
)

BOTH = Facts(attackers_alive=1, defenders_alive=1)


# ---------------------------------------------------------------------------
# The phase machine, as a pure function
# ---------------------------------------------------------------------------


def test_warmup_waits_for_both_sides_rather_than_counting_down():
    """A room that fills over a minute should not spend that minute counting down
    and then start with one player in it."""
    alone = Facts(attackers_alive=1, defenders_alive=0)
    state, emits = advance(RoundState(), 5.0, alone)
    assert state.phase == WARMUP
    assert not emits

    state, emits = advance(state, 0.05, BOTH)
    assert state.phase == FREEZE
    assert state.round == 1
    assert "round_start" in [e.kind for e in emits]


def test_freeze_becomes_live_and_live_gets_the_round_clock():
    state = advance(RoundState(), 0.05, BOTH)[0]
    assert state.remaining == pytest.approx(FREEZE_TIME)
    state, emits = advance(state, FREEZE_TIME, BOTH)
    assert state.phase == LIVE
    assert state.remaining == pytest.approx(ROUND_TIME)
    assert "round_live" in [e.kind for e in emits]


def live_round(round_number: int = 1) -> RoundState:
    state = advance(RoundState(), 0.05, BOTH)[0]
    return advance(state, FREEZE_TIME, BOTH)[0]


def test_a_plant_swaps_the_round_clock_for_the_fuse():
    """Which is what makes a plant with four seconds left a winning play rather
    than a wasted one."""
    state = live_round()
    state, _ = advance(state, 100.0, BOTH)  # nearly out of time
    assert state.remaining < 10.0
    state, emits = advance(state, 0.05, Facts(1, 1, planted_on="A"))
    assert state.bomb.state == "planted"
    assert state.bomb.fuse == pytest.approx(FUSE_TIME)
    assert "bomb_planted" in [e.kind for e in emits]
    # And the round did not then time out underneath it.
    state, _ = advance(state, 5.0, BOTH)
    assert state.phase == LIVE


def test_the_bomb_going_off_wins_the_round_for_the_attackers():
    state = live_round()
    state, _ = advance(state, 0.05, Facts(1, 1, planted_on="A"))
    state, emits = advance(state, FUSE_TIME + 0.1, BOTH)
    assert "bomb_exploded" in [e.kind for e in emits]
    assert state.phase == POST
    assert state.wins[state.attackers] == 1


def test_a_defuse_wins_the_round_for_the_defenders():
    state = live_round()
    state, _ = advance(state, 0.05, Facts(1, 1, planted_on="A"))
    attackers = state.attackers
    state, emits = advance(state, 0.05, Facts(1, 1, defused=True))
    assert "bomb_defused" in [e.kind for e in emits]
    assert state.wins[1 - attackers] == 1


def test_running_out_of_time_with_no_bomb_down_is_a_defender_win():
    state = live_round()
    attackers = state.attackers
    state, emits = advance(state, ROUND_TIME + 0.1, BOTH)
    assert "time_out" in [e.kind for e in emits]
    assert state.wins[1 - attackers] == 1


def test_wiping_the_attackers_after_a_plant_does_not_win_the_round():
    """The asymmetry that is the whole reason to plant early: once the bomb is
    down, killing everyone only means nobody is left to stop the defuse."""
    state = live_round()
    state, _ = advance(state, 0.05, Facts(1, 1, planted_on="A"))
    state, emits = advance(state, 0.05, Facts(attackers_alive=0, defenders_alive=1))
    assert state.phase == LIVE, "the round ended on an elimination after a plant"
    assert not [e for e in emits if e.kind == "round_end"]


def test_wiping_a_side_before_a_plant_does_end_the_round():
    state = live_round()
    attackers = state.attackers
    state, emits = advance(state, 0.05, Facts(attackers_alive=0, defenders_alive=1))
    assert "eliminated" in [e.kind for e in emits]
    assert state.wins[1 - attackers] == 1


def test_post_rolls_into_the_next_round():
    state = live_round()
    state, _ = advance(state, ROUND_TIME + 0.1, BOTH)
    assert state.phase == POST
    state, emits = advance(state, POST_TIME + 0.1, BOTH)
    assert state.phase == FREEZE
    assert state.round == 2
    assert "round_start" in [e.kind for e in emits]


# ---------------------------------------------------------------------------
# Half time
# ---------------------------------------------------------------------------


def play_out(
    state: RoundState, winner_is_attacker: bool
) -> tuple[RoundState, list[str]]:
    """Run one whole round to a decision, returning the emits seen."""
    seen: list[str] = []
    while state.phase == FREEZE:
        state, emits = advance(state, FREEZE_TIME + 0.1, BOTH)
        seen += [e.kind for e in emits]
    facts = (
        Facts(attackers_alive=1, defenders_alive=0)
        if winner_is_attacker
        else Facts(attackers_alive=0, defenders_alive=1)
    )
    state, emits = advance(state, 0.05, facts)
    seen += [e.kind for e in emits]
    while state.phase == POST:
        state, emits = advance(state, POST_TIME + 0.1, BOTH)
        seen += [e.kind for e in emits]
    return state, seen


def test_the_sides_always_get_to_swap_before_anyone_can_win():
    """`HALF_AT = ROUNDS_TO_WIN - 1`, and the minus one is the point.

    Set equal, a side takes every round of the first half and wins the match
    without the other ever having attacked — which makes the map's balance the
    entire result.
    """
    assert HALF_AT < ROUNDS_TO_WIN

    state = RoundState()
    state, _ = advance(state, 0.05, BOTH)
    swapped_at = None
    for _ in range(ROUNDS_TO_WIN * 3):
        if state.phase == OVER:
            break
        state, seen = play_out(state, winner_is_attacker=True)
        if "half" in seen and swapped_at is None:
            swapped_at = state.round
    assert state.phase == OVER
    assert swapped_at is not None, "one side won without the sides ever swapping"


def test_the_swap_changes_who_attacks_and_nothing_else():
    """Roles flip; teams, spawns and the score they earned do not — see the
    module docs on why that is sound here and would not be on an asymmetric
    map."""
    state = RoundState()
    state, _ = advance(state, 0.05, BOTH)
    first_half_attackers = state.attackers
    wins_before = None
    for _ in range(ROUNDS_TO_WIN * 3):
        state, seen = play_out(state, winner_is_attacker=True)
        if "half" in seen:
            wins_before = state.wins
            break
    assert state.attackers == 1 - first_half_attackers
    assert state.swapped
    # The people keep what they earned: nothing is reversed.
    assert wins_before is not None
    assert wins_before[first_half_attackers] == HALF_AT


def test_the_match_ends_at_the_round_limit():
    state = RoundState()
    state, _ = advance(state, 0.05, BOTH)
    for _ in range(ROUNDS_TO_WIN * 3):
        if state.phase == OVER:
            break
        state, seen = play_out(state, winner_is_attacker=True)
    assert state.phase == OVER
    assert max(state.wins) == ROUNDS_TO_WIN
    # And it stays over, whatever else happens.
    frozen, emits = advance(state, 100.0, BOTH)
    assert frozen == state
    assert not emits


def test_advance_does_not_mutate_the_state_it_was_given():
    """Frozen for a reason: a conformance fixture has to be able to replay a case
    and compare the whole structure, which means trusting that the input is still
    the input."""
    before = live_round()
    snapshot = (before.phase, before.remaining, before.round, before.wins)
    advance(before, 10.0, BOTH)
    assert (before.phase, before.remaining, before.round, before.wins) == snapshot


# ---------------------------------------------------------------------------
# The wrapper, against a real room
# ---------------------------------------------------------------------------


def defuse_room() -> MatchRoom:
    cmap = assets.load_map("hd_atrium")
    world = physics.World.from_map(cmap)
    return MatchRoom(
        "t",
        "hd_atrium",
        world,
        cmap.spawns(),
        pickups.place(world, cmap.entities),
        mode=modes.build("defuse"),
        objectives=objectives.place(world, cmap),
    )


@pytest.fixture
def game():
    room = defuse_room()
    att = room.add("att", None, team=0)
    dfn = room.add("def", None, team=1)
    room.simulate(0.05)  # warmup -> round 1
    return room, att, dfn, room.mode


def hold_use(
    room: MatchRoom, player, where, seq_start: int, ticks: int, until=None
) -> int:
    """Hold the action key at a position, staying put.

    `until` stops as soon as it is true, which matters more than it looks:
    holding for a fixed 30 seconds after a defuse runs straight through `POST`
    and into the next round, so a test asserting on the phase would be asserting
    about round two.
    """
    seq = seq_start
    for _ in range(ticks):
        if until is not None and until():
            break
        room.enqueue(
            player,
            Command(
                seq=seq,
                forward=0.0,
                strafe=0.0,
                jump=False,
                yaw=0.0,
                pitch=0.0,
                dt=0.016,
                use=True,
            ),
        )
        seq += 1
        room.simulate(0.05)
        player.state.x, player.state.y, player.state.z = where
    return seq


def go_live(room: MatchRoom) -> None:
    for _ in range(int(FREEZE_TIME / 0.05) + 2):
        room.simulate(0.05)


def test_a_round_starts_frozen_with_the_bomb_on_an_attacker(game):
    room, att, _dfn, mode = game
    assert mode.state.phase == FREEZE
    assert mode.state.bomb.carrier == att.id
    assert mode.state.attackers == att.team


def test_nobody_takes_damage_during_freeze_time(game):
    """So the round cannot be decided before it starts."""
    room, att, dfn, mode = game
    dfn.protected_until = 0.0
    room._apply_damage(dfn, att, 50.0, False, weapons.weapon_at(0), time.monotonic())
    assert dfn.health == pytest.approx(100.0)


def test_friendly_fire_is_partial_rather_than_off(game):
    """The reason `damage_scale` returns a float: a teammate you spray through is
    a real cost, and one you cannot hurt at all makes a doorway a place to
    stand."""
    room, att, _dfn, mode = game
    go_live(room)
    mate = room.add("mate", None, team=att.team)
    # A mid-round joiner is dead by design (see `on_join`), so this test has to
    # put them on their feet before it can ask what a hit does.
    room.respawn(mate)
    mate.armour = 0.0
    mate.protected_until = 0.0
    room._apply_damage(mate, att, 40.0, False, weapons.weapon_at(0), time.monotonic())
    landed = 100.0 - mate.health
    assert 0 < landed < 40.0


def test_planting_needs_the_carrier_on_a_site(game):
    room, att, _dfn, mode = game
    go_live(room)
    assert mode._action_for(room, att) == "", "a plant was offered off-site"
    site = mode.sites[0]
    att.state.x, att.state.y, att.state.z = site.x, site.y, site.z
    assert mode._action_for(room, att) == "plant"


def test_holding_use_on_a_site_plants_the_bomb(game):
    room, att, _dfn, mode = game
    go_live(room)
    site = mode.sites[0]
    where = (site.x, site.y, site.z)
    att.state.x, att.state.y, att.state.z = where
    hold_use(room, att, where, 1, 400)
    assert mode.state.bomb.state == "planted"
    assert mode.state.bomb.site == site.id
    assert att.objectives == 1


def test_letting_go_resets_the_plant_rather_than_pausing_it(game):
    """Otherwise walking away and coming back finishes it instantly."""
    room, att, _dfn, mode = game
    go_live(room)
    site = mode.sites[0]
    where = (site.x, site.y, site.z)
    att.state.x, att.state.y, att.state.z = where
    seq = hold_use(room, att, where, 1, 20)
    assert att.action_progress > 0

    room.enqueue(
        att,
        Command(
            seq=seq,
            forward=0.0,
            strafe=0.0,
            jump=False,
            yaw=0.0,
            pitch=0.0,
            dt=0.016,
            use=False,
        ),
    )
    room.simulate(0.05)
    assert att.action_progress == 0.0
    assert att.action_kind == ""


def test_a_defender_defuses_a_planted_bomb_and_wins_the_round(game):
    room, att, dfn, mode = game
    go_live(room)
    site = mode.sites[0]
    where = (site.x, site.y, site.z)
    att.state.x, att.state.y, att.state.z = where
    seq = hold_use(room, att, where, 1, 400)
    assert mode.state.bomb.state == "planted"

    bomb = mode.state.bomb
    at_bomb = (bomb.x, bomb.y, bomb.z)
    dfn.state.x, dfn.state.y, dfn.state.z = at_bomb
    assert mode._action_for(room, dfn) == "defuse"
    hold_use(room, dfn, at_bomb, seq, 600, until=lambda: mode.state.phase != LIVE)
    assert mode.state.phase in (POST, OVER)
    assert room.scores[dfn.team] == 1
    assert dfn.objectives == 1


def test_an_attacker_cannot_defuse_and_a_defender_cannot_plant(game):
    room, att, dfn, mode = game
    go_live(room)
    site = mode.sites[0]
    dfn.state.x, dfn.state.y, dfn.state.z = site.x, site.y, site.z
    assert mode._action_for(room, dfn) == "", "a defender was offered a plant"

    att.state.x, att.state.y, att.state.z = site.x, site.y, site.z
    hold_use(room, att, (site.x, site.y, site.z), 1, 400)
    bomb = mode.state.bomb
    att.state.x, att.state.y, att.state.z = bomb.x, bomb.y, bomb.z
    assert mode._action_for(room, att) == "", "an attacker was offered a defuse"


def test_nobody_respawns_inside_a_live_round(game):
    room, att, dfn, mode = game
    go_live(room)
    dfn.alive = False
    dfn.respawn_at = 0.0
    room.simulate(0.05)
    assert not dfn.alive


def test_a_round_reset_clears_the_things_that_would_carry_over(game):
    """Zones burn people in the next round, grenades detonate on fresh spawns,
    and — the quiet one — stale position history rewinds a shot in the first
    quarter-second against where somebody was *last* round."""
    room, att, dfn, mode = game
    go_live(room)
    room.zones.append(object())
    room.nades.append(object())
    room.history.record(time.time() * 1000.0, {att.id: (1.0, 2.0, 3.0)}, {att.id: 4.0})
    assert len(room.history) > 0

    mode._reset_round(room)
    assert not room.zones
    assert not room.nades
    assert len(room.history) == 0
    assert att.alive and dfn.alive


def test_the_bomb_moves_to_another_attacker_when_its_carrier_dies(game):
    """A dropped bomb on maps this size is a hunt rather than a round."""
    room, att, dfn, mode = game
    go_live(room)
    mate = room.add("mate", None, team=att.team)
    mate.alive = True
    assert mode.state.bomb.carrier in (att.id, mate.id)

    holder = room.players[mode.state.bomb.carrier]
    holder.protected_until = 0.0
    room._apply_damage(
        holder, dfn, 500.0, False, weapons.weapon_at(0), time.monotonic()
    )
    assert mode.state.bomb.carrier != holder.id
    assert mode.state.bomb.state == "carried"


def test_a_kill_does_not_move_the_round_score(game):
    """Rounds are the score; inheriting the base `on_kill` would make the number
    mean two things at once under a scoreboard labelled "Rounds"."""
    room, att, dfn, mode = game
    go_live(room)
    dfn.protected_until = 0.0
    before = list(room.scores)
    room._apply_damage(dfn, att, 500.0, False, weapons.weapon_at(0), time.monotonic())
    assert room.scores == before
    assert att.kills == 1


def test_a_mid_round_joiner_waits_for_the_next_one(game):
    """Dropping them in alive hands one side a body the other did not have to
    shoot, which decides the round on when somebody's browser finished
    loading."""
    room, _att, _dfn, mode = game
    go_live(room)
    late = room.add("late", None, team=1)
    assert not late.alive


# ---------------------------------------------------------------------------
# Wire
# ---------------------------------------------------------------------------


def test_the_timings_are_served_rather_than_left_for_a_client_to_guess(game):
    room, _att, _dfn, _mode = game
    config = room.state_payload()["mode"]["config"]
    assert config["plantTime"] == pytest.approx(PLANT_TIME)
    assert config["defuseTime"] == pytest.approx(DEFUSE_TIME)
    assert config["fuseTime"] == pytest.approx(FUSE_TIME)
    assert config["roundsToWin"] == ROUNDS_TO_WIN
    assert len(room.state_payload()["mode"]["sites"]) == len(_mode.sites)


def test_the_phase_and_the_fuse_are_public_and_progress_is_not(game):
    """A progress bar is about one player's own hands; a fuse is a thing
    everybody in the room can hear."""
    room, att, dfn, mode = game
    go_live(room)
    site = mode.sites[0]
    where = (site.x, site.y, site.z)
    att.state.x, att.state.y, att.state.z = where
    hold_use(room, att, where, 1, 400)

    shared = room.shared_view()["mode"]
    assert shared["phase"] == LIVE
    assert shared["bomb"]["state"] == "planted"
    assert shared["bomb"]["fuseIn"] > 0
    assert "progress" not in shared

    mine = room.private_view_for(att)["mode"]
    assert mine["attacking"] is True
    assert "progress" in mine
    assert room.private_view_for(dfn)["mode"]["attacking"] is False


# ---------------------------------------------------------------------------
# What the match gets recorded as
# ---------------------------------------------------------------------------


def test_a_result_carries_the_mode_and_the_objectives_it_was_scored_by(game):
    """The three fields a fight-shaped result could not hold.

    `mode` because a 5-3 in rounds and a 5-3 in kills are the same two numbers,
    `objectives` because in defuse they can be the whole of what a player did,
    and `roundsWon` because it is what an economy-shaped match is actually
    counting.
    """
    room, att, _dfn, _mode = game
    att.objectives = 2
    room.scores[att.team] = 3
    result = room.result_for(att.id)
    assert result is not None
    assert result["mode"] == "defuse"
    assert result["modeName"]
    assert result["objectives"] == 2
    assert result["roundsWon"] == 3


def test_a_player_who_only_planted_still_played_a_match(game):
    """The exact case B-6 exists for.

    Every fight-shaped counter is zero — no kills, no deaths, nothing landed —
    and this is unmistakably a match: they planted the bomb twice. The predicate
    used to say otherwise, so they got no card and no XP.
    """
    room, att, _dfn, _mode = game
    att.kills = att.deaths = 0
    att.damage_dealt = 0.0
    att.objectives = 2
    result = room.result_for(att.id)
    assert result is not None
    assert result["recordable"] is True


def test_rounds_won_follows_the_player_across_the_half_time_swap(game):
    """Read off `room.scores`, which is indexed by team and never reversed here.

    The swap flips which side *attacks*; nobody's `team` changes, so a player
    keeps the rounds they won. Reading it from `RoundState` instead would need a
    second update at half time, and that is the one that gets forgotten.
    """
    room, att, _dfn, mode = game
    room.scores[att.team] = 2
    before = mode.rounds_won(room, att)
    mode.state = replace(mode.state, swapped=True)
    assert mode.rounds_won(room, att) == before == 2


def test_deathmatch_reports_no_rounds_even_though_it_has_scores():
    """`scores` counts kills there, so reading it directly would pay round XP
    once per frag. The hook is what makes the two different questions."""
    cmap = assets.load_map("hd_atrium")
    world = physics.World.from_map(cmap)
    room = MatchRoom("dm", "hd_atrium", world, cmap.spawns())
    player = room.add("p", None)
    room.scores[player.team] = 12
    assert room.mode.rounds_won(room, player) == 0
    assert room.result_for(player.id)["roundsWon"] == 0

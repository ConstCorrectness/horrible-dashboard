"""The game-mode hooks, and the invariants that keep them from drifting.

`MatchRoom` used to encode "the only mode is deathmatch" in five places. These
tests are about the hooks that replaced them — that each one is actually called
from its documented site, and that the wire keeps the shape the snapshot's
fragmentation optimisation depends on.

Hermetic, like `test_hassault_match.py`: every room is built on a synthetic world
because AssaultCube content is copyright and cannot live in this repo.
"""

from __future__ import annotations

import json

import pytest

from backend.modules.hassault import grenades, modes
from backend.modules.hassault.match import Command, MatchRoom, MatchServer, weapons
from backend.modules.hassault.modes import Deathmatch, GameMode
from backend.modules.hassault.physics import flat_world


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


def make_room(mode: GameMode | None = None, room_id: str = "r1") -> MatchRoom:
    world = flat_world(32, floor=0, ceil=16)
    spawns = [Spawn(8, 8, team=0), Spawn(20, 20, team=1)]
    return MatchRoom(room_id, "testmap", world, spawns, mode=mode)


class Recorder(GameMode):
    """A mode that only writes down which hooks fired.

    The point of the exercise: a hook nobody calls is a hook that looks
    implemented and does nothing, and every one of these was a line of `match.py`
    before it was a method.
    """

    id = "recorder"
    name = "Recorder"

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.ff = 0.0

    def attach(self, room):
        self.calls.append("attach")

    def tick(self, room, elapsed, now):
        self.calls.append("tick")

    def on_join(self, room, player):
        self.calls.append("join")

    def on_leave(self, room, player):
        self.calls.append("leave")

    def outfit(self, room, player):
        self.calls.append("outfit")

    def on_kill(self, room, victim, attacker, head, weapon):
        self.calls.append("kill")

    def on_command(self, room, player, command, now):
        self.calls.append("command")

    def damage_scale(self, room, attacker, victim):
        return self.ff

    def may_respawn(self, room, player, now):
        self.calls.append("may_respawn")
        return True


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------


def test_an_unknown_mode_is_refused_rather_than_swapped_for_deathmatch():
    """A silent fallback would open a room that looks right and plays as
    something else — and because the client renders whatever the welcome
    describes, nothing anywhere would report the substitution."""
    with pytest.raises(ValueError, match="unknown game mode"):
        modes.build("bomb-defuse-deluxe")


def test_the_default_is_deathmatch_so_an_unmigrated_caller_changes_nothing():
    assert modes.build().id == "dm"
    assert modes.build(None).id == "dm"


def test_the_catalog_covers_every_buildable_mode():
    """The catalog feeds the REST enum, the agent tool's schema and the menus. A
    mode missing from it is a mode nobody can ask for."""
    catalog = modes.catalog()
    assert {m["id"] for m in catalog} == {"dm", "tdm", "ctf", "defuse"}
    for entry in catalog:
        assert modes.build(entry["id"]).name == entry["name"]
        assert entry["scoreLabel"]


# ---------------------------------------------------------------------------
# Every hook fires from its documented call site
# ---------------------------------------------------------------------------


def test_attach_join_and_leave_fire():
    mode = Recorder()
    room = make_room(mode)
    assert mode.calls == ["attach"]
    player = room.add("a", None)
    assert mode.calls == ["attach", "outfit", "join"]
    room.remove(player.id)
    assert mode.calls[-1] == "leave"


def test_outfit_runs_after_reset_loadout_not_instead_of_it():
    """`reset_loadout` hands out every weapon with full magazines, which is what
    deathmatch wants and what an economy has to undo. Ordering is the whole
    contract: a mode that strips a loadout must run *after* the grant, or the
    grant silently wins."""
    seen: list[int] = []

    class Stripper(GameMode):
        def outfit(self, room, player):
            # Whatever `reset_loadout` did is visible here.
            seen.append(sum(player.reserve))
            player.reserve = [0] * len(player.reserve)

    room = make_room(Stripper())
    player = room.add("a", None)
    assert seen and seen[0] > 0, "outfit ran before the loadout was granted"
    assert sum(player.reserve) == 0, "outfit's change was overwritten"


def test_tick_runs_once_per_simulate():
    mode = Recorder()
    room = make_room(mode)
    room.add("a", None)
    room.simulate(0.05)
    room.simulate(0.05)
    assert mode.calls.count("tick") == 2


def test_on_command_sees_every_consumed_command():
    mode = Recorder()
    room = make_room(mode)
    player = room.add("a", None)
    # From 1, not 0: `enqueue` dedupes against the last seq it saw, which starts
    # at 0, so a command numbered 0 is acked as already-seen and never consumed.
    for seq in range(1, 4):
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
            ),
        )
    room.simulate(0.05)
    room.simulate(0.05)
    # Against the ack rather than against 3, because how many commands a tick
    # consumes depends on the player's replenishing time budget — a command whose
    # `dt` does not fit stays queued, which is the throttle working. `ack` is the
    # seq of the last one actually consumed, so this pins the real invariant:
    # exactly one `on_command` per consumed command, no more and none skipped.
    # Against the ack rather than against 3, because how many commands a tick
    # consumes depends on the player's replenishing time budget — one whose `dt`
    # does not fit stays queued, which is the throttle working. `ack` is the seq
    # of the last one consumed, so with seqs from 1 it *is* the count, and this
    # pins the real invariant: exactly one `on_command` per consumed command.
    assert player.ack > 0, "nothing was consumed at all"
    assert mode.calls.count("command") == player.ack


def test_a_mode_can_hold_the_dead_out_of_a_round():
    """The one predicate a round-based mode needs. Expressed here rather than by
    pushing `respawn_at` into the future, which would work and would leave "when
    does this player come back" a number nothing owns."""

    class NoRespawn(GameMode):
        def may_respawn(self, room, player, now):
            return False

    room = make_room(NoRespawn())
    player = room.add("a", None)
    player.alive = False
    player.respawn_at = 0.0
    room.simulate(0.05)
    assert not player.alive

    room2 = make_room()
    p2 = room2.add("a", None)
    p2.alive = False
    p2.respawn_at = 0.0
    room2.simulate(0.05)
    assert p2.alive, "the default mode stopped respawning"


# ---------------------------------------------------------------------------
# damage_scale, at all three damage sites
# ---------------------------------------------------------------------------
#
# Honouring it at two of the three is the regression this refactor most invites,
# so there is a case per site rather than one case for "friendly fire".


def _two_players(mode: GameMode, same_team: bool = True):
    room = make_room(mode)
    a = room.add("a", None, team=0)
    b = room.add("b", None, team=0 if same_team else 1)
    # Face to face and close, so a shot cannot miss for a reason unrelated to
    # the rule under test.
    b.state.x, b.state.y, b.state.z = a.state.x + 3.0, a.state.y, a.state.z
    a.state.yaw = 0.0
    return room, a, b


def _candidates(room, shooter):
    """Who `_fire` would consider a legal target, by the rule it actually uses.

    Mirrors the filter rather than driving `_fire`, so this stays a test about
    the mode hook and not about fire rate, magazines or line of sight — each of
    which can make a shot miss for a reason unrelated to the rule under test.
    """
    return [
        p.id
        for p in room.players.values()
        if p.id != shooter.id
        and p.alive
        and not p.protected
        and room.mode.damage_scale(room, shooter, p) > 0.0
    ]


def test_damage_scale_gates_bullets():
    mode = Recorder()
    room, a, b = _two_players(mode)
    b.protected_until = 0.0
    mode.ff = 0.0
    assert b.id not in _candidates(room, a), "a teammate was a candidate at zero scale"
    mode.ff = 1.0
    assert b.id in _candidates(room, a), "a target was refused at full scale"

    # And the amount, which is the half that is easy to leave out: a mode
    # returning 0.0 looks correct without it, because nobody is a candidate.
    mode.ff = 0.0
    before = b.health
    room._apply_damage(b, a, 50.0, False, weapons.weapon_at(0), 100.0)
    assert b.health == pytest.approx(before), "damage landed through a zero scale"


def test_damage_scale_gates_the_he_blast():
    mode = Recorder()
    room, a, b = _two_players(mode)
    b.protected_until = 0.0
    nade = grenades.Grenade(
        id="n1",
        spec=next(g for g in grenades.GRENADES if g.kind == "he"),
        owner=a.id,
        team=a.team,
        x=b.state.x,
        y=b.state.y,
        z=b.state.z,
        vx=0.0,
        vy=0.0,
        vz=0.0,
        fuse=0.0,
    )
    mode.ff = 0.0
    room._detonate(nade, 100.0)
    assert b.health == pytest.approx(100.0), "the blast ignored a zero scale"

    mode.ff = 1.0
    room._detonate(nade, 200.0)
    assert b.health < 100.0, "the blast was refused at full scale"


def test_damage_scale_gates_a_fire_zone():
    mode = Recorder()
    room, a, b = _two_players(mode)
    b.protected_until = 0.0
    zone = grenades.Zone(
        id="z1",
        kind="fire",
        owner=a.id,
        team=a.team,
        x=b.state.x,
        y=b.state.y,
        z=b.state.z,
        radius=6.0,
        remaining=10.0,
        duration=10.0,
        damage_per_second=40.0,
    )
    mode.ff = 0.0
    room._burn(zone, 1.0, 100.0)
    assert b.health == pytest.approx(100.0), "the fire ignored a zero scale"

    mode.ff = 1.0
    room._burn(zone, 1.0, 200.0)
    assert b.health < 100.0, "the fire was refused at full scale"


def test_a_partial_scale_is_partial_damage_not_a_toggle():
    """The reason the hook returns a float. A mode with CS-style friendly fire
    wants a fraction, and a bool would need a second hook beside it."""

    class Half(GameMode):
        def damage_scale(self, room, attacker, victim):
            return 0.5

    room, a, b = _two_players(Half())
    b.protected_until = 0.0
    before = b.health
    b.armour = 0.0  # so the only thing scaling the hit is the mode
    room._apply_damage(b, a, 40.0, False, weapons.weapon_at(0), 100.0)
    assert before - b.health == pytest.approx(20.0)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def test_a_kill_scores_through_the_mode_and_not_apply_damage():
    """`_apply_damage` used to do `scores[team] += 1` inline. That line was one of
    the five places this class knew it was playing deathmatch."""

    class NeverScores(GameMode):
        def on_kill(self, room, victim, attacker, head, weapon):
            pass

    room, a, b = _two_players(NeverScores(), same_team=False)
    b.protected_until = 0.0
    room._apply_damage(b, a, 500.0, False, weapons.weapon_at(0), 100.0)
    assert not b.alive
    assert a.kills == 1, "kills is a stat and is always credited"
    assert room.scores == [0, 0], "the mode was bypassed"


def test_the_default_mode_still_scores_a_kill_to_the_killers_team():
    room, a, b = _two_players(Deathmatch(), same_team=False)
    b.protected_until = 0.0
    room._apply_damage(b, a, 500.0, False, weapons.weapon_at(0), 100.0)
    assert room.scores[a.team] == 1


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


def test_deathmatch_keeps_its_room_relative_result():
    room = make_room(Deathmatch())
    a = room.add("a", None)
    b = room.add("b", None)
    a.kills, b.kills = 5, 2
    a.deaths = 2
    won, mvp = room.mode.outcome_for(room, a)
    assert (won, mvp) == (True, True)
    assert room.mode.outcome_for(room, b) == (False, False)


def test_team_deathmatch_reads_the_team_score_not_your_own_kills():
    """A player who went 2-14 on the winning side did win, and telling them
    otherwise would describe a different game than the scoreboard showed."""
    room = make_room(Deathmatch(teams=True))
    a = room.add("a", None, team=0)
    b = room.add("b", None, team=1)
    room.scores = [10, 3]
    a.kills, b.kills = 2, 14
    assert room.mode.outcome_for(room, a)[0] is True
    assert room.mode.outcome_for(room, b)[0] is False


def test_result_for_takes_won_and_mvp_from_the_mode():
    class AlwaysWins(GameMode):
        def outcome_for(self, room, player):
            return (True, True)

    room = make_room(AlwaysWins())
    a = room.add("a", None)
    room.add("b", None)
    a.kills = 1  # so the match is recordable at all
    result = room.result_for(a.id)
    assert result is not None
    assert result["won"] is True
    assert result["mvp"] is True


def test_an_empty_session_is_still_not_a_victory():
    """`is_recordable` gates both fields, and it must keep doing so whatever the
    mode says: alone in a room, opening the pane used to read as a VICTORY."""

    class AlwaysWins(GameMode):
        def outcome_for(self, room, player):
            return (True, True)

    room = make_room(AlwaysWins())
    a = room.add("a", None)
    result = room.result_for(a.id)
    assert result is not None
    assert result["recordable"] is False
    assert result["won"] is False
    assert result["mvp"] is False


# ---------------------------------------------------------------------------
# The wire
# ---------------------------------------------------------------------------


def test_the_snapshot_gains_no_new_top_level_key():
    """Mode state rides in `shared`/`you`/`mode`, never as a new key beside them.

    The snapshot template splits one serialised tick on two sentinels; a new
    top-level key is not caught by that, it just changes a shape three clients
    parse.
    """
    room = make_room()
    player = room.add("a", None)
    message = room.snapshot_message(
        0.0,
        [player.snapshot(0.0)],
        room.shared_view(),
        0,
        room.private_view_for(player),
    )
    assert set(message["data"]) == {
        "room",
        "tick",
        "t",
        "ack",
        "players",
        "you",
        "scores",
        "nades",
        "zones",
        "fx",
        "itemsOut",
        "mode",
    }


def test_per_recipient_mode_state_rides_inside_you():
    """The dangerous mistake this guards: per-recipient data placed in
    `shared_state` is sent to everybody, and nothing raises, warns, or breaks the
    template — money would simply be world-readable."""

    class Wallet(GameMode):
        def private_state(self, room, player):
            return {"money": 800 if player.name == "a" else 100}

    room = make_room(Wallet())
    a = room.add("a", None)
    b = room.add("b", None)
    assert room.private_view_for(a)["mode"] == {"money": 800}
    assert room.private_view_for(b)["mode"] == {"money": 100}
    assert room.shared_view()["mode"] is None


def test_the_template_still_splits_with_a_populated_mode_blob():
    """The fragmentation optimisation degrades *silently* — a sentinel collision
    is caught and falls back to `send_json`, so the only symptom is the bandwidth
    saving quietly switching itself off."""

    class Busy(GameMode):
        def shared_state(self, room):
            return {
                "phase": "live",
                "phaseIn": 42.125,
                "round": 7,
                "bomb": {"state": "planted", "x": 12.0, "y": 8.5, "fuseIn": 18.25},
            }

        def private_state(self, room, player):
            return {"money": 4300, "owned": [1, 2], "progress": 0.42}

    room = make_room(Busy())
    player = room.add("a", None)
    head, mid, tail = room.snapshot_template(
        0.0, [player.snapshot(0.0)], room.shared_view()
    )
    rebuilt = json.loads(
        head + "7" + mid + json.dumps(room.private_view_for(player)) + tail
    )
    assert rebuilt["data"]["ack"] == 7
    assert rebuilt["data"]["mode"]["bomb"]["state"] == "planted"
    assert rebuilt["data"]["you"]["mode"]["money"] == 4300


def test_the_welcome_carries_the_mode_so_the_fabric_needs_no_changes():
    """`fabric.handle_join` sends `state_payload()` verbatim as a guest's
    welcome, which is the whole reason mode state goes in it."""
    room = make_room(Deathmatch(teams=True))
    payload = room.state_payload()
    assert payload["mode"]["id"] == "tdm"
    assert payload["mode"]["scoreLabel"] == "Kills"
    assert payload["mode"]["v"] >= 1


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_a_listing_row_names_its_mode():
    """And every key it produces must survive `MatchSummary`, which drops what it
    does not declare — see the test below."""
    server = MatchServer()
    room = make_room(Deathmatch(teams=True), room_id="x")
    server.rooms["x"] = room
    row = server.listing()[0]
    assert row["mode"] == "tdm"
    assert row["modeName"] == "Team Deathmatch"


def test_every_listing_key_survives_the_response_model():
    """A Pydantic response model drops undeclared fields silently. Left off, the
    server browser shows every match as deathmatch, lets somebody join a defuse
    round, and renders a deathmatch HUD over it, with no error anywhere."""
    from backend.modules.hassault.models import MatchSummary

    server = MatchServer()
    server.rooms["x"] = make_room(Deathmatch(teams=True), room_id="x")
    row = server.listing()[0]
    dumped = MatchSummary(**row).model_dump()
    missing = set(row) - set(dumped)
    assert not missing, f"the response model drops {sorted(missing)}"


def test_find_or_create_does_not_hand_you_a_room_in_another_mode():
    """Keyed on the map alone, "join hd_pit" drops a player into a stranger's
    defuse round holding a HUD for a different game — and it looks like a
    successful join from every angle."""
    server = MatchServer()
    dm = make_room(Deathmatch(), room_id="a")
    tdm = make_room(Deathmatch(teams=True), room_id="b")
    server.rooms["a"] = dm
    server.rooms["b"] = tdm
    # Both are on the same map. The mode is what tells them apart.
    assert server.find_or_create("testmap", "dm") is dm
    assert server.find_or_create("testmap", "tdm") is tdm
    assert server.find_or_create("testmap") is dm, "the default stopped meaning dm"

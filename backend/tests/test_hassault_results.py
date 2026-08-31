"""Match results: the numbers on the debrief card, and where they come from.

Everything tested here used to be `random.randint`. The card showed kills,
deaths, headshots, damage, XP, a rating and a tier, none of which had any
relationship to the match that had just been played — and all of it lived in a
process-global dict that a backend restart emptied.

So these tests are about provenance more than arithmetic: that a stat is the
simulation's own counter, that a headshot percentage cannot exceed 100, that a
match survives a restart because it is a row, and that progression is *derived*
from those rows rather than stored beside them where it could disagree.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.modules.hassault import results, weapons
from backend.modules.hassault.match import Command, MatchRoom
from backend.modules.hassault.physics import flat_world


class Spawn:
    def __init__(self, x: float, y: float, z: float = 0.0, team: int = 0) -> None:
        self.x = x
        self.y = y
        self.z = z
        self.yaw = 0.0
        self.attr2 = team


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    """Every test gets its own `app.db`, so a run cannot inherit another's rows."""
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    yield


def make_room() -> MatchRoom:
    world = flat_world(64, floor=0, ceil=16)
    return MatchRoom(
        "r1", "hd_pit", world, [Spawn(8, 8, team=0), Spawn(24, 24, team=1)]
    )


def place(player, x: float, y: float, yaw: float = 0.0) -> None:
    player.state.x = x
    player.state.y = y
    player.state.z = 0.0
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
# The stats are the simulation's
# ---------------------------------------------------------------------------


def test_damage_and_head_kills_come_from_shots_that_were_actually_resolved():
    """The whole point: a shot fired at a body, adjudicated by the server, moves
    the number the card prints."""
    room = make_room()
    shooter = room.add("Shooter", None)
    victim = room.add("Victim", None)
    # Facing along +x, twelve cubes apart, with a rifle.
    place(shooter, 8.0, 8.0)
    place(victim, 20.0, 8.0)
    # Spawn protection is real and would eat the shot: a freshly added player is
    # briefly invulnerable, which is not what this test is about.
    victim.protected_until = 0.0
    # A slot is the index into the served list, which is what the wire carries.
    shooter.weapon = weapons.WEAPONS.index(weapons.WEAPON_BY_ID["assault"])
    shooter.ammo[shooter.weapon] = 30

    room.enqueue(shooter, move(1, fire=True))
    # The budget is the replenishing time reservoir a client spends from; a test
    # driving one command by hand has to hand it the time.
    shooter.budget = 1.0
    room.simulate(1 / 60)

    assert shooter.damage_dealt > 0, "a landed shot moved nothing"
    assert victim.health < 100
    # And the damage recorded is what landed, not what was rolled.
    assert shooter.damage_dealt == pytest.approx(100.0 - victim.health, abs=0.51)


def test_overkill_is_not_counted_as_damage():
    """A 90-damage round into a body with 20 left is 20 points of damage. Counting
    the roll instead would make the stat a description of the weapon rather than
    of the match — and it is the one number a player can check against the health
    bars they watched go down."""
    room = make_room()
    shooter = room.add("Shooter", None)
    victim = room.add("Victim", None)
    place(shooter, 8.0, 8.0)
    place(victim, 20.0, 8.0)
    victim.protected_until = 0.0
    victim.health = 15.0
    # A slot is the index into the served list, which is what the wire carries.
    shooter.weapon = weapons.WEAPONS.index(weapons.WEAPON_BY_ID["assault"])
    shooter.ammo[shooter.weapon] = 30

    room.enqueue(shooter, move(1, fire=True))
    # The budget is the replenishing time reservoir a client spends from; a test
    # driving one command by hand has to hand it the time.
    shooter.budget = 1.0
    room.simulate(1 / 60)

    assert victim.alive is False
    assert shooter.damage_dealt <= 15.0


def test_the_result_is_read_before_the_player_is_removed():
    """`remove` drops the `MatchPlayer` and every counter with it, so the order is
    the whole implementation. Reading after would file a match of zeroes."""
    room = make_room()
    me = room.add("Shooter", None)
    me.kills = 3
    me.deaths = 1
    me.head_kills = 2
    me.damage_dealt = 640.0

    result = room.result_for(me.id)
    assert result is not None
    assert (result["kills"], result["deaths"]) == (3, 1)
    assert result["headKills"] == 2
    assert result["damageDealt"] == 640
    assert result["map"] == "hd_pit"

    room.remove(me.id)
    assert room.result_for(me.id) is None, "a departed player still has a result"


def test_winning_is_relative_to_the_room_and_counts_bots():
    """Deathmatch: you won if nobody outscored you, MVP if nobody equalled you.
    Bots are in the comparison — losing to one is losing, and a card that quietly
    excluded them would be flattering rather than true."""
    room = make_room()
    me = room.add("Me", None)
    bot = room.add("Bot", None)
    me.kills, bot.kills = 5, 9

    result = room.result_for(me.id)
    assert result["won"] is False and result["mvp"] is False

    me.kills = 9
    tie = room.result_for(me.id)
    assert tie["won"] is True, "a tie is not a loss"
    assert tie["mvp"] is False, "a tie is not an MVP either"

    me.kills = 12
    ahead = room.result_for(me.id)
    assert ahead["won"] and ahead["mvp"]


def test_a_player_alone_in_a_room_has_won_nothing_to_be_proud_of():
    room = make_room()
    me = room.add("Me", None)
    result = room.result_for(me.id)
    # No opponents: `won` is vacuously true and the card says how many there were,
    # rather than pretending a solo warm-up was a victory over somebody.
    assert result["opponents"] == 0


# ---------------------------------------------------------------------------
# The row, and what is derived from it
# ---------------------------------------------------------------------------


def sample(**kw) -> dict:
    base = {
        "map": "hd_pit",
        "room": "r1",
        "name": "@rob",
        "kills": 10,
        "deaths": 4,
        "headKills": 3,
        "damageDealt": 1200,
        "won": True,
        "mvp": True,
        "opponents": 3,
        "playedAt": 1000.0,
    }
    base.update(kw)
    return base


def test_a_match_is_a_row_and_survives_the_process():
    """The old summary lived in a module-level dict. This one is in `app.db`, so a
    restart — or a second process reading the same database — sees it."""
    results.record("acct", sample())
    summary = results.latest("acct")
    assert summary is not None
    assert summary["kills"] == 10
    assert summary["mapName"] == "hd_pit"
    assert summary["damageDealt"] == 1200
    assert summary["isMvp"] is True


def test_headshot_percent_is_kills_over_kills_and_never_exceeds_a_hundred():
    """`head_kills` counts *kills*, not hits, precisely so this division means
    something. Counting hits there prints 300%."""
    results.record("acct", sample(kills=4, headKills=4))
    assert results.latest("acct")["headshotPercent"] == 100.0


def test_a_match_with_no_kills_does_not_divide_by_zero():
    results.record("acct", sample(kills=0, headKills=0, won=False, mvp=False))
    summary = results.latest("acct")
    assert summary["headshotPercent"] == 0.0
    assert summary["kills"] == 0


def test_xp_is_a_function_of_what_happened():
    """Every term traceable to the match. A losing player still earns the base —
    a progression that only moves for people already winning is one nobody new
    ever moves."""
    played = results.xp_for(sample(kills=0, headKills=0, damageDealt=0, won=False))
    assert played == results.XP_BASE
    won = results.xp_for(sample(kills=0, headKills=0, damageDealt=0, won=True))
    assert won == results.XP_BASE + results.XP_WIN
    # And the kills, heads and damage each move it.
    assert results.xp_for(sample()) > won


def test_progression_is_summed_from_the_matches_not_stored_beside_them():
    """A running total in its own column is a number that can disagree with the
    rows it claims to summarise, with no way to tell which is wrong."""
    for _ in range(3):
        results.record("acct", sample())
    one = results.xp_for(sample())
    progress = results.progression("acct")
    assert progress["totalXp"] == one * 3
    assert progress["level"] == one * 3 // results.XP_PER_LEVEL + 1
    # A player who has never played is level 1, not level 0.
    assert results.progression("nobody")["level"] == 1


def test_dismissing_hides_the_card_and_keeps_the_match():
    """A column, not a delete: the row *is* the history, and closing a card is not
    a claim that the match did not happen."""
    results.record("acct", sample())
    assert results.latest("acct") is not None
    results.dismiss("acct")
    assert results.latest("acct") is None
    assert len(results.history("acct")) == 1


def test_one_accounts_matches_are_not_anothers():
    results.record("me", sample(kills=10))
    results.record("them", sample(kills=99))
    assert results.latest("me")["kills"] == 10
    assert len(results.history("me")) == 1


def test_the_latest_is_the_latest():
    results.record("acct", sample(kills=1, playedAt=1000.0))
    results.record("acct", sample(kills=2, playedAt=2000.0))
    assert results.latest("acct")["kills"] == 2


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def test_a_match_says_who_adjudicated_it():
    """Storage is not the trust boundary; simulation is. A row this node computed
    for itself and a row a referee reported are different claims, and the column
    is what keeps them tellable apart."""
    results.record("acct", sample())
    local = results.latest("acct")
    assert local["authority"] == "local"
    assert local["rated"] is False


def test_only_a_server_result_is_rated():
    results.record("acct", sample(), authority="server")
    served = results.latest("acct")
    assert served["authority"] == "server"
    assert served["rated"] is True


def test_an_unknown_authority_is_not_taken_at_its_word():
    """The value decides whether a match may ever count for something, so anything
    that is not exactly `server` is `local` — the claim that grants nothing."""
    results.record("acct", sample(), authority="SERVER-ish")
    assert results.latest("acct")["authority"] == "local"


def test_a_table_from_before_the_column_gains_it(tmp_path):
    """`CREATE TABLE IF NOT EXISTS` never adds a column to an existing table, so an
    install that recorded a match before provenance existed would fail every read
    afterwards — on exactly the machines that have been playing longest."""
    import sqlite3

    from backend.modules.database.app_db import ensure_app_db_dir

    # The old schema, as it shipped: everything except `authority`.
    with sqlite3.connect(str(ensure_app_db_dir())) as conn:
        conn.execute(
            """
            CREATE TABLE hassault_matches (
                id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
                player_name TEXT NOT NULL DEFAULT '', map_name TEXT NOT NULL,
                room TEXT NOT NULL DEFAULT '', kills INTEGER NOT NULL DEFAULT 0,
                deaths INTEGER NOT NULL DEFAULT 0, head_kills INTEGER NOT NULL DEFAULT 0,
                damage_dealt INTEGER NOT NULL DEFAULT 0, opponents INTEGER NOT NULL DEFAULT 0,
                won INTEGER NOT NULL DEFAULT 0, mvp INTEGER NOT NULL DEFAULT 0,
                xp INTEGER NOT NULL DEFAULT 0, drop_id TEXT,
                played_at REAL NOT NULL, dismissed_at REAL
            )
            """
        )
        conn.execute(
            "INSERT INTO hassault_matches (id, account_id, map_name, kills, played_at) "
            "VALUES ('old', 'acct', 'hd_pit', 7, 900.0)"
        )

    # The migration runs on the next init, and backfills the honest answer:
    # that match *was* adjudicated by the node that wrote it.
    results.init_results_db()
    row = results.latest("acct")
    assert row["kills"] == 7
    assert row["authority"] == "local"


def test_a_drop_is_attached_to_the_match_that_earned_it():
    """The drop and the row are written by two different things at two different
    times — `record` when the player leaves, the drop once the client process has
    actually exited — so attaching it is an update, and it has to exist."""
    match_id = results.record("acct", sample())
    results.attach_drop(match_id, "inst-42")
    assert results.latest("acct")["dropId"] == "inst-42"


def test_only_the_drops_id_is_stored():
    """Not its name, rarity or wear. Copying those onto the match row would mean a
    renamed skin showing its old name on every card that ever mentioned it."""
    match_id = results.record("acct", sample())
    results.attach_drop(match_id, "inst-42")
    summary = results.latest("acct")
    # Resolved by the route against the inventory, not by the row.
    assert summary["earnedDrop"] is None
    assert summary["dropId"] == "inst-42"


def test_a_drop_for_no_match_is_reported_not_swallowed(caplog):
    """The shape of the bug this function was missing for: the skin was rolled
    into the inventory, attaching it raised `AttributeError`, the watchdog logged
    it as "could not roll a drop", and the card showed nothing — leaving an item
    in the armoury that came from nowhere."""
    results.init_results_db()
    with caplog.at_level("WARNING"):
        results.attach_drop("no-such-match", "inst-42")
    assert "inst-42" in caplog.text


def test_a_table_from_before_drop_id_gains_it(tmp_path):
    """`drop_id` gets the same treatment `authority` does: an install predating a
    column keeps a table without it, and every read then fails."""
    import sqlite3

    from backend.modules.database.app_db import ensure_app_db_dir

    with sqlite3.connect(str(ensure_app_db_dir())) as conn:
        conn.execute(
            """
            CREATE TABLE hassault_matches (
                id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
                player_name TEXT NOT NULL DEFAULT '', map_name TEXT NOT NULL,
                room TEXT NOT NULL DEFAULT '', kills INTEGER NOT NULL DEFAULT 0,
                deaths INTEGER NOT NULL DEFAULT 0, head_kills INTEGER NOT NULL DEFAULT 0,
                damage_dealt INTEGER NOT NULL DEFAULT 0, opponents INTEGER NOT NULL DEFAULT 0,
                won INTEGER NOT NULL DEFAULT 0, mvp INTEGER NOT NULL DEFAULT 0,
                xp INTEGER NOT NULL DEFAULT 0,
                authority TEXT NOT NULL DEFAULT 'local',
                played_at REAL NOT NULL, dismissed_at REAL
            )
            """
        )
        conn.execute(
            "INSERT INTO hassault_matches (id, account_id, map_name, kills, played_at) "
            "VALUES ('old', 'acct', 'hd_pit', 7, 900.0)"
        )

    results.attach_drop("old", "inst-42")
    row = results.latest("acct")
    assert row["kills"] == 7
    assert row["dropId"] == "inst-42"


def test_the_card_shows_the_skin_the_drop_resolves_to():
    """End to end, because the two halves are in different modules: the row holds
    an id, and `GET /match/latest_summary` turns it into the name, rarity colour
    and wear the card draws."""
    from fastapi.testclient import TestClient

    from backend.app import app
    from backend.modules.hassault.skins import skin_manager

    account = "local_player"
    match_id = results.record(account, sample())
    drop = skin_manager.roll_drop(account)
    results.attach_drop(match_id, drop.instance_id)

    res = TestClient(app).get("/api/hassault/match/latest_summary")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["matchId"] == match_id
    assert body["earnedDrop"] is not None
    assert body["earnedDrop"]["instanceId"] == drop.instance_id
    assert body["earnedDrop"]["definition"]["name"]


# ---------------------------------------------------------------------------
# Was this a match at all?
# ---------------------------------------------------------------------------


def _result_vectors() -> dict:
    """The shared fixture, or a loud failure.

    Read from the repo rather than duplicated here for the same reason
    `physics-vectors.json` is: `apps/native-fps/tests/conformance.rs` replays the
    identical cases, and a copy in each language is two things to forget to
    update.
    """
    path = (
        Path(__file__).resolve().parents[2]
        / "packages"
        / "core"
        / "src"
        / "modules"
        / "hassault"
        / "__tests__"
        / "result-vectors.json"
    )
    assert path.exists(), (
        f"result vectors missing at {path} — regenerate with "
        "scripts/gen_hassault_result_vectors.py"
    )
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _result_vectors()["verdicts"], ids=lambda c: c["name"])
def test_is_recordable_matches_the_shared_fixture(case):
    """The predicate the native client mirrors, replayed case for case."""
    assert results.is_recordable(case) is case["expect"]["recordable"]


def test_an_empty_session_is_not_a_match():
    """The bug in one line: open the pane, deploy, leave. No opponent, nothing
    happened, and it used to be filed as a win."""
    assert not results.is_recordable(
        {"opponents": 0, "kills": 0, "deaths": 0, "damageDealt": 0}
    )


def test_a_lopsided_loss_is_still_a_match():
    """Deliberately not a quality bar. `XP_BASE` exists because turning up
    counts; what is excluded is the empty session, not the bad one."""
    assert results.is_recordable(
        {"opponents": 5, "kills": 0, "deaths": 15, "damageDealt": 0}
    )


def test_solo_is_never_a_victory():
    """`max(others, default=-1)` meant `0 >= -1`, so every abandoned session
    came back green."""
    room = make_room()
    alone = room.add("Alone", None)
    result = room.result_for(alone.id)
    assert result is not None
    assert result["recordable"] is False
    assert result["won"] is False
    assert result["mvp"] is False


def test_a_match_played_survives_the_bots_being_kicked():
    """`result_for` runs at leave time and reads the room, so a host who removes
    every bot before quitting would otherwise file a real match as an empty one."""
    room = make_room()
    you = room.add("You", None)
    room.add("Bot", None)
    # One tick is all it takes to latch: the room had two people in it.
    room.simulate(1 / 60)
    you.kills = 3
    room.remove_bots()

    result = room.result_for(you.id)
    assert result is not None
    assert result["opponents"] == 1
    assert result["recordable"] is True
    assert result["won"] is True

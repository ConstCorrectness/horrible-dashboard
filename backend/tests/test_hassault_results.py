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

"""P8: the wallet, keys, cases and StatTrak (`games_server/hassault_wallet.py`).

Each test names one rule: credits are paid once per thing that earned them and
capped by the day, a balance is a sum that cannot go below zero, a spend holds
the write lock (two racing spends of one balance make one key), a case opens
once and only with a key, the odds are the served ones, and StatTrak counts a
match's human kills once, onto the right skin.
"""

from __future__ import annotations

import random
import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _data(tmp_path, monkeypatch):
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    from backend.games_server import hassault_wallet

    monkeypatch.setattr(hassault_wallet, "_rng", random.Random(5))


def credit(account: str, amount: int, ref: str = "seed") -> None:
    from backend.games_server import hassault_wallet, store

    store.init_db()
    with store.get_conn() as conn:
        hassault_wallet._post(conn, account, amount, "grant", ref)


def win(kills: int = 5, humans: int = 1) -> dict:
    return {"kills": kills, "won": True, "humanOpponents": humans, "recordable": True}


# ---- earning -----------------------------------------------------------------------


def test_a_match_pays_once_and_less_against_bots():
    from backend.games_server import hassault_wallet as w

    paid = w.on_match("ada", "m1", win(5), 1, 1, now=1_000_000.0)
    assert paid["match"] == w.MATCH_BASE + 5 * w.MATCH_PER_KILL + w.MATCH_WIN
    assert w.on_match("ada", "m1", win(5), 1, 1, now=1_000_000.0)["match"] == 0
    bots = w.on_match("ada", "m2", win(5, humans=0), 1, 1, now=1_000_000.0)
    assert bots["match"] == int(paid["match"] * w.BOT_SHARE)


def test_matches_stop_paying_at_the_daily_cap():
    from backend.games_server import hassault_wallet as w

    day = 1_000_000.0
    total = sum(
        w.on_match("ada", f"m{i}", {"kills": 30, "humanOpponents": 1}, 1, 1, now=day)[
            "match"
        ]
        for i in range(20)
    )
    assert total == w.DAILY_MATCH_CAP
    # A new UTC day pays again.
    assert (
        w.on_match(
            "ada", "next", {"kills": 30, "humanOpponents": 1}, 1, 1, now=day + 86400
        )["match"]
        > 0
    )


def test_levels_and_the_weekly_win_pay_once_and_the_week_drops_a_case():
    from backend.games_server import hassault_wallet as w

    week = 1_000_000.0
    first = w.on_match("ada", "m1", win(), 1, 3, now=week)
    assert first["levels"] == 2 * w.LEVEL_REWARD
    assert first["weekly"] == w.WEEKLY_WIN_REWARD and first["case"]
    again = w.on_match("ada", "m2", win(), 1, 3, now=week + 60)
    assert again["levels"] == 0 and again["weekly"] == 0 and again["case"] is None
    assert [c["caseId"] for c in w.wallet("ada")["cases"]] == [w.WEEKLY_CASE]


def test_an_unrecordable_session_pays_nothing():
    from backend.games_server import hassault_wallet as w

    paid = w.on_match("ada", "m1", {"kills": 0, "recordable": False}, 1, 1)
    assert paid == {"match": 0, "levels": 0, "weekly": 0, "case": None}


# ---- spending ---------------------------------------------------------------------


def test_a_key_costs_its_price_and_the_floor_is_zero():
    from backend.games_server import hassault_wallet as w
    from backend.games_server import store

    with pytest.raises(w.InsufficientCredits):
        w.buy_key("ada")
    credit("ada", w.KEY_PRICE)
    assert w.buy_key("ada")["balance"] == 0
    assert w.wallet("ada")["keys"] == 1
    # The trigger, not only the code: a direct overdraft is refused.
    with pytest.raises(sqlite3.IntegrityError):
        with store.get_conn() as conn:
            conn.execute(
                "INSERT INTO hassault_wallet_ledger (account_id, delta, kind, ref, at) "
                "VALUES ('ada', -1, 'x', 'y', 0)"
            )


def test_two_racing_buys_of_one_keys_worth_make_one_key():
    from backend.games_server import hassault_wallet as w

    credit("ada", w.KEY_PRICE)
    outcomes: list[str] = []

    def buy() -> None:
        try:
            w.buy_key("ada")
            outcomes.append("key")
        except w.InsufficientCredits:
            outcomes.append("refused")
        except sqlite3.OperationalError:
            outcomes.append("busy")

    threads = [threading.Thread(target=buy) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes.count("key") == 1
    assert w.balance("ada") == 0 and w.wallet("ada")["keys"] == 1


def test_a_case_opens_once_and_only_with_a_key():
    from backend.games_server import hassault_wallet as w
    from backend.modules.hassault import cases

    case_id = w.on_match("ada", "m1", win(), 1, 1)["case"]
    assert w.open_case("ada", case_id) is None, "no key"
    credit("ada", 2 * w.KEY_PRICE)
    w.buy_key("ada")
    w.buy_key("ada")
    made = w.open_case("ada", case_id)
    assert made is not None and made["caseId"] == w.WEEKLY_CASE
    contents = {s.id for s in cases.CASE_BY_ID[w.WEEKLY_CASE].contents()}
    assert made["skinId"] in contents
    assert w.open_case("ada", case_id) is None, "already opened"
    assert w.wallet("ada")["keys"] == 1 and w.wallet("ada")["cases"] == []
    assert w.open_case("bob", case_id) is None


def test_the_inventory_lists_skins_not_cases_or_keys():
    from backend.games_server import hassault_account, hassault_wallet as w

    case_id = w.on_match("ada", "m1", win(), 1, 1)["case"]
    credit("ada", w.KEY_PRICE)
    w.buy_key("ada")
    assert hassault_account.inventory("ada") == []
    w.open_case("ada", case_id)
    assert len(hassault_account.inventory("ada")) == 1


# ---- the odds -----------------------------------------------------------------------


def test_each_cases_odds_cover_only_what_it_holds_and_sum_to_one():
    from backend.modules.hassault import cases

    for case in cases.CASES:
        odds = case.odds()
        assert abs(sum(odds.values()) - 1.0) < 1e-9
        assert set(odds) == {s.rarity for s in case.contents()}
        rng = random.Random(1)
        ids = {s.id for s in case.contents()}
        assert all(cases.roll(case, rng).skin_id in ids for _ in range(300))


def test_the_odds_are_served_by_node_and_server():
    from backend.app import app as node_app
    from backend.games_server.app import app as server_app
    from backend.modules.hassault import cases

    for client in (TestClient(node_app), TestClient(server_app)):
        listed = client.get("/api/hassault/cases").json()
        assert [c["id"] for c in listed] == [c.id for c in cases.CASES]
        assert abs(sum(o["chance"] for o in listed[0]["odds"]) - 1.0) < 1e-5


# ---- StatTrak ---------------------------------------------------------------------


def test_stattrak_counts_a_matchs_human_kills_once_onto_the_skin_for_that_side():
    from backend.games_server import hassault_account, hassault_wallet as w, store
    from backend.modules.hassault.skins import SKIN_CATALOG

    rifle = next(s for s in SKIN_CATALOG if s.weapon_id == "assault")
    store.init_db()
    with store.get_conn() as conn:
        w._insert_item(conn, "st1", "ada", "skin", rifle.id, "drop", "x", 0.1, 1, 0)
        w._insert_item(
            conn, "plain", "ada", "skin", rifle.id, "drop", "x", 0.1, 1, None
        )
    hassault_account.equip("ada", "st1", "t")
    hassault_account.equip("ada", "plain", "ct")
    kills = {("t", "assault"): 3, ("ct", "assault"): 4}
    assert w.count_stattrak("ada", "m1", kills) == 3, "the CT skin has no counter"
    assert w.count_stattrak("ada", "m1", kills) == 0, "once per match"
    inv = {i["instanceId"]: i for i in hassault_account.inventory("ada")}
    assert inv["st1"]["statTrackerKills"] == 3


def test_a_result_counts_human_opponents_and_human_kills_only():
    from backend.modules.hassault import bots, weapons
    from backend.modules.hassault.match import MatchServer

    room = MatchServer(record_replays=False).create("hd_pit")
    me = room.add("me", None, team=0)
    them = room.add("them", None, team=1)
    room.add("bot", None, brain=bots.BotBrain(skill=bots.SKILLS["normal"]), team=1)
    rifle = weapons.WEAPON_BY_ID["assault"]
    room._apply_damage(them, me, 999.0, False, rifle, 0.0)
    result = room.result_for(me.id)
    assert result["humanOpponents"] == 1
    assert result["humanKills"] == {"t:assault": 1}


# ---- the node --------------------------------------------------------------------


def test_a_node_with_no_account_has_no_wallet(monkeypatch):
    from backend.app import app
    from backend.modules.games import server_auth

    monkeypatch.setattr(server_auth, "signed_in_account", lambda: None)
    res = TestClient(app).get("/api/hassault/wallet")
    assert res.status_code == 409 and "game server" in res.json()["detail"]

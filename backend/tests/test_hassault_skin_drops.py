"""The level-up drop is a spend against a ledger, not a button.

The bug this file exists for: `POST /skins/claim_drop` rolled unconditionally.
The banner's "Claim Level-Up Drop" therefore produced a new item on every press,
never went away, and a player who held it down for a minute owned the Covert
tier — with an economy whose whole point is that a Covert is rare.

So what is pinned here is the *entitlement*, and the two ways it can leak: a
claim with nothing earned, and two claims arriving together for the same level.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.hassault import results
from backend.modules.hassault.skins import skin_manager

ACCOUNT = "local_player"


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    """A fresh `app.db` per test — and a cleared cache, since the manager is a
    process global whose in-memory inventories would otherwise cross over."""
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    skin_manager._inventories.clear()
    yield
    skin_manager._inventories.clear()


def a_match(**kw) -> str:
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
    return results.record(ACCOUNT, base)


def level_up(times: int = 1) -> None:
    """Play `times` matches worth exactly one level each.

    `XP_BASE` (100) plus 19_000 damage at 10 XP per 100 is `XP_PER_LEVEL` on the
    nose, so the level count these tests assert is arithmetic rather than "a big
    number, probably enough".
    """
    for _ in range(times):
        a_match(kills=0, deaths=0, headKills=0, won=False, damageDealt=19_000)


def test_a_player_who_has_never_played_has_nothing_to_claim():
    status = skin_manager.drop_status(ACCOUNT)
    assert status["level"] == 1
    assert status["available"] == 0
    assert skin_manager.claim_level_drop(ACCOUNT) is None


def test_the_claim_route_refuses_rather_than_dispensing():
    """The failure has to be visible. A 200 with no item would leave the banner
    lit and the player pressing it again."""
    res = TestClient(app).post("/api/hassault/skins/claim_drop")
    assert res.status_code == 409
    assert "level" in res.json()["detail"].lower()


def test_one_level_is_worth_exactly_one_drop():
    level_up(1)
    assert skin_manager.drop_status(ACCOUNT)["available"] == 1

    first = skin_manager.claim_level_drop(ACCOUNT)
    assert first is not None
    assert skin_manager.drop_status(ACCOUNT)["available"] == 0
    # The second press — the whole bug — gets nothing.
    assert skin_manager.claim_level_drop(ACCOUNT) is None


def test_levels_earned_while_away_are_all_still_claimable():
    """Entitlement accrues. Somebody who plays a session and opens the armoury
    afterwards should find every drop they earned, not the most recent one."""
    level_up(3)
    status = skin_manager.drop_status(ACCOUNT)
    assert status["available"] == 3
    for expected_left in (2, 1, 0):
        assert skin_manager.claim_level_drop(ACCOUNT) is not None
        assert skin_manager.drop_status(ACCOUNT)["available"] == expected_left
    assert skin_manager.claim_level_drop(ACCOUNT) is None


def test_a_claimed_drop_lands_in_the_inventory_once():
    level_up(1)
    before = len(skin_manager.get_inventory(ACCOUNT))
    drop = skin_manager.claim_level_drop(ACCOUNT)
    assert drop is not None
    inv = skin_manager.get_inventory(ACCOUNT)
    assert len(inv) == before + 1
    assert [i.instance_id for i in inv].count(drop.instance_id) == 1


def test_a_refused_claim_costs_nothing():
    """A claim that loses must not have half-happened: no skin, and no row
    consumed that would have paid for one."""
    level_up(1)
    skin_manager.claim_level_drop(ACCOUNT)
    size = len(skin_manager.get_inventory(ACCOUNT))
    assert skin_manager.claim_level_drop(ACCOUNT) is None
    assert len(skin_manager.get_inventory(ACCOUNT)) == size


def test_the_ledger_survives_a_restart():
    """The claim is in `app.db`, not in the manager's cache — otherwise every
    backend restart would refill the button."""
    level_up(2)
    assert skin_manager.claim_level_drop(ACCOUNT) is not None
    skin_manager._inventories.clear()
    assert skin_manager.drop_status(ACCOUNT)["available"] == 1


def test_a_match_drop_is_not_taken_out_of_the_level_entitlement():
    """`roll_drop` is the reward for *finishing a match*, handed out by the
    watchdog. It is earned by the thing that just happened, so it neither needs
    nor spends a level's claim."""
    level_up(1)
    skin_manager.roll_drop(ACCOUNT)
    assert skin_manager.drop_status(ACCOUNT)["available"] == 1


def test_the_route_reports_what_is_left():
    """The banner counts down without a second round trip, and the number comes
    from the ledger rather than from the browser decrementing its own copy."""
    level_up(2)
    res = TestClient(app).post("/api/hassault/skins/claim_drop")
    assert res.status_code == 200, res.text
    assert res.json()["remaining"] == 1

    status = TestClient(app).get("/api/hassault/skins/drops")
    assert status.status_code == 200
    assert status.json()["available"] == 1


def test_two_claims_racing_for_one_level_leave_one_winner(monkeypatch):
    """The guard is the primary key, not the read that precedes it.

    Both requests compute "level 2 is unclaimed" — that is what a race *is*.
    Simulated by making the read lie, because the only thing standing between
    the two of them is the INSERT.
    """
    level_up(1)
    assert skin_manager.claim_level_drop(ACCOUNT) is not None
    size = len(skin_manager.get_inventory(ACCOUNT))

    monkeypatch.setattr(skin_manager, "_claimed_levels", lambda account_id: set())
    monkeypatch.setattr(
        skin_manager,
        "drop_status",
        lambda account_id: {"available": 1, "level": 2},
    )
    assert skin_manager.claim_level_drop(ACCOUNT) is None
    assert len(skin_manager.get_inventory(ACCOUNT)) == size

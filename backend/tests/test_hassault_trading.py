"""P9: trading and the market (`games_server/hassault_trading.py`).

Each test names the abuse it closes: a fresh or unproven account cannot trade,
an offer goes only to a friend or a trade-link holder, a confirm waits for its
arming delay, a swap that finds anything changed moves nothing, imported and
held items never move, two buyers of one listing make one sale, and the market
fee is burnt rather than paid to anyone.
"""

from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from backend.games_server import hassault_trading as tr


@pytest.fixture(autouse=True)
def _data(tmp_path, monkeypatch):
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))


OLD = time.time() - 30 * 86400


def account(account_id: str, created: float = OLD, eligible: bool = True) -> str:
    """An account row, and (if `eligible`) the level and matches to trade."""
    from backend.games_server import hassault_account, store

    store.init_db()
    with store.get_conn() as conn:
        conn.execute(
            "INSERT INTO accounts (id, provider, subject, display_name, created_at) "
            "VALUES (?, 'test', ?, ?, ?)",
            (account_id, account_id, account_id, created),
        )
        if eligible:
            per = hassault_account._xp_per_level()
            for i in range(tr.MIN_SERVER_MATCHES):
                conn.execute(
                    "INSERT INTO hassault_xp_ledger VALUES (?, 'match', ?, ?, 0)",
                    (
                        account_id,
                        f"m{i}",
                        per * tr.MIN_LEVEL // tr.MIN_SERVER_MATCHES + 1,
                    ),
                )
    return account_id


def friends(a: str, b: str) -> None:
    from backend.games_server import store

    x, y = store._pair(a, b)
    with store.get_conn() as conn:
        conn.execute(
            "INSERT INTO friendships VALUES (?, ?, 'accepted', ?, 0)", (x, y, a)
        )


def item(
    owner: str, instance: str, hold: float | None = 0.0, kind: str = "skin"
) -> str:
    from backend.games_server import store
    from backend.modules.hassault.skins import SKIN_CATALOG

    with store.get_conn() as conn:
        conn.execute(
            "INSERT INTO hassault_items (instance_id, owner_id, def_id, kind, float_value, "
            "pattern_seed, origin, tradable_after, acquired_at) "
            "VALUES (?, ?, ?, ?, 0.2, 1, 'drop', ?, 0)",
            (instance, owner, SKIN_CATALOG[0].id, kind, hold),
        )
    return instance


def owner_of(instance: str) -> str:
    from backend.games_server import store

    with store.get_conn() as conn:
        return conn.execute(
            "SELECT owner_id FROM hassault_items WHERE instance_id = ?", (instance,)
        ).fetchone()["owner_id"]


def credit(account_id: str, amount: int) -> None:
    from backend.games_server import hassault_wallet, store

    with store.get_conn() as conn:
        hassault_wallet._post(
            conn, account_id, amount, "grant", f"g{amount}{account_id}"
        )


# ---- who may trade ------------------------------------------------------------------


def test_a_new_or_unproven_account_cannot_trade():
    account("fresh", created=time.time())
    account("idle", eligible=False)
    assert not tr.eligibility("fresh")["eligible"]
    reasons = tr.eligibility("idle")["reasons"]
    assert any("level" in r for r in reasons) and any(
        "server matches" in r for r in reasons
    )
    assert not tr.eligibility("guest_123")["eligible"]
    account("ada")
    assert tr.eligibility("ada")["eligible"]


def test_an_offer_goes_to_a_friend_or_a_trade_link_holder():
    account("ada"), account("bob"), account("cy")
    item("ada", "a1")
    with pytest.raises(tr.Refused, match="friends"):
        tr.offer("ada", "bob", ["a1"], [])
    with pytest.raises(tr.Refused, match="friends"):
        tr.offer("ada", "bob", ["a1"], [], token="guess")
    token = tr.trade_link("bob")
    assert tr.offer("ada", "bob", ["a1"], [], token=token)["state"] == "offered"
    # Rotating revokes the old link.
    tr.trade_link("bob", rotate=True)
    item("ada", "a2")
    with pytest.raises(tr.Refused):
        tr.offer("ada", "bob", ["a2"], [], token=token)


# ---- the swap ---------------------------------------------------------------------


def test_a_trade_arms_then_swaps_both_sides_with_a_fresh_hold_and_no_equips():
    from backend.games_server import hassault_account

    account("ada"), account("bob")
    friends("ada", "bob")
    item("ada", "a1"), item("bob", "b1")
    hassault_account.equip("ada", "a1")
    t = tr.offer("ada", "bob", ["a1"], ["b1"])
    now = time.time()
    tr.accept("bob", t["tradeId"], now=now)
    with pytest.raises(tr.Refused, match="arms in"):
        tr.confirm("bob", t["tradeId"], now=now + 1)
    done = tr.confirm("bob", t["tradeId"], now=now + tr.ARMING_DELAY + 0.1)
    assert done["state"] == "done"
    assert owner_of("a1") == "bob" and owner_of("b1") == "ada"
    inv = {i["instanceId"]: i for i in hassault_account.inventory("bob")}
    assert inv["a1"]["isEquipped"] is False
    with pytest.raises(tr.Refused, match="trade hold"):
        tr.offer("bob", "ada", ["a1"], [])


def test_a_swap_that_finds_anything_changed_moves_nothing():
    account("ada"), account("bob")
    friends("ada", "bob")
    item("ada", "a1"), item("bob", "b1")
    t = tr.offer("ada", "bob", ["a1"], ["b1"])
    now = time.time()
    tr.accept("bob", t["tradeId"], now=now)
    # Between accept and confirm, Ada lists what she offered.
    tr.list_item("ada", "a1", 100)
    with pytest.raises(tr.Refused, match="listed"):
        tr.confirm("bob", t["tradeId"], now=now + tr.ARMING_DELAY + 1)
    assert owner_of("a1") == "ada" and owner_of("b1") == "bob"
    assert tr.trades_for("bob")[0]["state"] == "failed"


def test_imported_and_held_items_never_move_and_only_the_right_party_acts():
    account("ada"), account("bob")
    friends("ada", "bob")
    item("ada", "imported", hold=None)
    item("ada", "held", hold=time.time() + 3600)
    with pytest.raises(tr.Refused, match="imported"):
        tr.offer("ada", "bob", ["imported"], [])
    with pytest.raises(tr.Refused, match="hold"):
        tr.offer("ada", "bob", ["held"], [])
    item("ada", "ok")
    t = tr.offer("ada", "bob", ["ok"], [])
    with pytest.raises(tr.Refused):
        tr.accept("ada", t["tradeId"])
    with pytest.raises(tr.Refused):
        tr.close("bob", t["tradeId"], "cancelled")
    assert tr.close("ada", t["tradeId"], "cancelled")["state"] == "cancelled"


def test_open_offers_between_two_players_are_capped():
    account("ada"), account("bob")
    friends("ada", "bob")
    for i in range(tr.PENDING_PER_PAIR):
        tr.offer("ada", "bob", [item("ada", f"x{i}")], [])
    with pytest.raises(tr.Refused, match="open offers"):
        tr.offer("ada", "bob", [item("ada", "one_more")], [])


# ---- the market -------------------------------------------------------------------


def test_a_sale_pays_the_seller_less_a_burnt_fee():
    from backend.games_server import hassault_wallet, store

    account("ada"), account("bob")
    item("ada", "a1")
    listing = tr.list_item("ada", "a1", 500)
    assert listing["fee"] == 50
    credit("bob", 600)
    sold = tr.buy("bob", listing["listingId"])
    assert sold["price"] == 500
    assert hassault_wallet.balance("bob") == 100
    assert hassault_wallet.balance("ada") == 450
    assert owner_of("a1") == "bob"
    with store.get_conn() as conn:
        burnt = conn.execute("SELECT SUM(amount) AS n FROM hassault_sinks").fetchone()[
            "n"
        ]
    assert burnt == 50
    assert tr.listings() == []


def test_a_listed_item_is_out_of_reach_until_cancelled():
    from backend.games_server import hassault_account

    account("ada"), account("bob")
    friends("ada", "bob")
    item("ada", "a1")
    listing = tr.list_item("ada", "a1", 100)
    assert not hassault_account.equip("ada", "a1")
    with pytest.raises(tr.Refused):
        tr.offer("ada", "bob", ["a1"], [])
    with pytest.raises(tr.Refused):
        tr.list_item("ada", "a1", 100)
    assert tr.cancel_listing("ada", listing["listingId"])
    assert hassault_account.equip("ada", "a1")


def test_a_purchase_without_the_credits_moves_nothing_and_prices_are_bounded():
    account("ada"), account("bob")
    item("ada", "a1")
    with pytest.raises(tr.Refused, match="prices"):
        tr.list_item("ada", "a1", tr.MIN_PRICE - 1)
    listing = tr.list_item("ada", "a1", 200)
    with pytest.raises(tr.Refused, match="costs"):
        tr.buy("bob", listing["listingId"])
    assert owner_of("a1") == "ada"
    with pytest.raises(tr.Refused, match="own"):
        tr.buy("ada", listing["listingId"])


def test_two_buyers_of_one_listing_make_one_sale():
    from backend.games_server import hassault_wallet

    account("ada"), account("bob"), account("cy")
    item("ada", "a1")
    listing = tr.list_item("ada", "a1", 100)
    credit("bob", 100), credit("cy", 100)
    outcomes: list[str] = []

    def attempt(buyer: str) -> None:
        try:
            tr.buy(buyer, listing["listingId"])
            outcomes.append(buyer)
        except tr.Refused:
            outcomes.append("refused")
        except Exception:  # noqa: BLE001 - a busy lock counts as not buying
            outcomes.append("busy")

    threads = [threading.Thread(target=attempt, args=(b,)) for b in ("bob", "cy")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    winners = [o for o in outcomes if o in ("bob", "cy")]
    assert len(winners) == 1
    loser = "cy" if winners[0] == "bob" else "bob"
    assert hassault_wallet.balance(loser) == 100, "the losing buyer paid nothing"
    assert owner_of("a1") == winners[0]


def test_the_market_and_its_history_are_served():
    from backend.games_server.app import app
    from backend.modules.hassault.skins import SKIN_CATALOG

    account("ada"), account("bob")
    item("ada", "a1")
    listing = tr.list_item("ada", "a1", 300)
    client = TestClient(app)
    listed = client.get("/api/hassault/market").json()
    assert listed[0]["listingId"] == listing["listingId"]
    assert listed[0]["item"]["instanceId"] == "a1"
    credit("bob", 300)
    tr.buy("bob", listing["listingId"])
    history = client.get(
        "/api/hassault/market/history", params={"skin_id": SKIN_CATALOG[0].id}
    ).json()
    assert [h["price"] for h in history] == [300]
    refused = client.post(
        "/api/hassault/account/market/list",
        json={"instanceId": "nope", "price": 100},
        headers={"Authorization": "Bearer bob"},
    )
    assert refused.status_code == 409


def test_a_node_with_no_account_has_no_trading(monkeypatch):
    from backend.app import app
    from backend.modules.games import server_auth

    monkeypatch.setattr(server_auth, "signed_in_account", lambda: None)
    client = TestClient(app)
    assert client.get("/api/hassault/trades").status_code == 409
    assert client.get("/api/hassault/market").json() == []


def test_a_partners_items_are_shown_only_to_a_friend_or_a_link_holder():
    account("ada"), account("bob"), account("cy")
    item("bob", "b1")
    item("bob", "imported", hold=None)
    with pytest.raises(tr.Refused, match="friends"):
        tr.partner_items("cy", "bob")
    friends("ada", "bob")
    assert [i["instanceId"] for i in tr.partner_items("ada", "bob")] == ["b1"]
    token = tr.trade_link("bob")
    assert [i["instanceId"] for i in tr.partner_items("cy", "bob", token)] == ["b1"]

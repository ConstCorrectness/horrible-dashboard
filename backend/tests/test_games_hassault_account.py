"""hassault account data on the game server (`backend/games_server/hassault_account.py`).

Each test names a rule from that module's docstring: the account is the token's,
level is a ledger sum, a drop is spent by its claim row, an import happens once
per machine and never pays a drop, and machine-scoped settings never sync.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    from backend.games_server.app import app

    return TestClient(app)


def bearer(account: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {account}"}


def give_xp(account: str, xp: int, match: str = "m1") -> None:
    from backend.games_server import hassault_account, store

    store.init_db()
    with store.get_conn() as conn:
        conn.execute(
            "INSERT INTO hassault_xp_ledger VALUES (?, 'match', ?, ?, 0)",
            (account, match, xp),
        )
    assert hassault_account.progression(account)["totalXp"] >= xp


def test_every_route_needs_a_token(server):
    for method, path in (
        ("get", "/api/hassault/account/settings"),
        ("get", "/api/hassault/account/inventory"),
        ("get", "/api/hassault/account/progression"),
        ("post", "/api/hassault/account/claim_drop"),
    ):
        assert getattr(server, method)(path).status_code == 401, path


def test_settings_sync_only_account_keys(server):
    res = server.put(
        "/api/hassault/account/settings",
        json={
            "values": {
                "hassault.sensitivity": 2.5,
                "hassault.video.gpu": "x",
                "junk": 1,
            }
        },
        headers=bearer("ada"),
    ).json()
    assert res["values"] == {"hassault.sensitivity": 2.5}
    assert set(res["dropped"]) >= {"junk"}
    got = server.get("/api/hassault/account/settings", headers=bearer("ada")).json()
    assert got["values"] == {"hassault.sensitivity": 2.5}
    # And nobody else's.
    other = server.get("/api/hassault/account/settings", headers=bearer("bob")).json()
    assert other["values"] == {}


def test_a_server_match_ledgers_xp_once(server):
    from backend.games_server import hassault_account

    result = {"kills": 3, "deaths": 1, "won": True, "damageDealt": 500, "opponents": 2}
    xp = hassault_account.grant_match_xp("ada", "room-1:ada", result)
    hassault_account.grant_match_xp("ada", "room-1:ada", result)
    assert xp > 0
    prog = server.get("/api/hassault/account/progression", headers=bearer("ada")).json()
    assert prog["totalXp"] == xp


def test_a_drop_is_spent_by_its_claim_and_only_once(server):
    from backend.games_server import hassault_account

    per = hassault_account._xp_per_level()
    give_xp("ada", per)  # level 2: one drop
    first = server.post("/api/hassault/account/claim_drop", headers=bearer("ada"))
    assert first.status_code == 200, first.text
    assert first.json()["remaining"] == 0
    again = server.post("/api/hassault/account/claim_drop", headers=bearer("ada"))
    assert again.status_code == 409
    inv = server.get("/api/hassault/account/inventory", headers=bearer("ada")).json()
    assert [i["instanceId"] for i in inv] == [first.json()["instanceId"]]


def test_two_racing_claims_pay_one_drop(server):
    from backend.games_server import hassault_account, store

    give_xp("ada", hassault_account._xp_per_level())
    results: list[object] = []

    def claim():
        try:
            results.append(hassault_account.claim_drop("ada"))
        except (
            sqlite3.OperationalError
        ) as exc:  # a locked database is a loss, not a win
            results.append(exc)

    threads = [threading.Thread(target=claim) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wins = [r for r in results if isinstance(r, dict)]
    assert len(wins) == 1
    with store.get_conn() as conn:
        owned = conn.execute(
            "SELECT COUNT(*) AS n FROM hassault_items WHERE owner_id = 'ada'"
        ).fetchone()["n"]
    assert owned == 1


def _node_item(instance: str, equipped: bool = False) -> dict:
    from backend.modules.hassault.skins import SKIN_CATALOG

    return {
        "instanceId": instance,
        "skinId": SKIN_CATALOG[0].id,
        "floatValue": 0.2,
        "patternSeed": 7,
        "acquiredAt": 1.0,
        "isEquipped": equipped,
    }


def test_an_import_lands_once_untradable_and_pays_no_drops(server):
    from backend.games_server import hassault_account

    per = hassault_account._xp_per_level()
    body = {
        "nodeId": "node-a",
        "items": [_node_item("i1", equipped=True), _node_item("i2")],
        "matches": [{"id": "m1", "xp": per}, {"id": "m2", "xp": per}],
    }
    first = server.post(
        "/api/hassault/account/import", json=body, headers=bearer("ada")
    ).json()
    assert first["imported"] is True and first["items"] == 2 and first["xp"] == 2 * per
    second = server.post(
        "/api/hassault/account/import", json=body, headers=bearer("ada")
    ).json()
    assert second["imported"] is False

    inv = server.get("/api/hassault/account/inventory", headers=bearer("ada")).json()
    assert {i["instanceId"] for i in inv} == {"i1", "i2"}
    assert all(i["isTradable"] is False for i in inv)
    prog = server.get("/api/hassault/account/progression", headers=bearer("ada")).json()
    assert prog["level"] == 3
    # Levels 2 and 3 were reached on a node's word: pre-claimed, nothing to take.
    assert prog["available"] == 0
    # The next level earned on the server pays normally.
    give_xp("ada", per, match="server-1")
    assert hassault_account.progression("ada")["available"] == 1


def test_a_copied_database_does_not_duplicate_items(server):
    body = {"nodeId": "node-a", "items": [_node_item("i1")], "matches": []}
    server.post("/api/hassault/account/import", json=body, headers=bearer("ada"))
    body["nodeId"] = "node-b"  # same instance ids, another machine
    res = server.post(
        "/api/hassault/account/import", json=body, headers=bearer("ada")
    ).json()
    assert res["items"] == 0 and res["skipped"] == 1


def test_equip_is_one_per_weapon_and_only_your_own(server):
    from backend.modules.hassault.skins import SKIN_CATALOG

    weapon = SKIN_CATALOG[0].weapon_id
    same = [s.id for s in SKIN_CATALOG if s.weapon_id == weapon][:2]
    items = [dict(_node_item(f"e{i}"), skinId=sid) for i, sid in enumerate(same)]
    server.post(
        "/api/hassault/account/import",
        json={"nodeId": "n", "items": items, "matches": []},
        headers=bearer("ada"),
    )
    for item in items:
        ok = server.post(
            "/api/hassault/account/equip",
            params={"instance_id": item["instanceId"]},
            headers=bearer("ada"),
        ).json()
        assert ok["ok"]
    inv = server.get("/api/hassault/account/inventory", headers=bearer("ada")).json()
    assert [i["instanceId"] for i in inv if i["isEquipped"]] == [
        items[-1]["instanceId"]
    ]
    stolen = server.post(
        "/api/hassault/account/equip",
        params={"instance_id": items[0]["instanceId"]},
        headers=bearer("bob"),
    ).json()
    assert stolen["ok"] is False


def test_a_trade_up_burns_ten_and_makes_one(server):
    from backend.modules.hassault.skins import RARITY_ORDER, SKIN_CATALOG

    rarity = next(
        r
        for r in RARITY_ORDER[:-1]
        if any(s.rarity == r for s in SKIN_CATALOG)
        and any(
            s.rarity == RARITY_ORDER[RARITY_ORDER.index(r) + 1] for s in SKIN_CATALOG
        )
    )
    skin = next(s for s in SKIN_CATALOG if s.rarity == rarity)
    items = [dict(_node_item(f"t{i}"), skinId=skin.id) for i in range(10)]
    server.post(
        "/api/hassault/account/import",
        json={"nodeId": "n", "items": items, "matches": []},
        headers=bearer("ada"),
    )
    made = server.post(
        "/api/hassault/account/tradeup",
        json={"instanceIds": [i["instanceId"] for i in items]},
        headers=bearer("ada"),
    )
    assert made.status_code == 200, made.text
    inv = server.get("/api/hassault/account/inventory", headers=bearer("ada")).json()
    assert [i["instanceId"] for i in inv] == [made.json()["instanceId"]]


def test_the_migration_upgrades_an_existing_database(tmp_path, monkeypatch):
    """Additive only: an install at the previous version gains the tables."""
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    from backend.games_server import store

    store.init_db()
    with store.get_conn() as conn:
        conn.execute("DROP TABLE hassault_items")
        conn.execute(
            "UPDATE schema_meta SET version = ?",
            (store.MIGRATIONS.index(store._m12_hassault_accounts),),
        )
    store.init_db()
    with store.get_conn() as conn:
        tables = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        version = conn.execute("SELECT version FROM schema_meta").fetchone()["version"]
    assert "hassault_items" in tables
    assert version == len(store.MIGRATIONS)

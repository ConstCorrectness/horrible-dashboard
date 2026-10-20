"""P5: loadouts per side, and how a player looks to everyone else.

Each test names a rule from `cosmetics.py` or `hassault_account.py`: a side
carries its own guns and skins, the server builds a look from its own tables
and never from a client, a guest's claimed look is cleaned, and a look never
rides a snapshot.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from backend.modules.hassault import cosmetics, weapons


def ids(loadout: dict[str, int]) -> dict[str, str]:
    return {slot: weapons.WEAPONS[i].id for slot, i in loadout.items()}


# ---- loadouts per side -------------------------------------------------------------


def test_a_flat_loadout_applies_to_both_sides_within_each_sides_rules():
    t_rifle = next(w for w in weapons.WEAPONS if w.side == "t" and w.slot == "primary")
    both = weapons.loadouts_for({"primary": t_rifle.id, "secondary": "pistol"})
    assert ids(both["t"])["primary"] == t_rifle.id
    # The other side cannot carry it: that slot alone falls back.
    assert ids(both["ct"])["primary"] == weapons.DEFAULT_LOADOUT["primary"]
    assert ids(both["ct"])["secondary"] == "pistol"


def test_a_per_side_loadout_is_read_from_the_stored_string():
    ct_rifle = next(
        w for w in weapons.WEAPONS if w.side == "ct" and w.slot == "primary"
    )
    stored = json.dumps({"t": {"primary": "shotgun"}, "ct": {"primary": ct_rifle.id}})
    both = weapons.loadouts_for(stored)
    assert ids(both["t"])["primary"] == "shotgun"
    assert ids(both["ct"])["primary"] == ct_rifle.id
    assert weapons.loadouts_for("not json") == weapons.loadouts_for(None)


def test_a_spawn_picks_the_loadout_for_the_players_team():
    from backend.modules.hassault.match import MatchServer

    room = MatchServer(record_replays=False).create("hd_atrium")
    chosen = weapons.loadouts_for(
        {"t": {"primary": "shotgun"}, "ct": {"primary": "sniper"}}
    )
    player = room.add("p", None, team=1, loadouts=chosen)
    assert weapons.WEAPONS[player.weapon].id == "sniper"
    player.team = 0
    room.respawn(player)
    assert weapons.WEAPONS[player.weapon].id == "shotgun"


# ---- agents and bundles ------------------------------------------------------------


def test_an_agent_must_suit_its_side():
    arc = next(
        a for a in cosmetics.AGENTS if a.faction == "arc" and a.id != "arc_default"
    )
    neutral = next(a for a in cosmetics.AGENTS if a.faction == "neutral")
    chosen = cosmetics.agents_for({"t": arc.id, "ct": arc.id})
    assert chosen == {"t": arc.id, "ct": cosmetics.DEFAULT_AGENTS["ct"]}
    assert cosmetics.agents_for(json.dumps({"ct": neutral.id}))["ct"] == neutral.id
    assert cosmetics.agents_for({"t": "nobody"})["t"] == cosmetics.DEFAULT_AGENTS["t"]


def test_a_guests_claimed_look_is_cleaned_not_trusted():
    claimed = {
        "t": {
            "agent": {"id": "arc_default", "armor": "#ff00ff", "trim": "#ff00ff"},
            "weapons": {
                "assault": {
                    "skinId": "x",
                    "baseColor": "#112233",
                    "accentColor": "#445566",
                    "patternType": "fade",
                    "floatValue": 7,
                },
                "pistol": {"baseColor": "red", "accentColor": "#000000"},
                "railgun": {"baseColor": "#000000", "accentColor": "#000000"},
            },
        },
        "ct": "nonsense",
    }
    clean = cosmetics.clean_bundle(claimed)
    # Agent colours are always ours, whatever was claimed.
    arc = cosmetics.AGENT_BY_ID["arc_default"]
    assert clean["t"]["agent"] == {
        "id": "arc_default",
        "armor": arc.armor,
        "trim": arc.trim,
    }
    assert set(clean["t"]["weapons"]) == {"assault"}
    assert clean["t"]["weapons"]["assault"]["floatValue"] == 1.0
    assert clean["ct"] == cosmetics.default_bundle()["ct"]


# ---- the account service -----------------------------------------------------------


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    from backend.games_server.app import app

    return TestClient(app)


def bearer(account: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {account}"}


def own(server: TestClient, account: str, skin_ids: list[str]) -> list[str]:
    items = [
        {
            "instanceId": f"{account}-{i}",
            "skinId": sid,
            "floatValue": 0.1,
            "patternSeed": 3,
            "acquiredAt": 1.0,
        }
        for i, sid in enumerate(skin_ids)
    ]
    server.post(
        "/api/hassault/account/import",
        json={"nodeId": "n", "items": items, "matches": []},
        headers=bearer(account),
    )
    return [i["instanceId"] for i in items]


def rifle_skins(count: int) -> list[str]:
    from backend.modules.hassault.skins import SKIN_CATALOG

    return [s.id for s in SKIN_CATALOG if s.weapon_id == "assault"][:count]


def test_a_skin_can_be_equipped_for_one_side(server):
    t_item, ct_item = own(server, "ada", rifle_skins(2))
    for item, side in ((t_item, "t"), (ct_item, "ct")):
        res = server.post(
            "/api/hassault/account/equip",
            params={"instance_id": item, "side": side},
            headers=bearer("ada"),
        )
        assert res.json() == {"ok": True}
    look = server.get("/api/hassault/account/cosmetics", headers=bearer("ada")).json()
    skins = rifle_skins(2)
    assert look["t"]["weapons"]["assault"]["skinId"] == skins[0]
    assert look["ct"]["weapons"]["assault"]["skinId"] == skins[1]
    inv = server.get("/api/hassault/account/inventory", headers=bearer("ada")).json()
    assert {i["instanceId"]: i["equippedSides"] for i in inv} == {
        t_item: ["t"],
        ct_item: ["ct"],
    }
    assert all(i["isEquipped"] for i in inv)

    # Equipping one for both sides replaces the other on its side too.
    server.post(
        "/api/hassault/account/equip",
        params={"instance_id": t_item},
        headers=bearer("ada"),
    )
    inv = server.get("/api/hassault/account/inventory", headers=bearer("ada")).json()
    assert {i["instanceId"]: i["isEquipped"] for i in inv} == {
        t_item: True,
        ct_item: False,
    }

    server.post(
        "/api/hassault/account/unequip",
        params={"weapon_id": "assault", "side": "t"},
        headers=bearer("ada"),
    )
    look = server.get("/api/hassault/account/cosmetics", headers=bearer("ada")).json()
    assert "assault" not in look["t"]["weapons"]
    assert look["ct"]["weapons"]["assault"]["skinId"] == rifle_skins(1)[0]
    bad = server.post(
        "/api/hassault/account/equip",
        params={"instance_id": t_item, "side": "spectator"},
        headers=bearer("ada"),
    )
    assert bad.status_code == 400


def test_the_look_comes_from_the_accounts_own_rows(server):
    neutral = next(a for a in cosmetics.AGENTS if a.faction == "neutral")
    server.put(
        "/api/hassault/account/settings",
        json={"values": {"hassault.agents": json.dumps({"ct": neutral.id})}},
        headers=bearer("ada"),
    )
    (item,) = own(server, "bob", rifle_skins(1))
    # Ada cannot wear Bob's item, by asking or otherwise.
    stolen = server.post(
        "/api/hassault/account/equip",
        params={"instance_id": item},
        headers=bearer("ada"),
    ).json()
    assert stolen == {"ok": False}
    look = server.get("/api/hassault/account/cosmetics", headers=bearer("ada")).json()
    assert look["ct"]["agent"]["id"] == neutral.id
    assert look["t"]["agent"]["id"] == cosmetics.DEFAULT_AGENTS["t"]
    assert look["t"]["weapons"] == {} and look["ct"]["weapons"] == {}


def test_a_trade_up_takes_burnt_items_off(server):
    from backend.games_server import hassault_account
    from backend.modules.hassault.skins import RARITY_ORDER, SKIN_CATALOG

    rarity = RARITY_ORDER[0]
    same = [s.id for s in SKIN_CATALOG if s.rarity == rarity]
    owned = own(server, "ada", [same[i % len(same)] for i in range(10)])
    hassault_account.equip("ada", owned[0])
    assert hassault_account.trade_up("ada", owned) is not None
    look = hassault_account.cosmetics("ada")
    assert look["t"]["weapons"] == {} and look["ct"]["weapons"] == {}


def test_the_migration_puts_an_equipped_item_on_both_sides(tmp_path, monkeypatch):
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    from backend.games_server import hassault_account, store
    from backend.modules.hassault.skins import SKIN_CATALOG

    store.init_db()
    with store.get_conn() as conn:
        conn.execute("DROP TABLE hassault_equips")
        conn.execute(
            "UPDATE schema_meta SET version = ?",
            (store.MIGRATIONS.index(store._m13_hassault_equips),),
        )
        conn.execute(
            "INSERT INTO hassault_items (instance_id, owner_id, def_id, float_value, "
            "pattern_seed, origin, equipped, acquired_at) "
            "VALUES ('i1', 'ada', ?, 0.2, 1, 'drop', 1, 0)",
            (SKIN_CATALOG[0].id,),
        )
    store.init_db()
    weapon = SKIN_CATALOG[0].weapon_id
    look = hassault_account.cosmetics("ada")
    assert look["t"]["weapons"][weapon]["skinId"] == SKIN_CATALOG[0].id
    assert look["ct"]["weapons"][weapon]["skinId"] == SKIN_CATALOG[0].id


# ---- in a match --------------------------------------------------------------------


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, raw: str) -> None:
        self.sent.append(json.loads(raw))


def test_a_server_seat_joins_wearing_its_accounts_look(server):
    from backend.games_server import hassault_account
    from backend.games_server.hassault_rooms import HassaultReferee, SeatConn

    (item,) = own(server, "ada", rifle_skins(1))
    hassault_account.equip("ada", item, "t")
    hassault_account.settings_put(
        "ada", {"hassault.loadout": json.dumps({"t": {"primary": "shotgun"}})}
    )
    referee = HassaultReferee()

    async def go() -> None:
        ada = SeatConn(FakeSocket(), "ada", "ada")
        guest = SeatConn(FakeSocket(), "guest_1", "g", is_guest=True)
        welcome = await referee.join(ada, "hd_atrium")
        me = welcome["playerId"]
        assert (
            welcome["cosmetics"][me]["t"]["weapons"]["assault"]["skinId"]
            == rifle_skins(1)[0]
        )
        room, player = referee.server.player_for(ada)
        if player.team == 0:
            assert weapons.WEAPONS[player.weapon].id == "shotgun"
        theirs = await referee.join(guest, "hd_atrium")
        assert theirs["cosmetics"][theirs["playerId"]] == cosmetics.default_bundle()
        joined = [m for m in ada.websocket.sent if m.get("event") == "joined"]
        assert joined and joined[-1]["data"]["cosmetics"] == cosmetics.default_bundle()

        # A change on the account reaches the room as it happens.
        hassault_account.unequip("ada", "assault")
        await referee.refresh_account("ada")
        changed = [m for m in guest.websocket.sent if m.get("event") == "cosmetics"]
        assert changed[-1]["data"]["playerId"] == me
        assert changed[-1]["data"]["cosmetics"]["t"]["weapons"] == {}

        # Never in a snapshot: it is a kilobyte that changes once a match.
        now = time.monotonic()
        rows = [p.snapshot(now) for p in room.players.values()]
        raw = json.dumps(room.snapshot_for(player, now, rows))
        assert "cosmetics" not in raw and "baseColor" not in raw

    asyncio.run(go())


def test_the_agent_list_is_served_by_node_and_server(server):
    from backend.app import app as node_app

    for client in (TestClient(node_app), server):
        listed = client.get("/api/hassault/agents").json()
        assert [a["id"] for a in listed] == [a.id for a in cosmetics.AGENTS]
        default = next(a for a in listed if a["id"] == "halon_default")
        assert default["sides"] == ["ct"]


def test_the_committed_fixture_is_current():
    from scripts.gen_hassault_cosmetics_vectors import OUT, payload

    with open(OUT, encoding="utf-8") as fh:
        committed = json.load(fh)
    assert committed == json.loads(json.dumps(payload())), (
        "cosmetics-vectors.json is stale: "
        "PYTHONPATH=. uv run python scripts/gen_hassault_cosmetics_vectors.py"
    )

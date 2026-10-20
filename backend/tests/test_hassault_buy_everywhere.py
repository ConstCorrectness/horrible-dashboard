"""P6: the in-match economy, everywhere.

The catalogue is built from the weapon table, a side buys only its own guns, a
gun bought over another in the same slot puts the old one on the floor, each
weapon pays its own kill reward, buying needs the buy window *and* the buy zone,
bots actually buy, and a game-server room can run defuse.
"""

from __future__ import annotations

import asyncio
import itertools
import json

import pytest

from backend.modules.hassault import assets, modes, physics, pickups, weapons
from backend.modules.hassault.match import Command, MatchRoom
from backend.modules.hassault.modes import objectives
from backend.modules.hassault.modes.defuse import (
    BUY_TIME,
    BUY_ZONE_RADIUS,
    CATALOG,
    FREE_SLOTS,
    FREEZE_TIME,
    KILL_REWARD,
)

BY_ID = {item.id: index for index, item in enumerate(CATALOG)}
_SEQ = itertools.count(1)


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
    t = room.add("t", None, team=0)
    ct = room.add("ct", None, team=1)
    room.simulate(0.05)  # warmup -> round 1, freeze
    for p in (t, ct):
        p.money = 16000
    return room, t, ct


def buy(room: MatchRoom, player, item: str) -> None:
    room.enqueue(
        player,
        Command(
            seq=next(_SEQ),
            forward=0.0,
            strafe=0.0,
            jump=False,
            yaw=0.0,
            pitch=0.0,
            dt=0.016,
            buy=BY_ID[item],
        ),
    )
    room.simulate(0.05)


def index_of(weapon_id: str) -> int:
    return weapons.WEAPONS.index(weapons.WEAPON_BY_ID[weapon_id])


# ---- the catalogue -------------------------------------------------------------


def test_every_priced_gun_is_for_sale_at_its_own_price_and_side():
    sold = {item.id: item for item in CATALOG if item.kind == "weapon"}
    for index, spec in enumerate(weapons.WEAPONS):
        if index in FREE_SLOTS or spec.price <= 0 or spec.slot == "melee":
            assert spec.id not in sold
            continue
        item = sold[spec.id]
        assert (item.slot, item.price, item.side) == (index, spec.price, spec.side)
    # Sidearms first, then primaries, then gear: the order a menu lists them.
    categories = [item.category for item in CATALOG]
    assert categories.index("primary") > categories.index("secondary")


def test_the_served_catalogue_says_who_may_buy_each_row(game):
    room, *_ = game
    rows = room.state_payload()["mode"]["catalog"]
    assert len(rows) == len(CATALOG)
    lancer = next(r for r in rows if r["id"] == "lancer")
    assert lancer["side"] == "ct" and lancer["category"] == "primary"
    assert room.state_payload()["mode"]["config"]["buyTime"] == BUY_TIME


def test_a_side_buys_only_its_own_guns(game):
    room, t, ct = game
    buy(room, t, "lancer")
    assert index_of("lancer") not in t.owned and t.money == 16000
    buy(room, ct, "lancer")
    assert index_of("lancer") in ct.owned
    buy(room, t, "raider")
    assert index_of("raider") in t.owned


def test_a_gun_bought_over_another_puts_the_old_one_on_the_floor(game):
    room, t, _ = game
    buy(room, t, "shotgun")
    buy(room, t, "assault")
    assert index_of("assault") in t.owned
    assert index_of("shotgun") not in t.owned
    drops = room.mode.dropped_weapons
    assert [d.slot for d in drops] == [index_of("shotgun")]


def test_a_bought_sidearm_replaces_the_free_pistol_on_the_keys(game):
    room, t, _ = game
    buy(room, t, "hand_cannon")
    cannon = index_of("hand_cannon")
    view = room.private_view_for(t)
    assert view["slots"]["secondary"] == cannon
    assert cannon in view["held"] and 1 not in view["held"]
    buy(room, t, "assault")
    assert room.private_view_for(t)["slots"]["primary"] == index_of("assault")


# ---- rewards ---------------------------------------------------------------------


def test_each_weapon_pays_its_own_kill_reward(game):
    room, t, ct = game
    t.money = 0
    room.mode.on_kill(room, ct, t, False, weapons.WEAPON_BY_ID["shotgun"])
    assert t.money == weapons.WEAPON_BY_ID["shotgun"].kill_reward
    t.money = 0
    room.mode.on_kill(room, ct, t, False, object())  # a grenade, say
    assert t.money == KILL_REWARD


# ---- the buy window and the buy zone -----------------------------------------------


def test_buying_needs_the_buy_zone_once_alive(game):
    room, t, _ = game
    # The zones of the end this player spawns at this round: the role's, not the team's.
    zones = room.mode.buy_zones[room.mode.spawn_end(t.team)]
    assert zones, "a map with T spawns has T buy zones"
    far = max(
        (
            (x, y)
            for x in range(4, room.world.ssize - 4, 4)
            for y in range(4, room.world.ssize - 4, 4)
        ),
        key=lambda p: min((p[0] - zx) ** 2 + (p[1] - zy) ** 2 for zx, zy, _ in zones),
    )
    t.state.x, t.state.y = float(far[0]), float(far[1])
    assert not room.mode.in_buy_zone(t)
    assert room.private_view_for(t)["mode"]["canBuy"] is False
    buy(room, t, "assault")
    assert index_of("assault") not in t.owned
    # Dead, in the freeze, a player buys for the round they will spawn into.
    t.alive = False
    assert room.mode.can_buy(room, t)


def test_the_zone_is_a_circle_round_each_of_the_teams_spawns(game):
    room, *_ = game
    for team in (0, 1):
        spawns = [e for e in room.spawns if e.attr2 == team]
        assert len(room.mode.buy_zones[team]) == len(spawns)
        assert all(r == BUY_ZONE_RADIUS for _, _, r in room.mode.buy_zones[team])


def test_the_window_closes_when_the_bomb_goes_down(game):
    room, *_ = game
    from dataclasses import replace

    for _ in range(int(FREEZE_TIME / 0.05) + 2):
        room.simulate(0.05)
    mode = room.mode
    assert mode.buy_time_left() > 0
    mode.state = replace(mode.state, bomb=replace(mode.state.bomb, state="planted"))
    assert mode.buy_time_left() == 0


# ---- bots --------------------------------------------------------------------------


def test_bots_buy_in_the_freeze():
    """They read a `buy_open` flag no mode had, and so never bought anything."""
    from backend.modules.hassault import bots

    room = defuse_room()
    room.add("t", None, team=0)
    bot = room.add(
        "bot", None, brain=bots.BotBrain(skill=bots.SKILLS["normal"]), team=1
    )
    room.simulate(0.05)
    bot.money = 16000
    for _ in range(80):
        room.simulate(0.05)
        if bot.owned or bot.owned_extras:
            break
    assert bot.owned or bot.owned_extras


# ---- on the game server ----------------------------------------------------------


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, raw: str) -> None:
        self.sent.append(json.loads(raw))


def test_a_game_server_room_can_run_defuse(tmp_path, monkeypatch):
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    from backend.games_server.hassault_rooms import HassaultReferee, SeatConn
    from backend.games_server.models import SERVER_CAPS

    assert "hassault_modes" in SERVER_CAPS
    referee = HassaultReferee()

    async def go() -> None:
        a = SeatConn(FakeSocket(), "acct-a", "a")
        b = SeatConn(FakeSocket(), "acct-b", "b")
        welcome = await referee.join(a, "hd_dust2", mode="defuse")
        assert welcome["mode"]["id"] == "defuse"
        assert welcome["mode"]["catalog"]
        # Another join for the same map and mode lands in the same room; a
        # deathmatch join does not.
        again = await referee.join(b, "hd_dust2", mode="defuse")
        assert again["room"] == welcome["room"]
        dm = await referee.join(SeatConn(FakeSocket(), "acct-c", "c"), "hd_dust2")
        assert dm["room"] != welcome["room"] and dm["mode"]["id"] != "defuse"
        with pytest.raises(ValueError):
            await referee.join(
                SeatConn(FakeSocket(), "acct-d", "d"), "hd_dust2", mode="nope"
            )

    asyncio.run(go())


def test_the_node_relays_a_mode_with_an_online_join():
    """The node's online path names the mode; an older server ignores the key."""
    import inspect

    from backend.modules.hassault import channel, ranked

    assert "mode" in inspect.signature(ranked.join).parameters
    assert "mode=mode" in inspect.getsource(channel)


def test_the_committed_economy_fixture_is_current():
    from scripts.gen_hassault_economy_vectors import OUT, payload

    with open(OUT, encoding="utf-8") as fh:
        committed = json.load(fh)
    assert committed == json.loads(json.dumps(payload())), (
        "economy-vectors.json is stale: "
        "PYTHONPATH=. uv run python scripts/gen_hassault_economy_vectors.py"
    )

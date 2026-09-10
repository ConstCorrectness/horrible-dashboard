"""Dropping and selling back in defuse: the two ways a purchase leaves your hands.

Split from `test_hassault_economy.py` because both of these are about money
*moving between players* rather than being earned, and that is exactly where an
economy leaks: every rule below is one whose absence turns one purchase into two
players' worth of refunds, or a spent item into a free one.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from backend.modules.hassault import assets, modes, physics, pickups, weapons
from backend.modules.hassault.match import Command, MatchRoom, parse_command
from backend.modules.hassault.modes import objectives
from backend.modules.hassault.modes.defuse import (
    CATALOG,
    DROP_REPICK_DELAY,
    FREEZE_TIME,
    LIVE,
)

BY_ID = {item.id: index for index, item in enumerate(CATALOG)}


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
    """Two attackers and a defender, all added before the first round opens —
    a joiner after that waits out the round dead, see `Defuse.on_join`."""
    room = defuse_room()
    att = room.add("att", None, team=0)
    mate = room.add("mate", None, team=0)
    dfn = room.add("def", None, team=1)
    room.simulate(0.05)  # warmup -> round 1, freeze
    for p in (att, mate, dfn):
        p.money = 10000
    return room, att, mate, dfn, room.mode


class Sender:
    """Sends commands as the client does: fields on a movement command."""

    def __init__(self, room: MatchRoom) -> None:
        self.room = room
        self.seq = 1

    def send(self, player, **fields) -> None:
        self.room.enqueue(
            player,
            Command(
                seq=self.seq,
                forward=0.0,
                strafe=0.0,
                jump=False,
                yaw=0.0,
                pitch=0.0,
                dt=0.016,
                **fields,
            ),
        )
        self.seq += 1
        self.room.simulate(0.05)

    def buy(self, player, what: str) -> None:
        self.send(player, buy=BY_ID[what])

    def sell(self, player, what: str) -> None:
        self.send(player, sell=BY_ID[what])

    def drop(self, player) -> None:
        self.send(player, drop=True)


def stand_on(player, x: float, y: float, z: float) -> None:
    player.state.x, player.state.y, player.state.z = x, y, z


def go_live(room: MatchRoom) -> None:
    for _ in range(int(FREEZE_TIME / 0.05) + 2):
        room.simulate(0.05)


def not_carrier(mode, *players):
    return next(p for p in players if p.id != mode.state.bomb.carrier)


# ---------------------------------------------------------------------------
# The wire
# ---------------------------------------------------------------------------


def test_drop_and_sell_parse_and_default_to_doing_nothing():
    """`-1` and not `0` for the sell, the `buy` shape: a default of zero names
    the first catalogue row, which would make every command sell a rifle."""
    bare = parse_command({"seq": 1})
    assert bare is not None
    assert bare.drop is False
    assert bare.sell == -1

    full = parse_command({"seq": 2, "drop": True, "sell": 3})
    assert full is not None
    assert full.drop is True
    assert full.sell == 3

    nonsense = parse_command({"seq": 3, "sell": "rifle"})
    assert nonsense is not None and nonsense.sell == -1


# ---------------------------------------------------------------------------
# Selling
# ---------------------------------------------------------------------------


def test_selling_in_the_freeze_refunds_everything_it_cost(game):
    room, att, *_ = game
    s = Sender(room)
    s.buy(att, "assault")
    s.sell(att, "assault")
    assert att.money == 10000
    assert 2 not in att.owned
    assert att.ammo[2] == 0 and att.reserve[2] == 0
    assert att.weapon == 1, "sold the rifle and was left holding an empty one"


def test_selling_is_refused_once_the_round_is_live(game):
    room, att, *_ = game
    s = Sender(room)
    s.buy(att, "assault")
    go_live(room)
    assert room.mode.state.phase == LIVE
    s.sell(att, "assault")
    assert 2 in att.owned
    assert att.money == 10000 - CATALOG[BY_ID["assault"]].price


def test_you_cannot_sell_what_you_never_bought(game):
    room, att, *_ = game
    Sender(room).sell(att, "armour")
    assert att.money == 10000


def test_a_thrown_grenade_cannot_be_sold_back(game):
    """Refunding spent utility would make the freeze a window where it is free."""
    room, att, *_ = game
    s = Sender(room)
    s.buy(att, "flash")
    att.nades.counts[1] = 0  # thrown
    s.sell(att, "flash")
    assert att.money == 10000 - CATALOG[BY_ID["flash"]].price


def test_armour_that_has_absorbed_something_cannot_be_sold(game):
    room, att, *_ = game
    s = Sender(room)
    s.buy(att, "armour")
    att.armour = weapons.MAX_ARMOUR - 10
    s.sell(att, "armour")
    assert "armour" in att.owned_extras


def test_sold_armour_and_kits_are_taken_off(game):
    room, _att, _mate, dfn, _mode = game
    s = Sender(room)
    s.buy(dfn, "armour")
    s.buy(dfn, "defuser")
    s.sell(dfn, "armour")
    s.sell(dfn, "defuser")
    assert dfn.armour == 0.0
    assert not dfn.owned_extras
    assert dfn.money == 10000


def test_buy_sell_buy_is_charged_once(game):
    """`purchased` must lose the row on a sale, or the second buy is refused as
    already owned — a menu that sold you something and will not sell it again."""
    room, att, *_ = game
    s = Sender(room)
    s.buy(att, "shotgun")
    s.sell(att, "shotgun")
    s.buy(att, "shotgun")
    assert 3 in att.owned
    assert att.money == 10000 - CATALOG[BY_ID["shotgun"]].price


def test_the_envelope_names_what_can_be_sold_only_in_the_freeze(game):
    room, att, *_ = game
    mode = room.mode
    s = Sender(room)
    s.buy(att, "assault")
    s.buy(att, "smoke")
    assert mode.private_state(room, att)["sellable"] == sorted(
        [BY_ID["assault"], BY_ID["smoke"]]
    )
    go_live(room)
    assert mode.private_state(room, att)["sellable"] == []


def test_a_new_round_forgets_what_was_purchased(game):
    room, att, *_ = game
    Sender(room).buy(att, "assault")
    room.mode._reset_round(room)
    assert not att.purchased


# ---------------------------------------------------------------------------
# Dropping
# ---------------------------------------------------------------------------


def test_the_knife_and_pistol_cannot_be_dropped(game):
    room, att, mate, _dfn, mode = game
    player = not_carrier(mode, att, mate)
    player.weapon = 1
    Sender(room).drop(player)
    assert mode.dropped_weapons == []


def test_a_dead_player_drops_nothing(game):
    room, att, mate, _dfn, mode = game
    player = not_carrier(mode, att, mate)
    s = Sender(room)
    s.buy(player, "assault")
    player.alive = False
    s.drop(player)
    assert mode.dropped_weapons == []
    assert 2 in player.owned


def test_dropping_a_gun_puts_it_on_the_floor_with_its_ammunition(game):
    room, att, mate, _dfn, mode = game
    player = not_carrier(mode, att, mate)
    s = Sender(room)
    s.buy(player, "assault")
    player.ammo[2] = 7
    s.drop(player)
    assert 2 not in player.owned
    assert player.weapon == 1
    [drop] = mode.dropped_weapons
    assert (drop.slot, drop.ammo) == (2, 7)
    shared = mode.shared_state(room)["drops"]
    assert shared == [drop.to_dict()]
    assert "ammo" not in shared[0], "a gun's magazine is not public"


def test_your_own_drop_does_not_jump_straight_back_into_your_hands(game):
    """It lands at your feet. Without the delay the next tick picks it up again
    and the key appears to do nothing."""
    room, att, mate, _dfn, mode = game
    player = not_carrier(mode, att, mate)
    s = Sender(room)
    s.buy(player, "assault")
    s.drop(player)
    room.simulate(0.05)
    assert len(mode.dropped_weapons) == 1
    assert 2 not in player.owned

    mode.dropped_weapons[0] = replace(
        mode.dropped_weapons[0],
        dropped_at=mode.dropped_weapons[0].dropped_at - DROP_REPICK_DELAY - 0.1,
    )
    room.simulate(0.05)
    assert mode.dropped_weapons == []
    assert 2 in player.owned


def test_a_teammate_picks_up_the_gun_and_its_ammunition(game):
    room, att, mate, _dfn, mode = game
    giver = not_carrier(mode, att, mate)
    taker = mate if giver is att else att
    s = Sender(room)
    s.buy(giver, "sniper")
    giver.ammo[4] = 3
    s.drop(giver)
    drop = mode.dropped_weapons[0]
    stand_on(taker, drop.x, drop.y, drop.z)
    taker.weapon = 1
    room.simulate(0.05)
    assert 4 in taker.owned
    assert taker.ammo[4] == 3
    assert taker.weapon == 4, "picked up a sniper while holding a pistol"


def test_a_pickup_does_not_swap_you_off_a_gun_you_bought(game):
    room, att, mate, _dfn, mode = game
    giver = not_carrier(mode, att, mate)
    taker = mate if giver is att else att
    s = Sender(room)
    s.buy(giver, "shotgun")
    s.drop(giver)
    s.buy(taker, "assault")
    drop = mode.dropped_weapons[0]
    stand_on(taker, drop.x, drop.y, drop.z)
    room.simulate(0.05)
    assert 3 in taker.owned
    assert taker.weapon == 2


def test_a_dropped_gun_cannot_be_sold_by_either_player(game):
    """The leak this whole file exists for: buy, drop for a teammate, and have
    both of you sell it back. Neither refund may happen."""
    room, att, mate, _dfn, mode = game
    giver = not_carrier(mode, att, mate)
    taker = mate if giver is att else att
    price = CATALOG[BY_ID["assault"]].price
    s = Sender(room)
    s.buy(giver, "assault")
    s.drop(giver)
    drop = mode.dropped_weapons[0]
    stand_on(taker, drop.x, drop.y, drop.z)
    room.simulate(0.05)
    assert 2 in taker.owned

    s.sell(giver, "assault")
    s.sell(taker, "assault")
    assert giver.money == 10000 - price
    assert taker.money == 10000
    assert mode.private_state(room, taker)["sellable"] == []


def test_a_round_reset_clears_the_floor(game):
    room, att, mate, _dfn, mode = game
    player = not_carrier(mode, att, mate)
    s = Sender(room)
    s.buy(player, "assault")
    s.drop(player)
    mode._reset_round(room)
    assert mode.dropped_weapons == []


# ---------------------------------------------------------------------------
# The bomb
# ---------------------------------------------------------------------------


def carrier_of(room, mode):
    return room.players[mode.state.bomb.carrier]


def test_the_carrier_drops_the_bomb_before_the_gun(game):
    room, _att, _mate, _dfn, mode = game
    carrier = carrier_of(room, mode)
    s = Sender(room)
    s.buy(carrier, "assault")
    s.drop(carrier)
    assert mode.state.bomb.state == "dropped"
    assert 2 in carrier.owned, "dropped the rifle along with the bomb"
    assert mode.private_state(room, carrier)["carrying"] is False
    shared = mode.shared_state(room)["bomb"]
    assert shared["state"] == "dropped"
    assert {"x", "y", "z"} <= shared.keys()
    assert any(fx.get("kind") == "bomb_drop" for fx in room.fx)


def test_a_defender_walks_over_a_dropped_bomb_and_leaves_it(game):
    room, _att, _mate, dfn, mode = game
    Sender(room).drop(carrier_of(room, mode))
    bomb = mode.state.bomb
    stand_on(dfn, bomb.x, bomb.y, bomb.z)
    room.simulate(0.05)
    assert mode.state.bomb.state == "dropped"


def test_a_teammate_picks_up_a_dropped_bomb_and_can_plant_it(game):
    room, att, mate, _dfn, mode = game
    carrier = carrier_of(room, mode)
    other = mate if carrier is att else att
    Sender(room).drop(carrier)
    bomb = mode.state.bomb
    stand_on(other, bomb.x, bomb.y, bomb.z)
    room.simulate(0.05)
    assert mode.state.bomb.state == "carried"
    assert mode.state.bomb.carrier == other.id


def test_a_carrier_who_dies_still_passes_the_bomb_rather_than_dropping_it(game):
    """Death is not a drop — see `Bomb`: a bomb lying where somebody fell is a
    hunt. Only the key puts it on the floor."""
    room, att, mate, _dfn, mode = game
    carrier = carrier_of(room, mode)
    other = mate if carrier is att else att
    carrier.alive = False
    mode.on_death(room, carrier)
    assert mode.state.bomb.state == "carried"
    assert mode.state.bomb.carrier == other.id


def test_attacker_bots_go_and_fetch_a_dropped_bomb(game):
    room, att, mate, _dfn, mode = game
    carrier = carrier_of(room, mode)
    other = mate if carrier is att else att
    Sender(room).drop(carrier)
    goal = mode.bot_goal(room, other)
    assert goal is not None
    assert (goal.x, goal.y) == (mode.state.bomb.x, mode.state.bomb.y)

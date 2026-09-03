"""The defuse economy: what a round pays, and what a buy is allowed to do.

Split from `test_hassault_defuse.py` because it is a different subject — that
file is about when a round ends, this one is about money — and because the buy
rules are the half most likely to be loosened by accident. Every check in `_buy`
is one a client cannot make and one a menu will happily offer past.
"""

from __future__ import annotations

import time

import pytest

from backend.modules.hassault import assets, modes, physics, pickups, weapons
from backend.modules.hassault.match import Command, MatchRoom
from backend.modules.hassault.modes import objectives
from backend.modules.hassault.modes.defuse import (
    CATALOG,
    Emit,
    DEFUSE_REWARD,
    FREEZE_TIME,
    KILL_REWARD,
    LIVE,
    LOSS_REWARD,
    LOSS_STREAK_BONUS,
    MAX_LOSS_STREAK,
    MAX_MONEY,
    PLANT_REWARD,
    START_MONEY,
    TEAMKILL_PENALTY,
    WIN_REWARD,
)

#: Catalogue indices, by id, so a case reads as what it buys.
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
    room = defuse_room()
    att = room.add("att", None, team=0)
    dfn = room.add("def", None, team=1)
    room.simulate(0.05)  # warmup -> round 1, freeze
    return room, att, dfn, room.mode


class Buyer:
    """Sends buys as the client does: a field on a movement command."""

    def __init__(self, room: MatchRoom) -> None:
        self.room = room
        self.seq = 1

    def buy(self, player, what: str | int) -> None:
        index = BY_ID[what] if isinstance(what, str) else what
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
                buy=index,
            ),
        )
        self.seq += 1
        self.room.simulate(0.05)


# ---------------------------------------------------------------------------
# The catalogue
# ---------------------------------------------------------------------------


def test_the_catalogue_is_served_so_no_client_carries_a_price(game):
    """A second copy of a price is a menu that disagrees with the server about
    what you can afford, and it fails as a purchase the menu offered and the
    server refused — money still there, nothing saying why."""
    room, _att, _dfn, _mode = game
    config = room.state_payload()["mode"]["config"]
    assert config["startMoney"] == START_MONEY
    assert config["killReward"] == KILL_REWARD
    assert config["winReward"] == WIN_REWARD

    catalog = room.state_payload()["mode"]["catalog"]
    assert len(catalog) == len(CATALOG)
    for served, item in zip(catalog, CATALOG, strict=True):
        assert served["id"] == item.id
        assert served["price"] == item.price
        assert served["kind"] in ("weapon", "armour", "nade")


def test_the_knife_and_pistol_are_not_in_the_catalogue():
    """You always have them, and a row for something you cannot not own is a row
    that never does anything."""
    slots = {item.slot for item in CATALOG if item.kind == "weapon"}
    assert 0 not in slots
    assert 1 not in slots


# ---------------------------------------------------------------------------
# Buying
# ---------------------------------------------------------------------------


def test_you_start_a_half_with_enough_for_a_choice_not_a_loadout(game):
    """Enough for a rifle *or* armour and a grenade, not both. The first round
    being a real decision is most of what an economy is for."""
    _room, att, dfn, _mode = game
    assert att.money == START_MONEY
    assert dfn.money == START_MONEY
    rifle = next(i for i in CATALOG if i.id == "assault")
    assert rifle.price > START_MONEY


def test_a_spawn_without_a_purchase_is_a_knife_and_a_pistol(game):
    """`reset_loadout` grants every weapon with full magazines. If `outfit` does
    not run immediately after it, the grant wins and the buy menu appears to do
    nothing at all: you pay, you spawn, and you are holding the full loadout."""
    _room, att, _dfn, _mode = game
    assert att.ammo[1] > 0, "the pistol is not free"
    assert att.reserve[1] < 0, "the pistol is not bottomless"
    for slot in (2, 3, 4):
        assert att.ammo[slot] == 0, f"weapon {slot} was granted unbought"
        assert att.reserve[slot] == 0
    assert all(n == 0 for n in att.nades.counts.values()), "grenades were granted"
    assert att.armour == 0.0
    assert att.weapon in (0, 1), "spawned holding a weapon with no ammunition"


def test_buying_a_weapon_hands_it_over_now_rather_than_next_round(game):
    """The buy happens during the freeze of the round it is for; a rifle that
    only arrived next round would be a menu that lies about what it sold."""
    room, att, _dfn, _mode = game
    att.money = 5000
    Buyer(room).buy(att, "assault")
    spec = weapons.weapon_at(2)
    assert 2 in att.owned
    assert att.ammo[2] == spec.mag
    assert att.reserve[2] == spec.reserve
    assert att.weapon == 2, "bought a rifle and was left holding something else"
    assert att.money == 5000 - CATALOG[BY_ID["assault"]].price


def test_a_grenade_bought_is_exactly_one_grenade(game):
    room, att, _dfn, _mode = game
    att.money = 5000
    Buyer(room).buy(att, "flash")
    assert att.nades.counts[1] == 1
    assert att.nades.counts[0] == 0, "buying one grenade granted another"


def test_armour_bought_is_armour_worn(game):
    room, att, _dfn, _mode = game
    att.money = 5000
    Buyer(room).buy(att, "armour")
    assert att.armour == weapons.MAX_ARMOUR


def test_you_cannot_buy_what_you_cannot_afford(game):
    room, att, _dfn, _mode = game
    assert att.money == START_MONEY
    Buyer(room).buy(att, "sniper")
    assert att.money == START_MONEY, "money left with nothing bought"
    assert 4 not in att.owned


def test_a_purchase_that_would_give_nothing_spends_nothing(game):
    """The `pickups.apply` shape, and the same argument: taking armour at full
    armour should not quietly cost you the round's money."""
    room, att, _dfn, _mode = game
    att.money = 5000
    buyer = Buyer(room)
    buyer.buy(att, "armour")
    after = att.money
    buyer.buy(att, "armour")
    assert att.money == after, "the second armour was charged for"

    buyer.buy(att, "flash")
    after = att.money
    buyer.buy(att, "flash")
    assert att.money == after, "the second flash was charged for"


def test_buying_is_refused_once_the_round_is_live(game):
    """The freeze is the buy window; a rifle mid-round would make the economy a
    tax rather than a decision."""
    room, att, _dfn, mode = game
    att.money = 5000
    for _ in range(int(FREEZE_TIME / 0.05) + 2):
        room.simulate(0.05)
    assert mode.state.phase == LIVE
    Buyer(room).buy(att, "assault")
    assert att.money == 5000
    assert 2 not in att.owned


def test_a_dead_player_may_still_buy(game):
    """They are buying for the *next* round, and the freeze is exactly when that
    is decided — so `alive` is deliberately not one of the checks."""
    room, att, _dfn, _mode = game
    att.money = 5000
    att.alive = False
    Buyer(room).buy(att, "armour")
    assert "armour" in att.owned_extras


def test_a_nonsense_index_buys_nothing_rather_than_picking_something(game):
    room, att, _dfn, _mode = game
    att.money = 5000
    buyer = Buyer(room)
    buyer.buy(att, len(CATALOG) + 5)
    assert att.money == 5000
    assert not att.owned and not att.owned_nades and not att.owned_extras


def test_a_command_with_no_buy_buys_nothing(game):
    """`-1`, not `0`: a default of zero names the first catalogue entry, which
    would make every movement command a request to buy a rifle."""
    room, att, _dfn, _mode = game
    att.money = 5000
    room.enqueue(
        att,
        Command(
            seq=1, forward=0.0, strafe=0.0, jump=False, yaw=0.0, pitch=0.0, dt=0.016
        ),
    )
    room.simulate(0.05)
    assert att.money == 5000
    assert not att.owned


# ---------------------------------------------------------------------------
# What a round pays
# ---------------------------------------------------------------------------


def go_live(room: MatchRoom) -> None:
    """Out of the freeze, where damage is off — see `damage_scale`."""
    for _ in range(int(FREEZE_TIME / 0.05) + 2):
        room.simulate(0.05)


def test_a_kill_pays_the_killer(game):
    room, att, dfn, _mode = game
    go_live(room)
    dfn.protected_until = 0.0
    before = att.money
    room._apply_damage(dfn, att, 500.0, False, weapons.weapon_at(0), time.monotonic())
    assert att.money == before + KILL_REWARD


def test_a_team_kill_charges_rather_than_pays(game):
    """Friendly fire here is partial, so a teammate in a doorway is already a
    cost; this is what stops shooting through them being free."""
    room, att, _dfn, _mode = game
    go_live(room)
    mate = room.add("mate", None, team=att.team)
    room.respawn(mate)
    mate.protected_until = 0.0
    before = att.money
    room._apply_damage(mate, att, 500.0, False, weapons.weapon_at(0), time.monotonic())
    assert att.money == before - TEAMKILL_PENALTY


def test_a_teamkill_penalty_cannot_put_you_in_debt(game):
    """A debt would follow somebody into a round they had nothing to do with."""
    room, att, _dfn, _mode = game
    go_live(room)
    mate = room.add("mate", None, team=att.team)
    room.respawn(mate)
    mate.protected_until = 0.0
    att.money = 100
    room._apply_damage(mate, att, 500.0, False, weapons.weapon_at(0), time.monotonic())
    assert att.money == 0


def test_dying_costs_nothing(game):
    room, att, dfn, _mode = game
    go_live(room)
    dfn.protected_until = 0.0
    before = dfn.money
    room._apply_damage(dfn, att, 500.0, False, weapons.weapon_at(0), time.monotonic())
    assert dfn.money == before


def test_winning_and_losing_a_round_both_pay(game):
    """A loss pays, or a side that loses once buys nothing, loses again, and the
    scoreline stops being about play."""
    room, att, dfn, mode = game
    att.money = dfn.money = 0
    winner = mode.state.attackers
    mode._perform(room, Emit("round_end", team=winner))
    won, lost = (att, dfn) if att.team == winner else (dfn, att)
    assert won.money == WIN_REWARD
    assert lost.money == LOSS_REWARD


def test_a_losing_side_is_paid_more_the_longer_it_loses(game):
    room, att, dfn, mode = game
    winner = mode.state.attackers
    loser_team = 1 - winner
    loser = att if att.team == loser_team else dfn
    paid = []
    for _ in range(MAX_LOSS_STREAK + 2):
        loser.money = 0
        mode._perform(room, Emit("round_end", team=winner))
        paid.append(loser.money)
    assert paid[0] == LOSS_REWARD
    assert paid[1] == LOSS_REWARD + LOSS_STREAK_BONUS
    # And it stops climbing, or a side that never wins ends the match richer than
    # the one beating them.
    assert paid[-1] == LOSS_REWARD + LOSS_STREAK_BONUS * MAX_LOSS_STREAK
    assert paid[-1] == paid[-2]


def test_a_win_clears_the_loss_streak(game):
    room, att, dfn, mode = game
    a_team = att.team
    mode._perform(room, Emit("round_end", team=1 - a_team))  # att loses
    mode._perform(room, Emit("round_end", team=a_team))  # att wins
    att.money = 0
    mode._perform(room, Emit("round_end", team=1 - a_team))  # att loses again
    assert att.money == LOSS_REWARD, "the streak survived a win"


def test_planting_and_defusing_pay_even_on_a_losing_round(game):
    """Planting and then being wiped is still the play that nearly worked, and an
    economy that only pays the winner punishes the side already losing."""
    room, att, dfn, mode = game
    for _ in range(int(FREEZE_TIME / 0.05) + 2):
        room.simulate(0.05)
    site = mode.sites[0]
    where = (site.x, site.y, site.z)
    att.state.x, att.state.y, att.state.z = where
    before = att.money
    seq = 1
    for _ in range(400):
        room.enqueue(
            att,
            Command(
                seq=seq,
                forward=0.0,
                strafe=0.0,
                jump=False,
                yaw=0.0,
                pitch=0.0,
                dt=0.016,
                use=True,
            ),
        )
        seq += 1
        room.simulate(0.05)
        att.state.x, att.state.y, att.state.z = where
        if mode.state.bomb.state == "planted":
            break
    assert mode.state.bomb.state == "planted"
    assert att.money == before + PLANT_REWARD
    assert DEFUSE_REWARD > 0


def test_money_is_capped(game):
    room, att, _dfn, mode = game
    att.money = MAX_MONEY
    mode._perform(room, Emit("round_end", team=att.team))
    assert att.money == MAX_MONEY


# ---------------------------------------------------------------------------
# What survives a round, and what does not
# ---------------------------------------------------------------------------


def test_a_new_round_takes_back_what_was_bought_but_not_the_money(game):
    """The kit is the round; the money is the match."""
    room, att, _dfn, mode = game
    att.money = 5000
    Buyer(room).buy(att, "assault")
    assert 2 in att.owned
    spent = att.money

    mode._reset_round(room)
    assert not att.owned, "a bought weapon survived the round"
    assert att.ammo[2] == 0
    assert att.money == spent, "the round reset took the money too"


def test_what_you_bought_survives_dying_within_the_round(game):
    """Only a new round takes it away — otherwise the first trade of a round
    would cost you the rifle you paid for."""
    room, att, dfn, _mode = game
    att.money = 5000
    Buyer(room).buy(att, "assault")
    att.protected_until = 0.0
    room._apply_damage(att, dfn, 500.0, False, weapons.weapon_at(0), time.monotonic())
    assert 2 in att.owned
    room.respawn(att)
    assert att.ammo[2] > 0, "the respawn did not re-grant what was bought"


def test_half_time_levels_the_purses(game):
    """Or the first half's economy decides a match whose sides have just been
    swapped."""
    room, att, dfn, mode = game
    att.money, dfn.money = 12000, 200
    mode._perform(room, Emit("half"))
    assert att.money == START_MONEY
    assert dfn.money == START_MONEY


def test_a_joiner_buys_in_at_the_starting_purse(game):
    """Arriving in round nine with nothing is a player who cannot participate;
    arriving with the room's average is a reward for having missed it."""
    room, _att, _dfn, _mode = game
    late = room.add("late", None, team=1)
    assert late.money == START_MONEY


# ---------------------------------------------------------------------------
# The wire
# ---------------------------------------------------------------------------


def test_money_rides_in_your_own_envelope_and_not_the_shared_one(game):
    """The field that makes the split matter. In `shared_state` it would be every
    player's purse, world-readable, with nothing raising, warning or breaking the
    snapshot template to say so."""
    room, att, dfn, _mode = game
    att.money = 4300
    dfn.money = 900
    assert room.private_view_for(att)["mode"]["money"] == 4300
    assert room.private_view_for(dfn)["mode"]["money"] == 900
    shared = room.shared_view()["mode"]
    assert "money" not in shared
    assert "bought" not in shared


def test_the_envelope_says_whether_the_window_is_open(game):
    room, att, _dfn, _mode = game
    assert room.private_view_for(att)["mode"]["canBuy"] is True
    for _ in range(int(FREEZE_TIME / 0.05) + 2):
        room.simulate(0.05)
    assert room.private_view_for(att)["mode"]["canBuy"] is False


def test_what_you_own_is_reported_as_catalogue_indices(game):
    """So a menu can grey out what is already bought without keeping its own idea
    of what that means."""
    room, att, _dfn, _mode = game
    att.money = 9000
    buyer = Buyer(room)
    buyer.buy(att, "assault")
    buyer.buy(att, "flash")
    bought = room.private_view_for(att)["mode"]["bought"]
    assert BY_ID["assault"] in bought
    assert BY_ID["flash"] in bought
    assert BY_ID["sniper"] not in bought

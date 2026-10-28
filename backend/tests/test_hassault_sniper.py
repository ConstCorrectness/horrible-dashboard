"""The sniper rifle stays in the game.

It went missing once without a single test failing. P3 (per-player loadouts)
made a switch to anything not held a refusal, which was right, but left the
only loadout picker in the desktop main menu, and gave bots archetypes counted
*per `add_bots` call* — so a bot added one at a time was always the SMG entry
fragger. A browser player could no longer hold the sniper, and rarely met one.

Each test below is one road by which the sniper reaches a player's hands. If one
is closed on purpose, change the test in the same commit and say so.
"""

from __future__ import annotations

from backend.modules.hassault import bots, modes, weapons
from backend.modules.hassault.match import MatchRoom
from backend.modules.hassault.modes import defuse
from backend.modules.hassault.physics import flat_world

SNIPER = weapons.WEAPON_BY_ID["sniper"]
SNIPER_INDEX = weapons.WEAPONS.index(SNIPER)


class Spawn:
    def __init__(self, x: float, y: float, team: int = 0) -> None:
        self.x = x
        self.y = y
        self.z = 0.0
        self.yaw = 0.0
        self.attr2 = team


def make_room(mode: str = "dm") -> MatchRoom:
    return MatchRoom(
        "snipe",
        "testmap",
        flat_world(48),
        [Spawn(8, 8, team=0), Spawn(30, 30, team=1), Spawn(8, 30, team=0)],
        mode=modes.build(mode),
        record=False,
    )


def test_the_sniper_is_a_primary_either_side_may_carry():
    # Index 4 is on the wire (the first five keep their meaning), and side "any"
    # is what lets both teams pick it in a loadout and buy it in defuse.
    assert SNIPER_INDEX == 4
    assert SNIPER.slot == "primary"
    assert SNIPER.side == "any"
    assert SNIPER.price > 0
    assert SNIPER.zoom_levels
    assert "sniper" in weapons.weapon_slots()
    assert weapons.resolve_slot("sniper") == SNIPER_INDEX
    assert weapons.resolve_slot("Sniper Rifle") == SNIPER_INDEX


def test_a_loadout_naming_the_sniper_is_honoured_on_both_sides():
    chosen = weapons.loadouts_for({"primary": "sniper", "secondary": "pistol"})
    assert chosen["t"]["primary"] == SNIPER_INDEX
    assert chosen["ct"]["primary"] == SNIPER_INDEX


def test_a_player_who_picks_the_sniper_spawns_holding_it():
    room = make_room("dm")
    player = room.add("shooter", None, team=0)
    room.set_loadout(player, {"primary": "sniper"})
    room.respawn(player)
    assert SNIPER_INDEX in player.held
    assert player.weapon == SNIPER_INDEX


def test_the_buy_menu_sells_the_sniper():
    rows = [item for item in defuse.CATALOG if item.id == "sniper"]
    assert len(rows) == 1
    assert rows[0].category == "primary"
    assert rows[0].price == SNIPER.price
    assert rows[0].side == "any"


def test_the_awp_arena_hands_everyone_the_sniper():
    room = make_room("awp")
    player = room.add("shooter", None, team=0)
    room.respawn(player)
    assert player.weapon == SNIPER_INDEX


def test_bots_added_one_at_a_time_still_field_a_sniper():
    # The browser's "+1 bot" and `add_bot` with no count both add one per call.
    # Counted per call, every one of those was archetype 0.
    room = make_room("dm")
    added = [bots.add_bots(room, 1)[0] for _ in bots.BOT_ARCHETYPES]
    assert {b.brain.archetype for b in added} == set(bots.BOT_ARCHETYPES)
    snipers = [b for b in added if b.brain.archetype == "sniper"]
    assert snipers and SNIPER_INDEX in snipers[0].held


def test_a_batch_of_bots_fields_a_sniper():
    room = make_room("dm")
    added = bots.add_bots(room, len(bots.BOT_ARCHETYPES))
    assert any(SNIPER_INDEX in b.held for b in added)

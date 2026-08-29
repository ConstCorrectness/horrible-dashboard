"""Items on the map: what taking one gives, what it refuses to give, and who is told.

Hermetic in the same way the match tests are — a flat world and items placed by
hand — because the map half is covered by `test_hassault_bundled.py` and what is
interesting here is the rules, not the geometry.
"""

from __future__ import annotations

import time

import pytest

from backend.modules.hassault import grenades, pickups, weapons
from backend.modules.hassault.match import Command, MatchRoom
from backend.modules.hassault.physics import flat_world


class Ent:
    """The three fields `pickups.place` reads off a map entity."""

    def __init__(self, type: int, x: int, y: int) -> None:
        self.type = type
        self.x = x
        self.y = y


class Spawn:
    def __init__(self, x: float, y: float, team: int = 0) -> None:
        self.x = x
        self.y = y
        self.z = 0.0
        self.yaw = 0.0
        self.attr2 = team


def make_room(items: list) -> MatchRoom:
    world = flat_world(32, floor=0, ceil=16)
    placed = pickups.place(world, items)
    return MatchRoom("r1", "testmap", world, [Spawn(8, 8)], placed)


def player_on(room: MatchRoom, item: pickups.Item):
    player = room.add("alice", None)
    player.state.x, player.state.y, player.state.z = item.x, item.y, item.z
    return player


# ---- placement --------------------------------------------------------------------


def test_only_the_item_entities_are_placed():
    """A map is mostly lights and spawns, and `akimbo` is an item we do not have.

    Placing an entity we cannot honour as something else would be a lie about
    what the map says, so it is dropped instead."""
    world = flat_world(32)
    placed = pickups.place(
        world,
        [
            Ent(pickups.HEALTH, 10, 10),
            Ent(1, 11, 11),  # light
            Ent(2, 12, 12),  # playerstart
            Ent(9, 13, 13),  # akimbo
            Ent(pickups.ARMOUR, 14, 14),
        ],
    )
    assert [item.kind for item in placed] == ["health", "armour"]
    assert [item.id for item in placed] == [0, 1]


def test_an_item_is_resolved_onto_the_floor_beneath_it():
    """An entity's `z` is the mapper's eye, exactly as a `playerstart`'s is. Read
    verbatim it would leave items hanging in the air, where nothing can run over
    them and nothing would say why."""
    world = flat_world(32, floor=6, ceil=20)
    (item,) = pickups.place(world, [Ent(pickups.AMMO, 10, 10)])
    assert item.z == 6
    assert (item.x, item.y) == (10.5, 10.5)


def test_an_item_buried_in_rock_is_dropped():
    world = flat_world(32)
    assert pickups.place(world, [Ent(pickups.HEALTH, 0, 0)]) == []


# ---- reach ------------------------------------------------------------------------


def test_reach_is_generous_horizontally_and_asymmetric_vertically():
    item = pickups.Item(0, "health", 10.0, 10.0, 0.0, pickups.ITEMS[pickups.HEALTH])
    assert pickups.in_reach(item, 10.0, 10.0, 0.0)
    assert pickups.in_reach(item, 11.5, 10.0, 0.0)
    assert not pickups.in_reach(item, 12.5, 10.0, 0.0)
    # Mid-jump over it still counts; standing a storey below it does not.
    assert pickups.in_reach(item, 10.0, 10.0, 3.0)
    assert not pickups.in_reach(item, 10.0, 10.0, -3.0)


# ---- what a pickup gives ----------------------------------------------------------


def test_health_tops_up_to_the_cap_and_reports_what_it_actually_gave():
    room = make_room([Ent(pickups.HEALTH, 10, 10)])
    item = room.items.items[0]
    player = player_on(room, item)
    player.health = 90

    (taken,) = room.items.collect(player)
    assert player.health == weapons.MAX_HEALTH
    # Ten, not twenty-five: the HUD would otherwise print a number the health
    # bar visibly disagrees with.
    assert taken.health == 10


def test_an_item_that_can_give_nothing_is_not_consumed():
    """The quiet unfairness this exists to prevent: running over the armour at
    full armour, gaining nothing, and taking it off the map for forty seconds."""
    room = make_room([Ent(pickups.HEALTH, 10, 10)])
    item = room.items.items[0]
    player = player_on(room, item)

    assert room.items.collect(player) == []
    assert item.available(time.time())


def test_ammo_fills_every_finite_reserve_and_skips_the_bottomless_one():
    room = make_room([Ent(pickups.AMMO, 10, 10)])
    item = room.items.items[0]
    player = player_on(room, item)
    for index, weapon in enumerate(weapons.WEAPONS):
        player.reserve[index] = 0

    (taken,) = room.items.collect(player)
    for index, weapon in enumerate(weapons.WEAPONS):
        if weapon.reserve < 0:
            # Unlimited: nothing to add, and counting it would report rounds the
            # player never received.
            assert player.reserve[index] == 0
        else:
            assert player.reserve[index] > 0
    assert taken.rounds > 0


def test_a_reserve_is_never_filled_past_where_it_started():
    room = make_room([Ent(pickups.AMMO, 10, 10)])
    item = room.items.items[0]
    player = player_on(room, item)
    player.reserve = {i: 1 for i in range(len(weapons.WEAPONS))}

    room.items.collect(player)
    for index, weapon in enumerate(weapons.WEAPONS):
        if weapon.reserve >= 0:
            assert player.reserve[index] <= weapon.reserve


def test_a_grenade_pickup_only_helps_someone_who_has_thrown_one():
    room = make_room([Ent(pickups.GRENADE, 10, 10)])
    item = room.items.items[0]
    player = player_on(room, item)

    assert room.items.collect(player) == []  # full pouch

    player.nades.take(0)
    (taken,) = room.items.collect(player)
    assert taken.nade == grenades.GRENADES[0].id
    assert player.nades.counts[0] == grenades.GRENADES[0].carried


def test_a_helmet_stops_short_of_where_the_vest_reaches():
    """Two armour items with the same cap would make the small one the large one
    for anybody who found it first."""
    helmet = pickups.ITEMS[pickups.HELMET]
    armour = pickups.ITEMS[pickups.ARMOUR]
    assert helmet.armour_cap < armour.armour_cap == weapons.MAX_ARMOUR


# ---- respawn ----------------------------------------------------------------------


def test_a_taken_item_comes_back_rather_than_disappearing():
    room = make_room([Ent(pickups.HEALTH, 10, 10)])
    item = room.items.items[0]
    player = player_on(room, item)
    player.health = 10

    now = time.time()
    assert room.items.collect(player, now)
    assert room.items.taken_ids(now) == [item.id]

    player.health = 10
    assert room.items.collect(player, now + item.spec.respawn - 0.1) == []
    # Back on the map a moment later, and reported as present again.
    assert room.items.taken_ids(now + item.spec.respawn) == []
    assert room.items.collect(player, now + item.spec.respawn)


# ---- who is told ------------------------------------------------------------------


def test_the_map_hole_is_public_and_the_contents_are_private():
    """An item vanishing is something everybody can see; what it gave you is not."""
    room = make_room([Ent(pickups.HEALTH, 10, 10)])
    item = room.items.items[0]
    taker = player_on(room, item)
    taker.health = 10
    other = room.add("bob", None)

    room._collect(taker)

    assert room.items.taken_ids() == [item.id]
    assert [fx["kind"] for fx in room.fx] == ["pickup"]
    assert room.shared_view()["itemsOut"] == [item.id]

    mine = taker.private_view(time.time())
    assert mine["picked"] == [{"item": item.id, "kind": "health", "health": 25}]
    assert other.private_view(time.time())["picked"] == []
    # And drained: a pickup is reported once, not every tick until you die.
    assert taker.private_view(time.time())["picked"] == []


def test_taking_something_is_audible():
    """Standing on the armour is not free map control."""
    room = make_room([Ent(pickups.HEALTH, 10, 10)])
    taker = player_on(room, room.items.items[0])
    taker.health = 10

    room._collect(taker)
    assert [n.kind for n in room.noises] == ["pickup"]


def test_armour_is_never_in_the_shared_rows():
    room = make_room([Ent(pickups.ARMOUR, 10, 10)])
    player = player_on(room, room.items.items[0])
    room._collect(player)

    assert player.armour > 0
    assert "armour" not in player.snapshot(time.time())
    assert player.private_view(time.time())["armour"] == round(player.armour)


# ---- armour in a fight ------------------------------------------------------------


def test_armour_absorbs_part_of_a_hit_and_spends_itself_doing_it():
    room = make_room([])
    victim = room.add("victim", None)
    attacker = room.add("attacker", None)
    victim.armour = 100.0
    weapon = weapons.WEAPONS[weapons.DEFAULT_WEAPON]

    room._apply_damage(victim, attacker, 40.0, False, weapon, time.time())

    absorbed = 40.0 * weapons.ARMOUR_ABSORB
    assert victim.armour == 100.0 - absorbed
    assert victim.health == weapons.MAX_HEALTH - (40.0 - absorbed)
    # The shooter is told a vest ate some of it, never how much is left.
    (hit,) = attacker.pending_hits
    assert hit["armour"] is True
    assert "remaining" not in hit


def test_damage_dealt_counts_health_lost_not_damage_rolled():
    """It is the one stat a player can check against the health bars they watched
    go down, so armour has to come off it."""
    room = make_room([])
    victim = room.add("victim", None)
    attacker = room.add("attacker", None)
    victim.armour = 100.0

    room._apply_damage(
        victim,
        attacker,
        40.0,
        False,
        weapons.WEAPONS[weapons.DEFAULT_WEAPON],
        time.time(),
    )
    assert attacker.damage_dealt == pytest.approx(40.0 * (1 - weapons.ARMOUR_ABSORB))


def test_armour_runs_out_rather_than_discounting_forever():
    room = make_room([])
    victim = room.add("victim", None)
    attacker = room.add("attacker", None)
    victim.armour = 5.0
    weapon = weapons.WEAPONS[weapons.DEFAULT_WEAPON]

    room._apply_damage(victim, attacker, 40.0, False, weapon, time.time())
    assert victim.armour == 0.0
    assert victim.health == weapons.MAX_HEALTH - 35.0


def test_spawning_carries_no_armour():
    """It is something the map gives you; starting with it would make the item
    everyone fights over a top-up."""
    room = make_room([])
    player = room.add("alice", None)
    player.armour = 80.0
    player.reset_loadout()
    assert player.armour == 0.0


# ---- the tick ---------------------------------------------------------------------


def test_running_over_an_item_takes_it():
    """The whole thing, through the simulation rather than through `collect`."""
    world = flat_world(32, floor=0, ceil=16)
    placed = pickups.place(world, [Ent(pickups.HEALTH, 12, 8)])
    room = MatchRoom("r1", "testmap", world, [Spawn(8, 8)], placed)
    player = room.add("alice", None)
    player.health = 10
    player.state.x, player.state.y = 8.0, 8.5
    player.state.z = 0.0

    for seq in range(1, 240):
        player.queue.append(
            Command(
                seq=seq,
                forward=1.0,
                strafe=0.0,
                jump=False,
                yaw=0.0,
                pitch=0.0,
                dt=1 / 60,
            )
        )
        room.simulate(1 / 60)
        if player.health > 10:
            break

    assert player.health == 35, "walked over a health pack and did not pick it up"
    assert room.items.taken_ids()


def test_a_corpse_does_not_collect():
    """A health pack under a lethal drop is not a rescue — and handing one to a
    dead player would take it off the map for everybody else too."""
    room = make_room([Ent(pickups.HEALTH, 10, 10)])
    item = room.items.items[0]
    player = player_on(room, item)
    player.health = 10
    player.alive = False

    room._movement_consequences(player, (item.x, item.y), False, time.time())
    assert room.items.taken_ids() == []

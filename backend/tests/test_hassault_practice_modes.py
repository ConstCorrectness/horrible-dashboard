"""P7's practice modes: the arenas, random guns, and retake.

Each is an existing mode with one rule changed, and each test names that rule:
an arena hands everybody the same guns and respawns them at once, random guns
deals from the room's RNG (so the same room deals the same guns), and a retake
round goes live with the bomb already planted and no economy.
"""

from __future__ import annotations

from backend.modules.hassault import modes, weapons
from backend.modules.hassault.match import MatchServer
from backend.modules.hassault.modes import arena, retake
from backend.modules.hassault.modes.defuse import FREEZE_TIME, LIVE


def index_of(weapon_id: str) -> int:
    return weapons.WEAPONS.index(weapons.WEAPON_BY_ID[weapon_id])


def room_for(mode: str, map_name: str = "hd_pit"):
    return MatchServer(record_replays=False).create(map_name, mode=mode)


def test_the_awp_arena_carries_the_sniper_whatever_was_picked():
    room = room_for("awp")
    p = room.add(
        "p", None, team=0, loadouts=weapons.loadouts_for({"primary": "shotgun"})
    )
    assert p.weapon == index_of("sniper")
    held = room.private_view_for(p)["held"]
    assert sorted(held) == sorted([index_of("sniper"), index_of("knife")])
    assert room.mode.respawn_delay(room, p) == arena.ARENA_RESPAWN


def test_the_aim_arena_is_a_rifle_and_a_pistol():
    room = room_for("aim")
    p = room.add("p", None, team=0)
    slots = room.private_view_for(p)["slots"]
    assert slots["primary"] == index_of("assault")
    assert slots["secondary"] == index_of("pistol")


def test_random_guns_deal_the_same_guns_from_the_same_seed():
    def deal() -> list[int]:
        room = room_for("dm_random")
        room.rng.seed(7)
        p = room.add("p", None, team=0)
        out = []
        for _ in range(6):
            room.respawn(p)
            out.append(p.weapon)
        return out

    first = deal()
    assert first == deal()
    assert all(weapons.WEAPONS[i].slot == "primary" for i in first)
    assert len(set(first)) > 1, "it is random, not one gun"


def test_retake_goes_live_with_the_bomb_planted_and_nothing_for_sale():
    room = room_for("retake", "hd_dust2")
    a = room.add("a", None, team=0)
    d = room.add("d", None, team=1)
    room.simulate(0.05)  # warmup -> round 1
    mode = room.mode
    assert mode.state.remaining <= retake.RETAKE_FREEZE < FREEZE_TIME
    site = next(s for s in mode.sites if s.id == mode.site_id)
    attacker = a if a.team == mode.state.attackers else d
    assert (attacker.state.x - site.x) ** 2 + (attacker.state.y - site.y) ** 2 <= 9.0
    assert room.state_payload()["mode"]["catalog"] == []
    assert not mode.can_buy(room, attacker)
    for _ in range(int(retake.RETAKE_FREEZE / 0.05) + 3):
        room.simulate(0.05)
    assert mode.state.phase == LIVE
    assert mode.state.bomb.state == "planted" and mode.state.bomb.site == site.id
    # Each side's card.
    defender = d if attacker is a else a
    assert index_of(retake.CARDS[weapons.side_of(attacker.team)]) in attacker.owned
    assert "defuser" in defender.owned_extras


def test_retake_needs_bomb_sites():
    import pytest

    with pytest.raises(ValueError):
        room_for("retake", "hd_pit")
    assert modes.is_mode("retake") and modes.is_mode("aim") and modes.is_mode("awp")

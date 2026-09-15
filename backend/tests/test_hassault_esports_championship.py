"""Comprehensive Unit Tests for Esports World Championship Suite.

Covers:
1. Frontier 1: Legendary Championship Arena hd_mirage layout, geometry, and bomb sites.
2. Frontier 2: Authoritative Ranked Matchmaking, Placement Calibration, and Seasonal MMR.
3. Frontier 3: Caster Observer Director action evaluation and Anti-Cheat PVS Fog-of-War.
4. Frontier 4: Co-op PvE Operations (Breach & Clear) and Squad Command Wheel.
5. Frontier 5: Physical Material Bullet Penetration and Wallbang Physics.
"""

import json
import os
import time
import pytest
from httpx import ASGITransport, AsyncClient

from backend.app import app
from backend.modules.hassault import director, match, ranked_mmr, weapons
from backend.modules.hassault.modes import build, catalog
from backend.modules.hassault.modes.breach import Breach, Hostage


# ============================================================================
# Frontier 1: hd_mirage Map Tests
# ============================================================================

def test_hd_mirage_json_map_validity():
    """Verify hd_mirage.json map file exists and contains competitive sites."""
    map_path = "backend/modules/hassault/maps/hd_mirage.json"
    assert os.path.exists(map_path), f"Map file missing at {map_path}"

    with open(map_path, "r") as f:
        data = json.load(f)

    assert data["title"] == "Desert Courtyard (Mirage)"
    assert data["author"] == "horrible-dashboard"
    assert len(data["spawns"]) >= 8
    ct_spawns = [s for s in data["spawns"] if s["team"] == 0]
    t_spawns = [s for s in data["spawns"] if s["team"] == 1]
    assert len(ct_spawns) >= 4
    assert len(t_spawns) >= 4

    # Verify Bomb Sites A and B
    sites = {s["id"]: s for s in data.get("objectives", {}).get("sites", [])}
    assert "A" in sites, "Site A missing"
    assert "B" in sites, "Site B missing"

    # Verify GLB model binary exists
    glb_path = "backend/modules/hassault/maps/hd_mirage.glb"
    assert os.path.exists(glb_path), f"GLB binary missing at {glb_path}"
    assert os.path.getsize(glb_path) > 10_000, "GLB binary suspiciously small"


# ============================================================================
# Frontier 2: Authoritative Ranked MMR & Seasons Tests
# ============================================================================

def test_ranked_profile_uncalibrated_and_placement():
    """Verify player starts unranked and requires placement matches."""
    player_id = f"test_ranked_player_{int(time.time()*1000)}"
    profile = ranked_mmr.get_player_ranked_profile(player_id)

    assert not profile["isCalibrated"]
    assert profile["calibrationRemaining"] == ranked_mmr.PLACEMENT_MATCHES_REQUIRED
    assert "Unranked" in profile["tier"]
    assert profile["tierBadge"] == "unranked"


def test_ranked_calibration_match_recording():
    """Verify recording matches updates history, calculates ELO, and records stats."""
    player_id = f"test_ranked_record_{int(time.time()*1000)}"

    # Record 10 placement matches to calibrate
    for i in range(10):
        res = {
            "won": True,
            "roundsWon": 13,
            "roundsLost": 7,
            "kills": 22,
            "deaths": 11,
            "headshots": 12,
            "damage": 2400,
            "adr": 120.0,
            "mvps": 3,
        }
        profile = ranked_mmr.record_ranked_match(
            account_id=player_id,
            match_id=f"match_{i}",
            map_name="hd_mirage",
            result=res,
        )

    assert profile["isCalibrated"]
    assert profile["calibrationRemaining"] == 0
    assert profile["matchesPlayed"] == 10
    assert profile["wins"] == 10
    assert profile["winRate"] == 100.0
    assert profile["kdRatio"] > 1.5
    assert profile["totalMvps"] == 30
    assert profile["tierBadge"] != "unranked"
    assert len(profile["recentMatches"]) == 10


def test_ranked_extended_leaderboard():
    """Verify extended leaderboard returns entries with tier badges and calibration info."""
    entries = ranked_mmr.get_ranked_leaderboard_extended(limit=10)
    assert isinstance(entries, list)
    for e in entries:
        assert "accountId" in e
        assert "tierBadge" in e
        assert "isCalibrated" in e


@pytest.mark.anyio
async def test_ranked_routes_api():
    """Verify REST endpoints for ranked profiles, seasons, and calibration."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Season info
        r_season = await client.get("/api/hassault/ranked/season")
        assert r_season.status_code == 200
        season_data = r_season.json()
        assert "season" in season_data
        assert "tiers" in season_data

        # Leaderboard
        r_lb = await client.get("/api/hassault/ranked/leaderboard")
        assert r_lb.status_code == 200
        assert isinstance(r_lb.json(), list)

        # Profile
        test_acc = f"acc_{int(time.time())}"
        r_prof = await client.get(f"/api/hassault/ranked/profile/{test_acc}")
        assert r_prof.status_code == 200
        prof_data = r_prof.json()
        assert prof_data["accountId"] == test_acc
        assert not prof_data["isCalibrated"]

        # Calibrate match endpoint
        r_calib = await client.post(
            "/api/hassault/ranked/calibrate",
            json={
                "accountId": test_acc,
                "mapName": "hd_mirage",
                "result": {"won": True, "roundsWon": 13, "roundsLost": 5, "kills": 18, "deaths": 8, "damage": 1900},
            },
        )
        assert r_calib.status_code == 200
        calib_res = r_calib.json()
        assert calib_res["matchesPlayed"] == 1


# ============================================================================
# Frontier 3: Caster Observer Director & PVS Anti-Cheat Tests
# ============================================================================

def test_observer_director_action_scoring():
    """Verify director evaluates player action intensity and priorities."""
    od = director.ObserverDirector(min_shot_duration=0.0)  # zero delay for immediate test switches

    class MockState:
        def __init__(self, x=0.0, y=0.0, z=0.0, health=100, yaw=0.0, pitch=0.0, weapon="assault"):
            self.x = x
            self.y = y
            self.z = z
            self.health = health
            self.yaw = yaw
            self.pitch = pitch
            self.weapon = weapon

    class MockPlayer:
        def __init__(self, pid, team, x=0.0, y=0.0, z=0.0, health=100, weapon="assault"):
            self.id = pid
            self.team = team
            self.alive = True
            self.state = MockState(x, y, z, health, weapon=weapon)
            self.last_damage_dealt_time = 0.0

    class MockRoom:
        def __init__(self):
            self.players = {}
            self.bomb_planted = False
            self.mode = None

    rm = MockRoom()
    p1 = MockPlayer("p1", team=0, x=10.0, y=10.0)
    p2 = MockPlayer("p2", team=1, x=12.0, y=10.0)  # in combat distance
    rm.players = {"p1": p1, "p2": p2}

    now = 100.0
    # p1 dealt damage recently -> high excitement
    p1.last_damage_dealt_time = 99.5
    eval_result = od.evaluate(rm, now=now)

    assert eval_result["targetId"] == "p1"
    assert "Firefight" in eval_result["focusReason"]
    assert "camera" in eval_result
    assert "x" in eval_result["camera"]


def test_anti_cheat_pvs_culling():
    """Verify occluded silent enemies have precise positions masked in snapshot packets."""
    class MockState:
        def __init__(self, x=0.0, y=0.0, z=0.0, health=100):
            self.x = x
            self.y = y
            self.z = z
            self.health = health

    class MockPlayer:
        def __init__(self, pid, team, x=0.0, y=0.0, z=0.0):
            self.id = pid
            self.team = team
            self.state = MockState(x, y, z)
            self.alive = True
            self.last_fire_time = 0.0

    class MockRoom:
        def __init__(self):
            self.pvs_culling = True
            self.players = {}
            self.mode = type("Mode", (), {"teams": True})()

        cull_pvs_snapshot_rows = match.MatchRoom.cull_pvs_snapshot_rows

    rm = MockRoom()
    viewer = MockPlayer("viewer", team=0, x=10.0, y=10.0, z=0.0)
    ally = MockPlayer("ally", team=0, x=12.0, y=10.0, z=0.0)
    enemy_visible = MockPlayer("enemy_vis", team=1, x=20.0, y=20.0, z=0.0)
    enemy_hidden = MockPlayer("enemy_hid", team=1, x=50.0, y=50.0, z=0.0)

    rm.players = {
        "viewer": viewer,
        "ally": ally,
        "enemy_vis": enemy_visible,
        "enemy_hid": enemy_hidden,
    }

    input_rows = [
        {"id": "viewer", "team": 0, "x": 10.0, "y": 10.0, "z": 0.0, "vx": 0.0, "vy": 0.0, "vz": 0.0},
        {"id": "ally", "team": 0, "x": 12.0, "y": 10.0, "z": 0.0, "vx": 0.0, "vy": 0.0, "vz": 0.0},
        {"id": "enemy_vis", "team": 1, "x": 20.0, "y": 20.0, "z": 0.0, "vx": 1.0, "vy": 0.0, "vz": 0.0},
        {"id": "enemy_hid", "team": 1, "x": 50.0, "y": 50.0, "z": 0.0, "vx": 2.0, "vy": 0.0, "vz": 0.0},
    ]

    spotted_by_team = {0: ["enemy_vis"], 1: []}
    culled_rows = rm.cull_pvs_snapshot_rows(viewer, input_rows, spotted_by_team)

    row_by_id = {r["id"]: r for r in culled_rows}
    # Viewer & Ally: intact
    assert row_by_id["viewer"]["x"] == 10.0
    assert row_by_id["ally"]["x"] == 12.0

    # Spotted Enemy: intact
    assert row_by_id["enemy_vis"]["x"] == 20.0
    assert not row_by_id["enemy_vis"].get("occluded", False)

    # Occluded Silent Enemy: coordinates masked!
    assert row_by_id["enemy_hid"]["occluded"] is True
    assert row_by_id["enemy_hid"]["x"] == 0.0
    assert row_by_id["enemy_hid"]["z"] == -100.0


# ============================================================================
# Frontier 4: Co-op PvE Operations & Squad Command Wheel Tests
# ============================================================================

def test_breach_mode_lifecycle_and_hostage_rescue():
    """Verify Breach & Clear mode lifecycle, hostage following, and star ratings."""
    mode = build("breach")
    assert isinstance(mode, Breach)
    assert mode.teams is True
    assert mode.score_label == "Rescued"

    class MockState:
        def __init__(self, x=34.0, y=56.0, z=0.0):
            self.x = x
            self.y = y
            self.z = z
            self.yaw = 0.0
            self.health = 100

    class MockPlayer:
        def __init__(self, pid, team=0):
            self.id = pid
            self.team = team
            self.state = MockState()
            self.alive = True

    class MockRoom:
        def __init__(self):
            self.spawns = [type("Spawn", (), {"x": 34.0, "y": 56.0, "z": 0.0, "team": 0})()]
            self.players = {}
            self.scores = [0, 0]
            self.fx = []

    rm = MockRoom()
    mode.attach(rm)

    assert len(mode.hostages) == 2
    h1 = mode.hostages[0]
    p1 = MockPlayer("rescuer_ct", team=0)
    rm.players["rescuer_ct"] = p1

    # Carrier picks up hostage 1
    h1.carrier_id = p1.id
    # Move player into extraction zone
    p1.state.x, p1.state.y = mode.extraction_zone[0], mode.extraction_zone[1]
    mode.tick(rm, elapsed=0.1, now=time.monotonic())

    assert h1.rescued is True
    assert h1.carrier_id is None
    assert mode.hostages_rescued_count == 1
    assert rm.scores[0] == 1


def test_squad_command_wheel():
    """Verify squad command wheel issues orders to bot squadmates."""
    mode = Breach()

    class MockState:
        def __init__(self):
            self.x = 20.0
            self.y = 20.0
            self.z = 0.0

    class MockPlayer:
        def __init__(self, pid, is_bot=False):
            self.id = pid
            self.team = 0
            self.is_bot = is_bot
            self.state = MockState()

    class MockRoom:
        def __init__(self):
            self.players = {}
            self.fx = []

    rm = MockRoom()
    leader = MockPlayer("leader_human", is_bot=False)
    bot1 = MockPlayer("bot_alpha", is_bot=True)
    bot2 = MockPlayer("bot_bravo", is_bot=True)
    rm.players = {leader.id: leader, bot1.id: bot1, bot2.id: bot2}

    cmd = mode.issue_squad_command(rm, leader, "HOLD")
    assert cmd["type"] == "HOLD"
    assert getattr(bot1, "squad_command")["type"] == "HOLD"
    assert getattr(bot2, "squad_command")["type"] == "HOLD"


# ============================================================================
# Frontier 5: Physical Material Bullet Penetration Tests
# ============================================================================

def test_material_bullet_penetration():
    """Verify material penetration calculations across sniper, rifles, SMGs, and barriers."""
    sniper = weapons.WEAPON_BY_ID["sniper"]
    assault = weapons.WEAPON_BY_ID["assault"]
    knife = weapons.WEAPON_BY_ID["knife"]

    # Sniper penetrates wood up to 1.5m
    can_pen, falloff = weapons.calculate_material_penetration(sniper, material="wood", thickness=0.4)
    assert can_pen is True
    assert falloff > 0.4

    # Sniper penetrates metal (sheet)
    can_pen_metal, falloff_metal = weapons.calculate_material_penetration(sniper, material="metal", thickness=0.2)
    assert can_pen_metal is True

    # Assault rifle penetrates thin wood (0.4m), but fails on thick stone (1.5m)
    can_pen_rifle, _ = weapons.calculate_material_penetration(assault, material="wood", thickness=0.4)
    assert can_pen_rifle is True

    can_pen_stone, _ = weapons.calculate_material_penetration(assault, material="stone", thickness=1.5)
    assert can_pen_stone is False

    # Pistol fails on thick metal
    pistol = weapons.WEAPON_BY_ID["pistol"]
    can_pen_pistol, _ = weapons.calculate_material_penetration(pistol, material="metal", thickness=0.8)
    assert can_pen_pistol is False

    # Knife cannot penetrate walls
    can_pen_knife, _ = weapons.calculate_material_penetration(knife, material="wood", thickness=0.1)
    assert can_pen_knife is False

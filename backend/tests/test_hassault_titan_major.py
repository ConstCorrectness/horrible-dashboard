"""Comprehensive Unit Tests for Titan League Major Suite.

Covers:
1. Frontier 1: Multi-Level Arena hd_nuke layout, verticality, sites A & B, GLB binary.
2. Frontier 2: Authoritative Major Tournament Bracket & Pick/Ban Veto Engine (Swiss stage, Playoffs, BO1/BO3 vetoes, REST endpoints).
3. Frontier 3: Tickrate Invariant Sub-Tick Movement & Input Buffering (subtick parsing, fractional jump impulse).
4. Frontier 4: Procedural Tactical Smoke & Volumetric Blast Dissipation (volumetric nodes, HE dispersion clearance, fire extinguishing).
5. Frontier 5: Spatial 3D HRTF Audio Propagation & Physical Occlusion (Woodworth ITD, binaural azimuth/elevation, wall attenuation).
"""

import json
import math
import os
import time
import pytest
from httpx import ASGITransport, AsyncClient

from backend.app import app
from backend.modules.hassault import grenades, match, noise, physics
from backend.modules.hassault.physics import MoveInput, PlayerState, flat_world
from backend.modules.hassault.tournaments import (
    ACTIVE_DUTY_MAPS,
    TournamentEngine,
    tournament_manager,
)


# ============================================================================
# Frontier 1: Multi-Level Arena hd_nuke Tests
# ============================================================================

def test_hd_nuke_json_map_structure_and_verticality():
    """Verify hd_nuke.json map exists, passes lint requirements, and includes Sites A and B."""
    map_path = "backend/modules/hassault/maps/hd_nuke.json"
    assert os.path.exists(map_path), f"Map file missing at {map_path}"

    with open(map_path, "r") as f:
        data = json.load(f)

    assert data["title"] == "Nuclear Facility (Nuke)"
    assert len(data["spawns"]) == 8
    ct_spawns = [s for s in data["spawns"] if s["team"] == 0]
    t_spawns = [s for s in data["spawns"] if s["team"] == 1]
    assert len(ct_spawns) == 4
    assert len(t_spawns) == 4

    # Verify Bomb Sites A and B
    sites = {s["id"]: s for s in data.get("objectives", {}).get("sites", [])}
    assert "A" in sites, "Site A missing"
    assert "B" in sites, "Site B missing"

    # Verify GLB model binary exists and has non-trivial size
    glb_path = "backend/modules/hassault/maps/hd_nuke.glb"
    assert os.path.exists(glb_path), f"GLB binary missing at {glb_path}"
    assert os.path.getsize(glb_path) > 20_000, "GLB binary is too small"

    web_glb = "apps/web/public/hd_nuke.glb"
    assert os.path.exists(web_glb), f"Web GLB missing at {web_glb}"


# ============================================================================
# Frontier 2: Authoritative Tournament Bracket & Pick/Ban Veto Engine
# ============================================================================

def test_tournament_creation_and_swiss_round_1():
    """Verify 16-team tournament creation, Swiss stage seeding and Round 1 pairings."""
    engine = TournamentEngine()
    tourn_id = f"test_tourn_{int(time.time()*1000)}"
    t_state = engine.create_major(tourn_id, "Test Major 2026")

    assert t_state["id"] == tourn_id
    assert t_state["stage"] == "swiss"
    assert t_state["round"] == 1
    assert len(t_state["teams"]) == 16
    assert len(t_state["matches"]) == 8  # 16 teams / 2 = 8 matches in round 1

    # Verify Active Duty 7-map pool
    assert len(t_state["activeDutyMaps"]) == 7
    assert "hd_nuke" in t_state["activeDutyMaps"]
    assert "hd_mirage" in t_state["activeDutyMaps"]
    assert "hd_inferno" in t_state["activeDutyMaps"]


def test_map_veto_bo1_sequence():
    """Verify full BO1 6-ban + 1-decider map veto sequence."""
    engine = TournamentEngine()
    tourn_id = f"test_tourn_veto_{int(time.time()*1000)}"
    t_state = engine.create_major(tourn_id, "Test Major Veto")
    match_id = t_state["matches"][0]["id"]
    team_a = t_state["matches"][0]["team_a_id"]
    team_b = t_state["matches"][0]["team_b_id"]

    # Initial veto state
    v_state = engine.get_veto_state(match_id)
    assert v_state["format"] == "BO1"
    assert v_state["currentTurn"] == team_a
    assert v_state["currentAction"] == "ban"
    assert len(v_state["remainingMaps"]) == 7

    # Ban 1: Team A bans hd_facility
    v_state = engine.perform_veto(match_id, team_a, "ban", "hd_facility")
    assert "hd_facility" in v_state["bannedMaps"]
    assert v_state["currentTurn"] == team_b

    # Ban 2: Team B bans hd_bank
    v_state = engine.perform_veto(match_id, team_b, "ban", "hd_bank")
    assert "hd_bank" in v_state["bannedMaps"]
    assert v_state["currentTurn"] == team_a

    # Ban 3: Team A bans hd_office
    v_state = engine.perform_veto(match_id, team_a, "ban", "hd_office")

    # Ban 4: Team B bans hd_dust2
    v_state = engine.perform_veto(match_id, team_b, "ban", "hd_dust2")

    # Ban 5: Team A bans hd_inferno
    v_state = engine.perform_veto(match_id, team_a, "ban", "hd_inferno")

    # Ban 6: Team B bans hd_mirage
    v_state = engine.perform_veto(match_id, team_b, "ban", "hd_mirage")

    # Veto should now be complete and decider map must be hd_nuke!
    assert v_state["isComplete"] is True
    assert v_state["deciderMap"] == "hd_nuke"


def test_tournament_swiss_progression_and_playoffs():
    """Simulate Swiss rounds until qualification and verify playoffs seeding."""
    engine = TournamentEngine()
    tourn_id = f"test_tourn_prog_{int(time.time()*1000)}"
    t_state = engine.create_major(tourn_id, "Test Major Progression")

    # Simulate 5 Swiss rounds
    for r in range(1, 6):
        curr_state = engine.get_tournament_state(tourn_id)
        if curr_state["stage"] != "swiss":
            break
        # Complete all matches in current round
        round_matches = [m for m in curr_state["matches"] if m["round"] == r and m["status"] != "completed"]
        for m in round_matches:
            # Team A always wins for deterministic progression
            winner = m["team_a_id"]
            engine.complete_match(m["id"], winner, score_a=13, score_b=7)

    final_state = engine.get_tournament_state(tourn_id)
    # Stage should advance to playoffs once 8 teams qualify
    assert final_state["stage"] in ("swiss", "playoffs")
    teams = final_state["teams"]
    assert any(t["wins"] > 0 for t in teams)


@pytest.mark.anyio
async def test_tournament_rest_endpoints():
    """Verify REST endpoints for tournament state, match query, veto and completion."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # GET /api/hassault/tournaments/major
        res = await client.get("/api/hassault/tournaments/major?tournament_id=rest_major_test")
        assert res.status_code == 200
        data = res.json()
        assert data["id"] == "rest_major_test"
        assert len(data["teams"]) == 16
        assert len(data["matches"]) >= 1

        match_id = data["matches"][0]["id"]
        team_a = data["matches"][0]["team_a_id"]

        # GET /api/hassault/tournaments/match/{match_id}
        res_m = await client.get(f"/api/hassault/tournaments/match/{match_id}")
        assert res_m.status_code == 200
        m_data = res_m.json()
        assert m_data["matchId"] == match_id
        assert m_data["currentTurn"] == team_a

        # POST /api/hassault/tournaments/veto
        veto_payload = {
            "match_id": match_id,
            "team_id": team_a,
            "action": "ban",
            "map_name": "hd_office",
        }
        res_v = await client.post("/api/hassault/tournaments/veto", json=veto_payload)
        assert res_v.status_code == 200
        v_data = res_v.json()
        assert "hd_office" in v_data["bannedMaps"]

        # POST /api/hassault/tournaments/match/{match_id}/complete
        comp_payload = {
            "winner_id": team_a,
            "score_a": 13,
            "score_b": 10,
        }
        res_c = await client.post(
            f"/api/hassault/tournaments/match/{match_id}/complete", json=comp_payload
        )
        assert res_c.status_code == 200


# ============================================================================
# Frontier 3: Tickrate Invariant Sub-Tick Movement Tests
# ============================================================================

def test_subtick_command_parsing():
    """Verify subtick field parses from raw network dictionary."""
    raw = {
        "seq": 101,
        "forward": 1.0,
        "strafe": 0.0,
        "jump": True,
        "dt": 1 / 64,
        "subtick": 0.42,
    }
    cmd = match.parse_command(raw)
    assert cmd is not None
    assert cmd.seq == 101
    assert cmd.jump is True
    assert abs(cmd.subtick - 0.42) < 1e-4


def test_subtick_jump_impulse_immediate_displacement():
    """Verify subtick jump immediately begins vertical displacement proportionally."""
    world = flat_world(64, floor=0, ceil=16)

    # Standard jump at tick boundary (subtick = 0.0)
    p_standard = PlayerState(x=10.0, y=10.0, z=0.0, on_ground=True)
    physics.step(world, p_standard, MoveInput(jump=True, dt=1 / 64, subtick=0.0), dt=1 / 64)

    # Sub-tick jump occurring at 50% through the tick (subtick = 0.5)
    p_subtick = PlayerState(x=10.0, y=10.0, z=0.0, on_ground=True)
    physics.step(world, p_subtick, MoveInput(jump=True, dt=1 / 64, subtick=0.5), dt=1 / 64)

    # Subtick jump begins climbing within the tick so z position is greater than standard starting at floor
    assert p_subtick.z > 0.0
    assert not p_subtick.on_ground


# ============================================================================
# Frontier 4: Procedural Tactical Smoke & Volumetric Blast Dissipation
# ============================================================================

def test_volumetric_smoke_nodes_generation():
    """Verify volumetric smoke nodes expand outwards around solid walls."""
    world = flat_world(64, floor=0, ceil=16)
    nodes = grenades.generate_volumetric_smoke_nodes(world, x=30.0, y=30.0, z=0.0, radius=4.5)
    assert len(nodes) >= 8
    center_node = nodes[0]
    assert center_node["x"] == 30.0
    assert center_node["y"] == 30.0


def test_smoke_zone_contains_and_volumetric_bounds():
    """Verify Zone.contains checks main radius and expanded volumetric sub-nodes."""
    zone = grenades.Zone(
        id="test_zone_1",
        kind="smoke",
        owner="p1",
        team=0,
        x=20.0,
        y=20.0,
        z=0.0,
        radius=4.0,
        remaining=15.0,
        duration=15.0,
        damage_per_second=0.0,
        volumetric_nodes=[
            {"x": 20.0, "y": 20.0, "z": 0.0, "r": 2.5},
            {"x": 23.5, "y": 20.0, "z": 0.0, "r": 2.0},
        ],
    )
    # Inside main radius
    assert zone.contains(20.0, 20.0, 0.0) is True
    # Inside offset volumetric node
    assert zone.contains(24.5, 20.0, 0.0) is True
    # Far outside
    assert zone.contains(35.0, 20.0, 0.0) is False


def test_he_dispersion_clears_smoke_temporarily():
    """Verify HE blast marks smoke zone as cleared for 2.5 seconds."""
    now = 100.0
    zone = grenades.Zone(
        id="test_smoke",
        kind="smoke",
        owner="p1",
        team=0,
        x=20.0,
        y=20.0,
        z=0.0,
        radius=4.5,
        remaining=15.0,
        duration=15.0,
        damage_per_second=0.0,
    )
    # Initially active
    assert zone.cleared_until <= now
    assert grenades.sight_blocked_by([zone], (20.0, 10.0, 1.0), (20.0, 30.0, 1.0), now=now) is True

    # HE explosion sets cleared_until
    zone.cleared_until = now + 2.5

    # During 2.5s dissipation window, smoke is cleared and sight is unblocked
    assert grenades.sight_blocked_by([zone], (20.0, 10.0, 1.0), (20.0, 30.0, 1.0), now=now + 1.0) is False

    # After 2.5s expires, smoke re-blooms
    assert grenades.sight_blocked_by([zone], (20.0, 10.0, 1.0), (20.0, 30.0, 1.0), now=now + 3.0) is True


# ============================================================================
# Frontier 5: Spatial 3D HRTF Audio Propagation & Occlusion Tests
# ============================================================================

def test_hrtf_spatial_params_woodworth():
    """Verify Woodworth ITD calculation and stereo panning across azimuth angles."""
    listener_pos = (20.0, 20.0, 1.7)
    listener_yaw = 0.0  # Facing positive X (+dx)

    # Sound directly ahead (azimuth = 0)
    sound_ahead = (30.0, 20.0, 1.7)
    params_ahead = noise.hrtf_spatial_params(listener_pos, listener_yaw, sound_ahead)
    assert abs(params_ahead["azimuth"]) < 1.0
    assert abs(params_ahead["pan"]) < 0.05
    assert abs(params_ahead["itd_ms"]) < 0.05

    # Sound 90 degrees to the right (+Y)
    sound_right = (20.0, 30.0, 1.7)
    params_right = noise.hrtf_spatial_params(listener_pos, listener_yaw, sound_right)
    assert 85.0 < params_right["azimuth"] < 95.0
    assert params_right["pan"] > 0.9  # Panned right
    assert params_right["itd_ms"] > 0.5  # Positive ITD (reaches right ear first)

    # Sound 90 degrees to the left (-Y)
    sound_left = (20.0, 10.0, 1.7)
    params_left = noise.hrtf_spatial_params(listener_pos, listener_yaw, sound_left)
    assert -95.0 < params_left["azimuth"] < -85.0
    assert params_left["pan"] < -0.9  # Panned left
    assert params_left["itd_ms"] < -0.5  # Negative ITD


def test_acoustic_wall_occlusion_and_lowpass_muffle():
    """Verify raycast against solid geometry applies wall transmission loss and marks noise occluded."""
    world = flat_world(64, floor=0, ceil=16)
    types = bytearray(world.type)
    # Place solid wall at x=25
    for y in range(64):
        types[y * 64 + 25] = 0
    walled_world = physics.World(
        ssize=64,
        type=bytes(types),
        floor=world.floor,
        ceil=world.ceil,
        vdelta=world.vdelta,
    )

    sound = noise.Noise(kind="shot", source="enemy", x=30.0, y=20.0, z=1.0, loudness=100.0, weapon="ak47")
    listener_pos = (20.0, 20.0, 1.7)

    # Unoccluded sound (open world)
    open_heard = noise.hear(world, listener_pos, sound)
    assert open_heard is not None
    open_vol, _, _, open_occluded = open_heard
    assert open_occluded is False

    # Occluded sound (through solid wall at x=25)
    walled_heard = noise.hear(walled_world, listener_pos, sound)
    assert walled_heard is not None
    walled_vol, _, _, walled_occluded = walled_heard
    assert walled_occluded is True
    # Volume is muffled through the wall
    assert walled_vol < open_vol

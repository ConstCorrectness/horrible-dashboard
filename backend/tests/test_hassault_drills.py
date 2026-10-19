"""Aim drills: the served schedules, the scoring reference, and the routes.

See `backend/modules/hassault/drills.py`. The clients replay what is pinned in
`drill-vectors.json`; this file pins that the fixture is what the module says
today, and that every drill can actually be run on the map it names.
"""

from __future__ import annotations

import json
import math
import random

import pytest
from fastapi.testclient import TestClient

from backend.modules.hassault import drills, mapsource, physics

VECTORS = "packages/core/src/modules/hassault/__tests__/drill-vectors.json"


def test_the_committed_fixture_is_current():
    from scripts.gen_hassault_drill_vectors import payload

    with open(VECTORS, encoding="utf-8") as fh:
        committed = json.load(fh)
    # Through JSON, so a tuple and a list compare equal and floats round-trip
    # exactly as the clients will read them.
    assert committed == json.loads(json.dumps(payload())), (
        "drill-vectors.json is stale: "
        "PYTHONPATH=. uv run python scripts/gen_hassault_drill_vectors.py"
    )


def test_hd_aim_ships_with_its_stations():
    assert "hd_aim" in mapsource.bundled_names()
    ids = {s["id"] for s in drills.stations("hd_aim")}
    for drill in drills.DRILLS:
        if drill.map == "hd_aim":
            assert drill.station in ids, drill.id


@pytest.mark.parametrize(
    "drill", [d for d in drills.DRILLS if d.map], ids=lambda d: d.id
)
def test_every_drill_expands_on_its_map(drill):
    out = drills.expand(drill.id)
    assert out["map"] == drill.map
    assert out["shooter"] is not None
    assert 0 < len(out["targets"]) <= drills.MAX_TARGETS
    for target in out["targets"]:
        segments = target["segments"]
        assert segments[0][0] == 0, "a schedule starts at tick 0"
        ticks = [s[0] for s in segments]
        assert ticks == sorted(set(ticks)), "segments are strictly increasing"
        assert all(s[1] in (-1, 0, 1) for s in segments)


@pytest.mark.parametrize("station", drills.stations("hd_aim"), ids=lambda s: s["id"])
def test_a_strafing_target_reaches_both_ends_of_its_rail(station):
    """A rail that runs into a wall is a target that stops dead somewhere and
    never turns back. Every hd_aim rail is clear for its whole width."""
    world, _cmap, _src = drills._map("hd_aim")
    sx, sy = station["shooter"]
    for px, py, half in station["posts"]:
        yaw = math.atan2(sy - py, sx - px)
        state = drills._stand(world, px, py, yaw)
        post = (state.x, state.y)
        _segments, trace = drills.schedule(
            world,
            state,
            half,
            "wide",
            20 * drills.TICK_RATE,
            random.Random(0),
            samples=1,
        )
        side = (-math.sin(yaw), math.cos(yaw))
        lateral = [
            (x - post[0]) * side[0] + (y - post[1]) * side[1] for _k, x, y, _z in trace
        ]
        reach = half - drills.TURN_MARGIN
        assert max(lateral) >= reach and min(lateral) <= -reach, (station["id"], px, py)
        # And it stays on its own floor: nothing about a rail should be a fall.
        assert all(abs(z - trace[0][3]) < 1e-6 for *_rest, z in trace), (px, py)


def test_the_any_map_drill_stands_targets_on_the_nearest_spawns():
    cmap = mapsource.load_bundled("hd_dust2")
    spawn = next(e for e in cmap.entities if e.name == "playerstart")
    out = drills.expand("spawn_strafe", "hd_dust2", (spawn.x + 0.5, spawn.y + 0.5))
    assert out["shooter"] is None
    assert out["targets"]
    for target in out["targets"]:
        assert math.hypot(target["x"] - spawn.x - 0.5, target["y"] - spawn.y - 0.5) > (
            drills.NEAR_SPAWN
        )


@pytest.mark.parametrize(
    "args",
    [
        ("nope",),
        ("spawn_strafe",),  # any-map, and no map given
        ("spawn_strafe", "hd_dust2"),  # and no position
        ("aim_static", "hd_dust2"),  # an hd_aim drill on another map
    ],
)
def test_a_drill_that_cannot_run_here_says_why(args):
    with pytest.raises(drills.DrillError):
        drills.expand(*args)


def test_replaying_a_schedule_lands_where_the_simulation_did():
    """The claim the clients rely on, made against Python's own step: segments
    alone (no RNG, no turn-back rule) reproduce the trace."""
    with open(VECTORS, encoding="utf-8") as fh:
        vectors = json.load(fh)
    from scripts.gen_hassault_vectors import build

    world = build(vectors["world"])
    for case in vectors["replays"]:
        s = case["start"]
        state = physics.PlayerState(
            x=s["x"], y=s["y"], z=s["z"], yaw=s["yaw"], on_ground=True
        )
        segments = case["segments"]
        samples = {k: (x, y, z) for k, x, y, z in case["samples"]}
        seg = 0
        for k in range(case["ticks"] + 1):
            if k in samples:
                ex, ey, ez = samples[k]
                assert (state.x, state.y, state.z) == pytest.approx(
                    (ex, ey, ez), abs=1e-9
                )
            if k == case["ticks"]:
                break
            while seg + 1 < len(segments) and segments[seg + 1][0] <= k:
                seg += 1
            _tick, strafe, crouch, jump, _visible = segments[seg]
            physics.step(
                world,
                state,
                physics.MoveInput(
                    strafe=float(strafe), crouch=bool(crouch), jump=bool(jump)
                ),
                drills.TICK,
            )


# ---- routes ----------------------------------------------------------------------


@pytest.fixture
def node():
    from backend.app import app

    return TestClient(app)


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    from backend.games_server.app import app

    return TestClient(app)


@pytest.mark.parametrize("which", ["node", "server"])
def test_both_backends_serve_the_drills(which, request):
    client = request.getfixturevalue(which)
    listed = client.get("/api/hassault/drills", params={"map": "hd_aim"}).json()
    assert {d["id"] for d in listed} == {
        d.id for d in drills.DRILLS if d.map in (None, "hd_aim")
    }
    on_dust = client.get("/api/hassault/drills", params={"map": "hd_dust2"}).json()
    assert [d["id"] for d in on_dust] == ["spawn_strafe"]

    body = client.get("/api/hassault/drills/aim_strafe").json()
    assert body["tickRate"] == drills.TICK_RATE
    assert body["targets"][0]["segments"]
    assert body["headOnly"] is False

    refused = client.get("/api/hassault/drills/spawn_strafe")
    assert refused.status_code == 400
    assert "any map" in refused.json()["detail"]


def test_the_weapons_route_serves_falloff_start(node):
    """`WeaponOut` is a response model, and a response model drops a field it
    does not declare: `falloffStart` was in `to_dict` and missing on the wire."""
    from backend.modules.hassault import weapons

    served = {w["id"]: w for w in node.get("/api/hassault/weapons").json()}
    for weapon in weapons.WEAPONS:
        assert served[weapon.id]["falloffStart"] == weapon.falloff_start

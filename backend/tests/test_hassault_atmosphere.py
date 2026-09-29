"""The served light rig and point lights — see `backend/modules/hassault/atmosphere.py`.

Tested through the HTTP response, not only the model: a field added to a
pydantic model and left out of `response_model` is dropped without a word, and
both clients would then quietly draw their own defaults.
"""

from __future__ import annotations

import pytest

from backend.modules.hassault import atmosphere, mapsource
from backend.modules.hassault.cgz import CgzError


@pytest.fixture
def server(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    from backend.games_server.app import app

    return TestClient(app)


def test_every_bundled_map_serves_a_resolved_atmosphere(server):
    for name in mapsource.bundled_names():
        info = server.get(f"/api/hassault/maps/{name}").json()
        atmos = info["atmosphere"]
        assert atmos is not None, name
        # Resolved means complete: a client carries no defaults of its own.
        assert set(atmos) == set(atmosphere.CUBE_DEFAULT), name
        for key in ("sunDir", "fillDir"):
            length = sum(v * v for v in atmos[key]) ** 0.5
            assert abs(length - 1.0) < 1e-4, (name, key)
        assert isinstance(info["lights"], list)


def test_a_cube_map_with_no_atmosphere_is_lit_exactly_as_before(server):
    """The browser's old constants, served. The native parity test pins its own
    decoded copy of these against the same numbers."""
    atmos = server.get("/api/hassault/maps/hd_pit").json()["atmosphere"]
    assert atmos["hemiSky"] == 0xBFD4FF
    assert atmos["sunColor"] == 0xFFF2DD
    assert atmos["fogColor"] == 0x11161F
    assert atmos["fogDensity"] == 0.0055
    assert atmos["exposure"] == 1.15
    assert atmos["sunDisc"] is False


def test_cube_arenas_serve_their_light_entities(server):
    lights = server.get("/api/hassault/maps/hd_atrium").json()["lights"]
    assert len(lights) == 9
    for light in lights:
        assert light["radius"] > 0
        assert 0 <= light["color"] <= 0xFFFFFF
        assert light["intensity"] > 0


def test_a_source_atmosphere_overrides_only_what_it_names():
    world = mapsource.build(
        {
            "sfactor": 6,
            "brushes": [{"op": "room", "rect": [2, 2, 20, 20], "floor": 0, "ceil": 10}],
            "atmosphere": {"sunColor": "#ffcc88", "sunDir": [0, 2, 0], "exposure": 1.3},
        }
    )
    resolved = atmosphere.resolve(world.atmosphere, "gltf")
    assert resolved["sunColor"] == 0xFFCC88
    assert resolved["sunDir"] == [0.0, 1.0, 0.0]
    assert resolved["exposure"] == 1.3
    # Untouched keys come from the *format's* defaults.
    assert resolved["skyHorizon"] == atmosphere.GLTF_DEFAULT["skyHorizon"]


@pytest.mark.parametrize(
    "bad",
    [
        {"exposure": 40},
        {"sunDir": [0, 0, 0]},
        {"fogColor": "not a colour"},
        {"sunshine": 1},
        {"sunDisc": "yes"},
    ],
)
def test_a_bad_atmosphere_fails_the_build(bad):
    with pytest.raises(CgzError):
        mapsource.build({"sfactor": 6, "atmosphere": bad})


def test_ac_grey_lights_and_our_intensity_both_decode():
    world = mapsource.build(
        {
            "sfactor": 6,
            "brushes": [{"op": "room", "rect": [2, 2, 20, 20], "floor": 0, "ceil": 10}],
            "entities": [
                {"type": "light", "x": 5, "y": 5, "radius": 20, "color": [200, 0, 0]},
                {
                    "type": "light",
                    "x": 8,
                    "y": 8,
                    "radius": 12,
                    "color": [255, 128, 64],
                    "intensity": 1.5,
                },
            ],
        }
    )
    grey, warm = atmosphere.lights(world)
    # AC's shorthand: g and b zero means a grey light of brightness r.
    assert grey["color"] == 0xC8C8C8
    assert grey["intensity"] == 1.0
    assert warm["color"] == 0xFF8040
    assert warm["intensity"] == 1.5

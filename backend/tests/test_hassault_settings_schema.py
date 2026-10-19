"""The `hassault.*` settings schema, and both places that serve it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.modules.hassault import crosshair, settings_schema

VECTORS = (
    Path(__file__).resolve().parents[2]
    / "packages/core/src/modules/hassault/__tests__/settings-vectors.json"
)
MACHINE_ONLY = (
    "hassault.video.adapter",
    "hassault.video.backend",
    "hassault.video.powerPreference",
    "hassault.video.monitor",
    "hassault.video.displayMode",
    "hassault.video.exclusiveMode",
    "hassault.nativeBinaryPath",
)


def test_every_key_is_namespaced_unique_and_typed():
    keys = [s.key for s in settings_schema.SCHEMA]
    assert len(keys) == len(set(keys))
    for s in settings_schema.SCHEMA:
        assert s.key.startswith("hassault.")
        assert s.scope in ("account", "machine")
        assert s.type in ("number", "boolean", "string")
        assert isinstance(
            s.default, {"number": (int, float), "boolean": bool, "string": str}[s.type]
        )
        if s.type == "number":
            assert (
                s.min is not None and s.max is not None and s.min <= s.default <= s.max
            )
        if s.enum:
            assert s.default in s.enum
        if s.choices:
            assert s.default in s.choices


def test_the_gpu_monitor_display_and_binary_stay_on_the_machine():
    for key in MACHINE_ONLY:
        assert settings_schema.BY_KEY[key].scope == "machine", key


def test_the_player_follows_the_account():
    account = set(settings_schema.account_keys())
    assert "hassault.sensitivity" in account
    assert "hassault.video.fov" in account
    assert all(k in account for k in crosshair.KEYS.values())


def test_fov_is_one_row_with_the_old_browser_key_as_an_alias():
    fov = settings_schema.BY_KEY["hassault.video.fov"]
    assert (fov.min, fov.max, fov.default) == (60, 110, 75)
    assert fov.aliases == ("hassault.fov",)
    assert "hassault.fov" not in settings_schema.BY_KEY


def test_the_crosshair_rows_are_the_specs_ranges_and_defaults():
    d = crosshair.DEFAULT
    by = settings_schema.BY_KEY
    for field, rng, default in (
        ("size", crosshair.SIZE, d.size),
        ("gap", crosshair.GAP, d.gap),
        ("thickness", crosshair.THICKNESS, d.thickness),
        ("outlineThickness", crosshair.OUTLINE, d.outline),
        ("alpha", crosshair.ALPHA, d.alpha),
    ):
        row = by[crosshair.KEYS[field]]
        assert (row.min, row.max, row.default) == (
            rng[0] / rng[2],
            rng[1] / rng[2],
            default,
        )
    assert by[crosshair.KEYS["style"]].enum == crosshair.STYLES
    assert by[crosshair.KEYS["color"]].enum == crosshair.COLOR_NAMES
    assert by[crosshair.KEYS["color"]].hex_color


def test_the_fixture_is_current():
    """If this fails, run `scripts/gen_hassault_settings_vectors.py`."""
    fixture = json.loads(VECTORS.read_text(encoding="utf-8"))
    fixture.pop("generator")
    assert fixture == settings_schema.to_dict()


def test_the_node_serves_the_schema():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.modules.hassault.routes import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    body = TestClient(app).get("/api/hassault/settings/schema").json()
    assert body == settings_schema.to_dict()


@pytest.fixture
def game_server(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    from backend.games_server.app import app

    return TestClient(app)


def test_the_game_server_serves_the_same_schema(game_server):
    res = game_server.get("/api/hassault/settings/schema")
    assert res.status_code == 200
    assert res.json() == settings_schema.to_dict()

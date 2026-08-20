"""The hitbox spec: that it is the single authority, and that tuning it works.

Two separate claims, and it is worth being clear which is which.

1. **The spec is the only copy.** `physics.py` and `weapons.py` used to own the
   body as constants and the clients each held their own. If tuning the spec does
   not move a hit, some call site is still reading a constant bound at import — the
   exact failure the nullable defaults exist to prevent, and one that would look
   like the tuning lab simply not working.
2. **A stale fixture cannot pass.** `physics-vectors.json` pins that the Python and
   TypeScript physics agree. It says nothing about whether either matches the
   *current* body, so it carries the spec id it was generated against.
"""

from __future__ import annotations

import json
import random
from dataclasses import replace
from pathlib import Path

import pytest

from backend.modules.hassault import hitbox, physics, weapons

VECTORS = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "core"
    / "src"
    / "modules"
    / "hassault"
    / "__tests__"
    / "physics-vectors.json"
)


@pytest.fixture(autouse=True)
def _restore_spec():
    """Every test here tunes a process-global. Leaking one into the next test —
    or into the rest of the suite — would be a spectacularly confusing failure."""
    yield
    hitbox.reset()


# --- identity ---------------------------------------------------------------


def test_the_spec_id_moves_when_a_hit_deciding_number_moves():
    base = hitbox.DEFAULT.spec_id
    assert replace(hitbox.DEFAULT, radius=1.2).spec_id != base
    assert replace(hitbox.DEFAULT, head_band=1.4).spec_id != base
    assert replace(hitbox.DEFAULT, crouch_eye_scale=0.8).spec_id != base


def test_the_spec_id_ignores_the_art_tolerances():
    """A tolerance governs whether a *model* is acceptable, not where a bullet
    lands. Tightening one must not invalidate a physics fixture that is still an
    accurate description of the body."""
    base = hitbox.DEFAULT.spec_id
    assert replace(hitbox.DEFAULT, fit_tolerance=0.05).spec_id == base
    assert replace(hitbox.DEFAULT, eye_tolerance=0.01).spec_id == base


def test_the_shared_fixture_is_not_stale():
    """The guard. If this fails, the body changed and the cross-language vectors
    describe the old one — regenerate them and make *both* suites pass."""
    stamped = json.loads(VECTORS.read_text(encoding="utf-8"))["hitboxSpecId"]
    assert stamped == hitbox.DEFAULT.spec_id, (
        "physics-vectors.json was generated against hitbox spec "
        f"{stamped}, but the current default is {hitbox.DEFAULT.spec_id}"
    )


# --- derived geometry -------------------------------------------------------


def test_derived_heights_match_what_the_modules_used_to_hardcode():
    spec = hitbox.DEFAULT
    assert spec.standing_height == pytest.approx(physics.STANDING_HEIGHT)
    assert spec.crouch_height == pytest.approx(physics.CROUCH_HEIGHT)
    assert spec.crouch_eye_height == pytest.approx(physics.CROUCH_EYE_HEIGHT)
    assert spec.standing_height == pytest.approx(weapons.BODY_HEIGHT)
    assert spec.head_band == pytest.approx(weapons.HEAD_BAND)


def test_crouch_interpolation_hits_both_ends():
    spec = hitbox.DEFAULT
    assert spec.height_at(0.0) == pytest.approx(spec.standing_height)
    assert spec.height_at(1.0) == pytest.approx(spec.crouch_height)
    assert spec.eye_at(0.0) == pytest.approx(spec.eye_height)
    assert spec.eye_at(1.0) == pytest.approx(spec.crouch_eye_height)


# --- tuning actually reaches the code that decides a hit --------------------


def test_tuning_the_radius_moves_the_hitbox():
    """A ray that passes 1.5 cubes wide of a body misses the default 1.1 radius and
    hits a 1.6 one. If this fails, `ray_hits_body` is still defaulting to a
    constant bound at import."""
    origin = (0.0, 1.5, 3.0)
    direction = (1.0, 0.0, 0.0)
    feet = (10.0, 0.0, 0.0)

    assert weapons.ray_hits_body(origin, direction, feet) is None
    hitbox.tune(radius=1.6)
    assert weapons.ray_hits_body(origin, direction, feet) is not None


def test_tuning_the_head_band_moves_where_a_headshot_starts():
    """Aim at a height inside the default body but below its head band, then widen
    the band until that same point is a head. The multiplier applied is the visible
    consequence, so this checks damage rather than a boolean."""
    world = _empty_world()
    # A single-pellet weapon with reach: the knife is first in the table and its
    # 5-cube range stops the shot before it ever gets to the body.
    weapon = next(w for w in weapons.WEAPONS if w.id == "assault")
    rng = random.Random(0)
    spec = hitbox.DEFAULT

    # A shot 1.4 cubes below the top of the body: outside a 1.0 band, inside a 2.0.
    z = spec.standing_height - 1.4
    origin = (16.0, 16.0, z)
    targets = {"victim": (24.0, 16.0, 0.0)}

    body = weapons.resolve_shot(
        world, weapon, origin, (1.0, 0.0, 0.0), targets, rng, spread=0.0
    )
    assert body.hits and not body.hits[0].head

    hitbox.tune(head_band=2.0)
    head = weapons.resolve_shot(
        world, weapon, origin, (1.0, 0.0, 0.0), targets, rng, spread=0.0
    )
    assert head.hits and head.hits[0].head
    assert head.hits[0].damage > body.hits[0].damage


def test_tuning_reaches_the_bodies_the_movement_code_stands_up():
    """`eye_height` and `body_height` are read by the simulation every tick, and by
    the shot rewind through `heights`. They must follow the spec too."""
    player = physics.PlayerState(x=0.0, y=0.0, z=0.0)
    player.crouch = 0.0
    assert physics.eye_height(player) == pytest.approx(hitbox.DEFAULT.eye_height)

    hitbox.tune(eye_height=6.0)
    assert physics.eye_height(player) == pytest.approx(6.0)
    assert physics.body_height(player) == pytest.approx(6.0 + hitbox.DEFAULT.above_eye)


def test_reset_restores_the_shipped_body():
    hitbox.tune(radius=2.0)
    assert hitbox.current().radius == 2.0
    hitbox.reset()
    assert hitbox.current() == hitbox.DEFAULT


def _empty_world() -> physics.World:
    """A small open box: floor at 0, ceiling high, nothing solid in the way. The
    shot tests need somewhere for a bullet to travel that is not a wall."""
    from backend.modules.hassault.cgz import SPACE

    ssize = 32
    n = ssize * ssize
    return physics.World(
        ssize=ssize,
        type=bytes([SPACE]) * n,
        floor=bytes([0]) * n,
        ceil=bytes([32]) * n,
        vdelta=bytes([0]) * n,
    )


# --- the served surface -----------------------------------------------------


def _client():
    from fastapi.testclient import TestClient

    from backend.app import app

    return TestClient(app)


def test_the_route_serves_every_dimension():
    """Over real HTTP, not `to_dict()`. A `response_model` filters: a dimension
    added to the spec but forgotten on `HitboxOut` would vanish here and the client
    would silently keep whatever it had."""
    body = _client().get("/api/hassault/hitbox").json()
    for key in hitbox.DEFAULT.to_dict():
        assert key in body, f"{key} was dropped by the response model"
    assert body["specId"] == hitbox.DEFAULT.spec_id
    assert body["overridden"] is False


def test_tuning_over_http_moves_the_body_and_says_it_is_overridden():
    client = _client()
    tuned = client.put("/api/hassault/hitbox", json={"radius": 1.4}).json()
    assert tuned["radius"] == pytest.approx(1.4)
    assert tuned["overridden"] is True
    assert tuned["specId"] != hitbox.DEFAULT.spec_id
    # The derived heights are served, so they must follow the primitives.
    assert tuned["crouchScale"] == pytest.approx(hitbox.current().crouch_scale)

    back = client.put("/api/hassault/hitbox", json={"reset": True}).json()
    assert back["overridden"] is False
    assert back["specId"] == hitbox.DEFAULT.spec_id


def test_a_zero_is_a_value_not_an_omission():
    """`0` head band means "no headshots" — a legitimate thing to try. Resolved
    with `is None`, so it must not read as "leave it alone"."""
    tuned = _client().put("/api/hassault/hitbox", json={"headBand": 0.0}).json()
    assert tuned["headBand"] == pytest.approx(0.0)


def test_the_lore_route_serves_both_factions_and_the_map_briefs():
    body = _client().get("/api/hassault/lore").json()
    assert [f["short"] for f in body["factions"]] == ["ARC", "HALON"]
    assert body["teamFactions"] == ["arc", "halon"]
    # Every map this repo ships has a brief; one read out of somebody's install
    # deliberately does not.
    for name in ("hd_pit", "hd_crossing", "hd_atrium"):
        assert body["mapBriefs"][name]["site"]


def test_rank_names_cover_every_ladder_tier():
    """The ladder is the game server's. If it grows a tier and this does not, the
    fallback shows a raw id rather than hiding a rated player — but the fixture
    should catch the drift first."""
    from backend.games_server.store import TIERS

    from backend.modules.hassault import lore

    for tier, _floor in TIERS:
        assert tier in lore.RANKS, f"ladder tier {tier!r} has no HorribleAssault rank"

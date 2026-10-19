"""The accuracy model: `weapons.shot_cone`, and the fixture both clients replay.

What a CS-style model promises, each as an assertion: stopping is what buys a
shot, a jump-shot is a gamble, crouching steadies, a tap beats a spam, and a
weapon nobody tuned fires exactly as it did before the model existed.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from backend.modules.hassault import weapons
from backend.modules.hassault.physics import MOVE_SPEED
from backend.modules.hassault.weapons import ShotContext, shot_cone

VECTORS = "packages/core/src/modules/hassault/__tests__/accuracy-vectors.json"

RIFLE = weapons.WEAPON_BY_ID["assault"]
PISTOL = weapons.WEAPON_BY_ID["pistol"]
SNIPER = weapons.WEAPON_BY_ID["sniper"]


def test_the_committed_fixture_is_current():
    from scripts.gen_hassault_accuracy_vectors import payload, weapons_spec_id

    with open(VECTORS, encoding="utf-8") as fh:
        committed = json.load(fh)
    assert committed["weaponsSpecId"] == weapons_spec_id(), (
        "the weapon table changed and accuracy-vectors.json did not: "
        "PYTHONPATH=. uv run python scripts/gen_hassault_accuracy_vectors.py"
    )
    assert committed == json.loads(json.dumps(payload()))


def test_an_untuned_weapon_fires_its_old_cone():
    plain = replace(
        RIFLE,
        first_shot_spread=None,
        move_inaccuracy=0.0,
        air_inaccuracy=0.0,
        crouch_scale=1.0,
        per_shot_inaccuracy=0.0,
    )
    for ctx in (
        ShotContext(),
        ShotContext(speed=MOVE_SPEED, airborne=True, crouch=1.0, since_last_shot=0.0),
    ):
        assert shot_cone(plain, ctx) == weapons.residual_spread(plain, ctx.scoped)


@pytest.mark.parametrize("weapon", [RIFLE, PISTOL, SNIPER], ids=lambda w: w.id)
def test_running_costs_and_stopping_buys(weapon):
    still = shot_cone(weapon, ShotContext(scoped=1))
    walking = shot_cone(weapon, ShotContext(scoped=1, speed=MOVE_SPEED * 0.52))
    running = shot_cone(weapon, ShotContext(scoped=1, speed=MOVE_SPEED))
    assert still < walking < running


def test_under_the_threshold_movement_is_free():
    at = MOVE_SPEED * weapons.ACCURATE_SPEED_SCALE
    assert shot_cone(RIFLE, ShotContext(speed=at)) == shot_cone(RIFLE, ShotContext())


def test_a_jump_shot_is_worse_than_a_running_one():
    running = shot_cone(RIFLE, ShotContext(speed=MOVE_SPEED))
    jumping = shot_cone(RIFLE, ShotContext(speed=MOVE_SPEED, airborne=True))
    assert jumping > running + 0.1


def test_crouching_steadies_the_rifle():
    assert shot_cone(RIFLE, ShotContext(crouch=1.0)) < shot_cone(RIFLE, ShotContext())


def test_a_tap_beats_a_spam_and_the_bloom_fades():
    tap = shot_cone(PISTOL, ShotContext())
    spam = shot_cone(PISTOL, ShotContext(since_last_shot=0.0))
    half = shot_cone(PISTOL, ShotContext(since_last_shot=PISTOL.recovery_time / 2))
    assert tap < half < spam
    assert shot_cone(PISTOL, ShotContext(since_last_shot=PISTOL.recovery_time)) == tap


def test_a_scoped_sniper_on_the_move_is_a_miss():
    still = shot_cone(SNIPER, ShotContext(scoped=1))
    moving = shot_cone(SNIPER, ShotContext(scoped=1, speed=MOVE_SPEED))
    # At 0.002 a still scoped shot is near-perfect; running adds forty times it.
    assert moving > still * 20


def test_the_weapons_route_serves_every_accuracy_field():
    """`WeaponOut` drops undeclared fields; this is the HTTP response, not the dict."""
    from fastapi.testclient import TestClient

    from backend.app import app

    served = {w["id"]: w for w in TestClient(app).get("/api/hassault/weapons").json()}
    for weapon in weapons.WEAPONS:
        want = weapon.to_dict()
        for key in (
            "firstShotSpread",
            "moveInaccuracy",
            "airInaccuracy",
            "crouchScale",
            "perShotInaccuracy",
            "recoveryTime",
        ):
            assert served[weapon.id][key] == want[key], (weapon.id, key)

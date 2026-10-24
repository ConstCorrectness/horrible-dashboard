"""Retake placement, and the `physics.place_body` helper it goes through.

A retake puts its attackers down directly on a site rather than walking them
there, so nothing on the way checks that a body fits. The vertical resolve in
`step` then lifts a body onto the highest floor under its 2.2-cube footprint —
the warehouse roof at 27 on hd_assault's B, under a 24 ceiling, where the fifth
attacker of five stayed wedged for the round. These pin the invariant that
stops that for every bundled site: placed bodies stand, on the floor `step`
itself would resolve them to.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from backend.modules.hassault import mapsource, physics
from backend.modules.hassault.modes import objectives, retake
from backend.modules.hassault.physics import MoveInput, PlayerState


def _world(name: str):
    cmap = mapsource.load_bundled(name)
    world = physics.World.from_map(cmap)
    return world, objectives.place(world, cmap).sites


def _cases():
    for name in mapsource.bundled_names():
        _world_, sites = _world(name)
        for site in sites:
            for count in range(1, 6):
                yield pytest.param(name, site.id, count, id=f"{name}-{site.id}-{count}")


def _place(name: str, site_id: str, count: int):
    world, sites = _world(name)
    site = next(s for s in sites if s.id == site_id)
    mode = retake.Retake()
    attackers = mode.state.attackers
    players = {
        str(i): SimpleNamespace(team=attackers, alive=True, state=PlayerState())
        for i in range(count)
    }
    room = SimpleNamespace(world=world, players=players)
    mode._place_attackers(room, site)
    return world, site, [p.state for p in players.values()]


def _assert_stands(world, body: PlayerState) -> None:
    floor, _ceil, enclosed = physics._support(world, body.x, body.y)
    assert not enclosed
    assert body.z == floor, f"z {body.z} is not the support floor {floor}"
    assert physics.can_stand(world, body.x, body.y, body.z)


@pytest.mark.parametrize(("name", "site_id", "count"), list(_cases()))
def test_every_placed_attacker_stands_on_its_support_floor(name, site_id, count):
    world, site, bodies = _place(name, site_id, count)
    for body in bodies:
        _assert_stands(world, body)
        # On the site's floor, not a ledge beside it.
        centre = physics.place_body(world, site.x, site.y)
        assert abs(body.z - centre[2]) <= physics.STEP_HEIGHT


def test_hd_assault_b_with_five_does_not_lift_anyone_onto_the_roof():
    """The report: one of five attackers on B ended up on the warehouse roof and
    could not move. Placed, then stood still for a second — nobody climbs."""
    world, _site, bodies = _place("hd_assault", "B", 5)
    for body in bodies:
        z0 = body.z
        assert z0 < 27.0
        for _ in range(60):
            physics.step(world, body, MoveInput(dt=1 / 60), 1 / 60)
        assert body.z == pytest.approx(z0)
        assert body.on_ground


@pytest.mark.parametrize("name", mapsource.bundled_names())
def test_place_body_never_moves_a_body_more_than_its_reach(name):
    world, _sites = _world(name)
    n = world.ssize
    for i in range(1, 16):
        for j in range(1, 16):
            x, y = n * i / 16 + 0.37, n * j / 16 + 0.61
            px, py, pz = physics.place_body(world, x, y)
            assert math.hypot(px - x, py - y) <= physics.PLACE_REACH + 1e-9
            if physics.can_stand(world, px, py, pz):
                assert pz == physics._support(world, px, py)[0]


def test_place_body_leaves_a_body_that_already_fits_where_it_is():
    world = physics.flat_world(32, floor=3)
    assert physics.place_body(world, 10.25, 12.5) == (10.25, 12.5, 3.0)


def test_place_body_steps_out_of_a_wall():
    world = physics.flat_world(32)
    # Against the solid border: the footprint overlaps it, so it must move in.
    x, y, z = physics.place_body(world, 2.5, 16.0)
    assert x > 2.5 and z == 0.0
    assert physics.can_stand(world, x, y, z)
    assert math.hypot(x - 2.5, y - 16.0) <= physics.PLACE_REACH

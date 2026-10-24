"""Baked collision for the modelled maps, and the jump the lint allows on it."""

import json

import pytest

from backend.modules.hassault import maplint, mapsource, physics


def _ledge_landing(height: int) -> float:
    """Run at a ledge of `height`, jump, and report where the body ends up."""
    best = 0.0
    for jump_at in (0.30, 0.35, 0.40, 0.45, 0.50):
        source = {
            "sfactor": 6,
            "brushes": [
                {"op": "room", "rect": [2, 2, 40, 20], "floor": 0, "ceil": 30},
                {"op": "room", "rect": [25, 2, 17, 20], "floor": height, "ceil": 30},
            ],
        }
        world = physics.World.from_map(mapsource.build(source, name="ledge"))
        player = physics.PlayerState(x=10.5, y=10.5, z=0, on_ground=True)
        for i in range(240):
            t = i / 120
            move = physics.MoveInput(
                forward=1, jump=jump_at < t < jump_at + 0.05, dt=1 / 120
            )
            physics.step(world, player, move, 1 / 120)
        best = max(best, player.z)
    return best


def test_maplint_jump_climb():
    """`JUMP_CLIMB` is the highest ledge a running jump lands on, measured."""
    top = int(maplint.JUMP_CLIMB)
    assert _ledge_landing(top) == pytest.approx(top)
    assert _ledge_landing(top + 1) == pytest.approx(0.0)


GLB_MAPS = [
    name
    for name in mapsource.bundled_names()
    if json.loads(
        mapsource.source_path(name).read_text(encoding="utf-8")
    ).get("format")
    == "gltf"
]


@pytest.mark.parametrize("name", GLB_MAPS)
def test_modelled_maps_simulate_their_model(name):
    """A hosted match runs the JSON brushes, never the GLB. They must be the
    bake of the GLB — a hand-drawn sketch there is every prop walk-through online."""
    world = mapsource.load_bundled(name)
    assert world is not None and world.baked_collision


@pytest.mark.parametrize("name", GLB_MAPS)
def test_bake_is_of_the_current_model(name):
    """The grid was baked from the mesh the generator last exported.

    The generator writes the hash of its collision mesh as `collisionSource`, the
    bake the hash of what it read as `collisionBaked`. A map regenerated and not
    re-baked keeps the old grid under the new model — players walking into
    walls that moved, standing on floors that are gone — and nothing else
    notices. A bake from the shipped GLB (`--from-shipped`) fails here too: its
    floors move.
    """
    source = json.loads(mapsource.source_path(name).read_text(encoding="utf-8"))
    assert source.get("collisionSource"), "regenerate the map: no collisionSource"
    assert source.get("collisionBaked") == source["collisionSource"], (
        "re-bake: the grid is not the bake of the current model "
        "(tools/blender/bake_collision.py)"
    )

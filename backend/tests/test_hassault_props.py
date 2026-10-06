"""Natural prop placement for the modelled maps (`tools/blender/props.py`).

The planning half is plain Python, so it is tested here without Blender: what
matters is not what a crate looks like but where the placer will and will not put
one. A prop in a wall, in a doorway or on a spawn is a bug the map's playability
tests would only catch by luck; a prop that closes a passage is the one that
matters most.
"""

import importlib.util
import random
from collections import deque
from pathlib import Path

import pytest

_PROPS = Path(__file__).resolve().parents[2] / "tools" / "blender" / "props.py"
_spec = importlib.util.spec_from_file_location("hassault_props", _PROPS)
props = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(props)

WEIGHTS = {"crates": 4, "barrels": 3, "jars": 1, "sacks": 1}


def _hall(lane_axis=None):
    """A 40 x 30 m hall: a solid block in it, a banned disc, a wall dividing off a corridor."""
    plan = props.Plan(0.0, 0.0, 40.0, 30.0)
    plan.mark_rect(plan.solid, 0.0, 40.0, 0.0, 0.5)  # south wall
    plan.mark_rect(plan.solid, 0.0, 40.0, 29.5, 30.0)  # north wall
    plan.mark_rect(plan.solid, 0.0, 0.5, 0.0, 30.0)  # west wall
    plan.mark_rect(plan.solid, 39.5, 40.0, 0.0, 30.0)  # east wall
    plan.mark_rect(plan.solid, 15.0, 22.0, 12.0, 20.0)  # a building
    plan.mark_disc(plan.forbid, 8.0, 8.0, 4.0)  # a spawn
    plan.solve()

    def zone_of(x, y):
        return 1.0, WEIGHTS, lane_axis

    return plan, zone_of


def _placed(plan, seed=7, **kw):
    rng = random.Random(seed)
    plan_, zone_of = plan
    return props.dress(plan_, rng, zone_of, **kw)


def _signature(vignettes):
    return [
        (
            name,
            [(p.kind, round(p.x, 4), round(p.y, 4), round(p.yaw, 4)) for p in pieces],
        )
        for name, pieces in vignettes
    ]


def test_distance_is_zero_in_a_wall_and_grows_away_from_it():
    plan, _ = _hall()
    assert plan.d_at(10.0, 0.2) == 0.0
    assert plan.d_at(10.0, 1.0) == pytest.approx(0.75, abs=0.3)
    assert plan.d_at(10.0, 6.0) == pytest.approx(5.5, abs=0.4)


def test_the_normal_points_away_from_the_wall():
    plan, _ = _hall()
    nx, ny = plan.normal_at(10.0, 1.2)
    assert ny > 0.9 and abs(nx) < 0.3  # up, off the south wall


def test_a_hall_gets_dressed():
    placed = _placed(_hall())
    assert len(placed) > 10
    names = {name for name, _ in placed}
    assert {"crates", "barrels"} <= names


def test_no_piece_is_inside_a_wall_a_ban_or_another_piece():
    plan, zone_of = _hall()
    placed = props.dress(plan, random.Random(3), zone_of)
    seen = set()
    for _, pieces in placed:
        for p in pieces:
            if p.kind == "rubble":
                ix, iy = plan.cell(p.x, p.y)
                k = plan._i(ix, iy)
                assert not plan.solid[k] and not plan.forbid[k]
                continue
            for k in props._cells(plan, p):
                assert not plan.solid[k], f"{p.kind} in a wall"
                assert not plan.forbid[k], f"{p.kind} in a banned zone"
    # Two pieces of one vignette may share a cell only by stacking (z above 0).
    for _, pieces in placed:
        for p in pieces:
            if p.kind == "rubble" or p.z > 0:
                continue
            for k in props._cells(plan, p):
                assert k not in seen, "two ground pieces overlap"
                seen.add(k)


def test_placement_is_deterministic_for_a_seed_and_differs_between_seeds():
    a = _signature(_placed(_hall(), seed=11))
    b = _signature(_placed(_hall(), seed=11))
    c = _signature(_placed(_hall(), seed=12))
    assert a == b
    assert a != c


def test_rubble_is_below_any_eye():
    placed = _placed(_hall())
    rubble = [p for _, ps in placed for p in ps if p.kind == "rubble"]
    assert rubble
    # Standing eye is 1.5 m; a body can crouch to well under a metre.
    assert all(p.z + p.size[2] < 0.25 for p in rubble)


def test_cover_stands_only_in_a_lane():
    none = _placed(_hall(lane_axis=None))
    assert not [n for n, _ in none if n == "cover"]
    some = _placed(_hall(lane_axis=(0.0, 1.0)), lane_budget=4)
    covers = [n for n, _ in some if n == "cover"]
    assert 0 < len(covers) <= 4


def test_dressing_never_closes_a_passage():
    """Flood-fill the hall from one end to the other over what is solid or placed."""
    plan, zone_of = _hall(lane_axis=(1.0, 0.0))
    props.dress(plan, random.Random(5), zone_of, lane_budget=10)

    def open_cell(ix, iy):
        k = plan._i(ix, iy)
        return not (plan.solid[k] or plan.placed[k])

    start = plan.cell(2.0, 15.0)
    goal = plan.cell(38.0, 15.0)
    assert open_cell(*start) and open_cell(*goal)
    seen, queue = {start}, deque([start])
    while queue:
        ix, iy = queue.popleft()
        if (ix, iy) == goal:
            return
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = ix + dx, iy + dy
            if plan.inside(nx, ny) and (nx, ny) not in seen and open_cell(nx, ny):
                seen.add((nx, ny))
                queue.append((nx, ny))
    pytest.fail("the clutter closed the hall")


def test_a_passage_too_narrow_is_left_alone():
    """Two walls 1.5 m apart: nothing stands in a corridor you could not walk past it in."""
    plan = props.Plan(0.0, 0.0, 30.0, 12.0)
    plan.mark_rect(plan.solid, 0.0, 30.0, 0.0, 5.0)
    plan.mark_rect(plan.solid, 0.0, 30.0, 6.5, 12.0)
    plan.solve()
    placed = props.dress(plan, random.Random(1), lambda x, y: (1.0, WEIGHTS, None))
    assert placed == []


def test_rect_free_respects_walls_bans_and_the_plan_edge():
    plan, _ = _hall()
    assert plan.rect_free(25.0, 30.0, 5.0, 10.0)
    assert not plan.rect_free(14.0, 18.0, 14.0, 18.0)  # the building
    assert not plan.rect_free(6.0, 10.0, 6.0, 10.0)  # the spawn
    assert not plan.rect_free(38.0, 41.0, 5.0, 10.0)  # off the edge

"""The cut-and-spread layout shared by the modelled maps (`tools/blender/cutlayout.py`).

A map is cut along a few lines and the halves spread apart, so the maths has to
keep a few promises, whichever map is using it: a piece wholly on one side only
moves; one that crosses a cut grows by exactly the gap; one that ends on a cut
does not grow; a composite is rigid. The wrappers are tested with stand-ins for
the Blender primitives, so none of this needs Blender.
"""

import importlib.util
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parents[2] / "tools" / "blender"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _TOOLS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cutlayout = _load("cutlayout")

CUTS = {"x": [(10.0, 2.0), (30.0, 3.0)], "y": [(20.0, 4.0)]}


@pytest.fixture
def warper():
    return cutlayout.Warper(CUTS)


def test_a_point_moves_by_the_gaps_of_the_cuts_below_it():
    cuts = cutlayout.Cuts(CUTS)
    assert cuts.metres(5.0, "x") == 5.0
    assert cuts.metres(15.0, "x") == 17.0
    assert cuts.metres(35.0, "x") == 40.0
    assert cuts.metres(25.0, "y") == 29.0


def test_a_box_that_ends_on_a_cut_does_not_grow():
    cuts = cutlayout.Cuts(CUTS)
    # A lower edge on the cut belongs above it; an upper edge belongs below.
    assert cuts.metres(10.0, "x") == 12.0
    assert cuts.metres(10.0, "x", upper=True) == 10.0


def test_cubes_use_the_same_cuts_at_three_to_the_metre():
    cuts = cutlayout.Cuts(CUTS)
    assert cuts.cubes(45.0, 75.0) == (
        cuts.metres(15.0, "x") * 3,
        cuts.metres(25.0, "y") * 3,
    )


def test_the_total_growth_is_the_sum_of_the_gaps():
    cuts = cutlayout.Cuts(CUTS)
    assert cuts.total("x") == 5.0
    assert cuts.total("y") == 4.0


def _boxes(warper):
    made = []

    def raw(collection, name, center, size, material):
        made.append((name, center, size))

    return warper.box(raw), made


def test_a_box_wholly_on_one_side_only_moves(warper):
    box, made = _boxes(warper)
    box(None, "crate", (40.0, 5.0, 0.5), (2.0, 2.0, 1.0), None)
    (_, center, size) = made[0]
    # Both cuts below it (x 10 and 30) move it by 2 + 3.
    assert center == (45.0, 5.0, 0.5)
    assert size == (2.0, 2.0, 1.0)  # rigid, height untouched


def test_a_box_across_a_cut_grows_by_exactly_the_gap(warper):
    box, made = _boxes(warper)
    # A wall from x 5 to 15 crosses the cut at 10: its length grows by 2.
    box(None, "wall", (10.0, 5.0, 2.0), (10.0, 1.0, 4.0), None)
    (_, center, size) = made[0]
    assert size == (12.0, 1.0, 4.0)
    assert center[0] - size[0] / 2 == 5.0
    assert center[0] + size[0] / 2 == 17.0
    assert size[2] == 4.0 and center[2] == 2.0


def test_a_box_that_ends_on_a_cut_keeps_its_size(warper):
    box, made = _boxes(warper)
    box(
        None, "slab", (5.0, 5.0, 0.5), (10.0, 2.0, 1.0), None
    )  # x 0 to 10, ends on the cut
    assert made[0][2] == (10.0, 2.0, 1.0)


def test_a_composite_is_rigid_and_moves_by_its_anchor(warper):
    placed = []

    def build(collection, name, pos, extra):
        placed.append((name, pos, extra))

    wrapped = warper.composite(build, 2)
    wrapped(None, "palm", (35.0, 25.0, 0.0), "kept")
    assert placed[0] == ("palm", (40.0, 29.0, 0.0), "kept")


def test_parts_placed_inside_a_composite_are_not_warped_again(warper):
    seen = []

    def raw(collection, name, center, size, material):
        seen.append(center)

    box = warper.box(raw)

    def build(collection, name, pos):
        box(collection, "part", pos, (1.0, 1.0, 1.0), None)

    warper.composite(build, 2)(None, "thing", (35.0, 25.0, 0.0))
    # Warped once, by the composite's anchor: (40, 29), not (45, 33).
    assert seen[0][:2] == (40.0, 29.0)


def test_raw_passes_everything_through_untouched(warper):
    box, made = _boxes(warper)
    with warper.raw():
        box(None, "already_placed", (35.0, 25.0, 0.0), (1.0, 1.0, 1.0), None)
    assert made[0][1] == (35.0, 25.0, 0.0)
    assert warper.active


def test_a_line_helper_is_laid_out_between_its_moved_ends(warper):
    got = []

    def battlements(collection, name, start, end, height, mats):
        got.append((start, end))

    warper.line(battlements, 2, 3)(
        None, "crenel", (5.0, 5.0, 10.0), (35.0, 5.0, 10.0), 0.8, None
    )
    assert got[0] == ((5.0, 5.0, 10.0), (40.0, 5.0, 10.0))


def test_stairs_are_rigid_but_recorded_with_their_real_footprint(warper):
    made = []

    def raw(collection, name, start, width, rise, run, steps, axis, material):
        made.append(start)

    warper.stairs(raw)(None, "flight", (35.0, 5.0, 0.0), 4.0, 2.8, 7.0, 9, "+y", None)
    assert made[0][:2] == (40.0, 5.0)
    kind, name, xr, yr, zr = warper.scan[0]
    assert kind == "stairs"
    assert yr == (
        5.0,
        12.0,
    )  # seven metres of run, so the scan can see a cut through it


def test_the_scan_records_footprints_before_they_are_warped(warper):
    box, _ = _boxes(warper)
    box(None, "wall", (10.0, 5.0, 2.0), (10.0, 1.0, 4.0), None)
    kind, name, xr, yr, zr = warper.scan[0]
    assert (kind, name, xr, zr) == ("box", "wall", (5.0, 15.0), (0.0, 4.0))


def test_a_rect_in_the_authored_frame_comes_out_stretched(warper):
    x0, y0, x1, y1 = warper.rect(5.0, 15.0, 35.0, 25.0)
    assert (x0, x1) == (5.0, 40.0)
    assert (y0, y1) == (15.0, 29.0)

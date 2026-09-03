"""Objective authoring: flag entities, the `objectives` source block, and lint.

The two halves are authored differently on purpose and the reasons are in
`modes/objectives.py`. What is pinned here is the behaviour that would otherwise
fail quietly: a flag's yaw, a map that cannot host the mode it was asked for, and
a lint rule that only applies where the map says it should.
"""

from __future__ import annotations

import pytest

from backend.modules.hassault import cgz, maplint, mapsource, physics
from backend.modules.hassault.modes import objectives

BUNDLED = ("hd_atrium", "hd_crossing", "hd_pit")


def built(name: str):
    cmap = mapsource.load_bundled(name)
    return cmap, physics.World.from_map(cmap)


# ---------------------------------------------------------------------------
# Authoring
# ---------------------------------------------------------------------------


def test_a_flags_yaw_survives_the_source_document():
    """The silent failure the typed `ctf_flag` branch exists to fix.

    Without it a flag falls through `_build_entity`'s raw-attrs path, where
    `ANGLED_TYPES` decodes `attrs[0]` as **tenths of a degree** — so `yaw: 45`
    round-trips as 4.5. Nothing errors; the flag just faces the wrong way, which
    is not something a test of "does the map build" would ever notice.
    """
    for name in BUNDLED:
        cmap, world = built(name)
        placed = objectives.place(world, cmap)
        assert placed.flags, f"{name} has no flags"
        for flag in placed.flags:
            # Every bundled flag is authored facing across the map, and a decode
            # error would land these at 9.0 and 27.0.
            assert flag.yaw in (90.0, 270.0), f"{name} flag yaw came back {flag.yaw}"


def test_a_flag_carries_its_team_the_way_a_spawn_does():
    cmap, world = built("hd_atrium")
    placed = objectives.place(world, cmap)
    assert sorted(f.team for f in placed.flags) == [0, 1]


def test_an_objective_sits_on_the_floor_not_at_the_mappers_eye():
    """The mapper's-eye problem every placement in this module has: an entity's
    `z` is where the editor's camera was, and the editor flies."""
    for name in BUNDLED:
        cmap, world = built(name)
        placed = objectives.place(world, cmap)
        for flag in placed.flags:
            assert physics.can_stand(world, flag.x, flag.y, flag.z), (
                f"{name}: a flag is not standable where it was placed"
            )
        for site in placed.sites:
            assert physics.can_stand(world, site.x, site.y, site.z), (
                f"{name}: site {site.id} is not standable"
            )


def test_a_site_id_is_short_and_server_chosen():
    """Site ids end up inside the snapshot's mode blob, and anything a client
    could influence there risks colliding with the template's sentinels — which
    does not crash, it silently turns the fragmentation off."""
    for bad in ("", "LONGNAME", "A B", "!!"):
        with pytest.raises(cgz.CgzError):
            mapsource.build(
                {
                    "sfactor": 6,
                    "brushes": [
                        {"op": "room", "rect": [4, 4, 40, 40], "floor": 0, "ceil": 12}
                    ],
                    "entities": [{"type": "playerstart", "x": 10, "y": 10}],
                    "objectives": {"sites": [{"id": bad, "x": 20, "y": 20}]},
                },
                "t",
            )


def test_a_site_outside_the_map_is_refused_at_build_time():
    with pytest.raises(cgz.CgzError):
        mapsource.build(
            {
                "sfactor": 6,
                "brushes": [
                    {"op": "room", "rect": [4, 4, 40, 40], "floor": 0, "ceil": 12}
                ],
                "entities": [{"type": "playerstart", "x": 10, "y": 10}],
                "objectives": {"sites": [{"id": "A", "x": 900, "y": 20}]},
            },
            "t",
        )


def test_a_cgz_only_map_declares_no_modes_and_no_objectives():
    """Bomb sites live in the *source* document, so a map read from a real `.cgz`
    has none — and that is the honest answer, not a gap. A community map has no
    idea what a bomb site is."""
    cmap = cgz.CgzMap(
        name="x",
        magic="ACMP",
        version=10,
        header_size=980,
        sfactor=6,
        title="x",
        waterlevel=-100.0,
        watercolor=(0, 0, 0, 0),
        maprevision=1,
        ambient=40,
        flags=0,
        timestamp=0,
    )
    assert cmap.modes == []
    assert cmap.objectives == {}


# ---------------------------------------------------------------------------
# Refusing a room that could never be finished
# ---------------------------------------------------------------------------


def test_defuse_on_a_map_with_no_sites_raises_rather_than_opening():
    """Every degraded form of this is worse than a refusal: the bomb can never be
    planted, every round timer expires, and nothing says why."""
    with pytest.raises(ValueError, match="no bomb sites"):
        objectives.check_playable("defuse", objectives.Objectives())


def test_ctf_needs_exactly_one_flag_per_team():
    one = objectives.Objectives(flags=[objectives.Flag(0, 1.0, 1.0, 0.0, 0.0)])
    with pytest.raises(ValueError, match="one ctf_flag entity per team"):
        objectives.check_playable("ctf", one)

    both = objectives.Objectives(
        flags=[
            objectives.Flag(0, 1.0, 1.0, 0.0, 0.0),
            objectives.Flag(1, 9.0, 9.0, 0.0, 0.0),
        ]
    )
    objectives.check_playable("ctf", both)


def test_deathmatch_needs_nothing_from_a_map():
    objectives.check_playable("dm", objectives.Objectives())
    objectives.check_playable("tdm", objectives.Objectives())


# ---------------------------------------------------------------------------
# Lint
# ---------------------------------------------------------------------------


def test_every_bundled_map_lints_clean_for_the_modes_it_declares():
    """The acceptance criterion for the content, and the reason the rules are
    worth having: they check the map, not the loader."""
    for name in BUNDLED:
        cmap, _ = built(name)
        errors = [f for f in maplint.lint(cmap) if f.severity == "error"]
        assert not errors, f"{name}: {[(f.code, f.message) for f in errors]}"


def test_every_bundled_map_declares_the_modes_it_can_actually_host():
    """A map claiming defuse without sites, or CTF without two flags, would open
    a room nobody can finish — `check_playable` is the runtime guard and this is
    the one that catches it before anyone tries."""
    for name in BUNDLED:
        cmap, world = built(name)
        placed = objectives.place(world, cmap)
        assert cmap.modes, f"{name} declares no modes"
        for mode_id in cmap.modes:
            objectives.check_playable(mode_id, placed)


def test_a_mode_rule_does_not_fire_on_a_map_that_never_claimed_the_mode():
    """Every objective rule is conditional on `cmap.modes`. A deathmatch map
    failing "no bomb sites" would make the whole lint advisory, and an advisory
    lint is one nobody reads."""
    source = {
        "sfactor": 6,
        "brushes": [{"op": "room", "rect": [4, 4, 40, 40], "floor": 0, "ceil": 12}],
        "entities": [
            {"type": "playerstart", "x": 10, "y": 10, "team": 0},
            {"type": "playerstart", "x": 30, "y": 30, "team": 1},
        ],
    }
    plain = mapsource.build(source, "plain")
    codes = {f.code for f in maplint.lint(plain)}
    assert not {c for c in codes if c.startswith(("site.", "flag."))}

    claims_defuse = mapsource.build({**source, "modes": ["defuse"]}, "d")
    assert "site.none" in {f.code for f in maplint.lint(claims_defuse)}


def test_a_site_on_top_of_a_spawn_is_refused():
    """Planting where the defenders arrive is not a round."""
    built_map = mapsource.build(
        {
            "sfactor": 6,
            "modes": ["defuse"],
            "brushes": [{"op": "room", "rect": [4, 4, 56, 56], "floor": 0, "ceil": 12}],
            "entities": [
                {"type": "playerstart", "x": 20, "y": 20, "team": 0},
                {"type": "playerstart", "x": 40, "y": 40, "team": 1},
            ],
            "objectives": {"sites": [{"id": "A", "x": 21, "y": 20}]},
        },
        "onspawn",
    )
    codes = {f.code for f in maplint.lint(built_map)}
    assert "site.near_spawn" in codes


def test_uneven_team_spawns_are_flagged_for_a_mode_with_sides():
    built_map = mapsource.build(
        {
            "sfactor": 6,
            "modes": ["tdm"],
            "brushes": [{"op": "room", "rect": [4, 4, 56, 56], "floor": 0, "ceil": 12}],
            "entities": [
                {"type": "playerstart", "x": 10, "y": 10, "team": 0},
                {"type": "playerstart", "x": 12, "y": 20, "team": 0},
                {"type": "playerstart", "x": 14, "y": 30, "team": 0},
                {"type": "playerstart", "x": 40, "y": 40, "team": 1},
            ],
        },
        "lopsided",
    )
    codes = {f.code for f in maplint.lint(built_map)}
    assert "spawn.unbalanced" in codes

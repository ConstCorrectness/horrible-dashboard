"""The map designer's backend: drafts, edits, undo, lint and save.

Three things are pinned here, and the first is the load-bearing one.

**A draft is the same map.** A draft seeded from `hd_pit` has to compile to bytes
indistinguishable from `hd_pit` itself, through the same routes. The whole design
rests on `assets.load_map` resolving `draft:<id>`, so that all three clients read
an unsaved document through the map path they already have — and the moment those
two paths disagree, the editor is showing you something the server will not serve.

**Every edit inverts.** The ops are closed under inversion: the undo of one is
another of the same eight, replayed through the same code that made it. So the
test is not "does undo work" but "does applying every op and then undoing them all
land exactly where it started" — byte-for-byte on the document.

**A refused edit changes nothing.** A client re-fetches the map after each edit,
so a document left half-mutated in a state that will not build is not a draft with
a problem in it; it is a client with nothing to draw.
"""

from __future__ import annotations

import asyncio
import copy
import json

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.hassault import assets, drafts, maplint, mapsource, textures
from backend.modules.hassault.cgz import PLANE_ORDER, CgzError

API = "/api/hassault"
SEED = "hd_pit"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def draft():
    made = drafts.create(SEED)
    yield made
    drafts.close(made.id)


@pytest.fixture
def sandbox_maps(tmp_path, monkeypatch):
    """A maps directory saving can write into without touching the real one."""
    for name in mapsource.bundled_names():
        source = mapsource.MAPS_DIR / f"{name}.json"
        (tmp_path / f"{name}.json").write_text(
            source.read_text(encoding="utf-8"), encoding="utf-8"
        )
    monkeypatch.setattr(mapsource, "MAPS_DIR", tmp_path)
    mapsource.load_bundled.cache_clear()
    mapsource.bundled_names.cache_clear()
    yield tmp_path
    mapsource.load_bundled.cache_clear()
    mapsource.bundled_names.cache_clear()


# ---- a draft is the same map ------------------------------------------------------


def test_a_seeded_draft_is_byte_identical_to_the_map_it_came_from(draft):
    """The proof the whole design rests on.

    `mapsource.build` is the only compiler, and a draft runs the same document
    through it. If these ever differ, the editor is rendering something other than
    what the match server would load, and nothing else in this suite would notice.
    """
    original = mapsource.load_bundled(SEED)
    built = drafts.compiled(draft.id)
    for plane in PLANE_ORDER:
        assert getattr(built, plane) == getattr(original, plane), plane
    assert len(built.entities) == len(original.entities)
    assert built.sfactor == original.sfactor
    assert built.waterlevel == original.waterlevel


def test_load_map_resolves_a_draft_by_name(draft):
    """The one function that had to change for any of this to work: every map
    route and all three clients' boot paths go through it."""
    assert assets.load_map(drafts.PREFIX + draft.id) is not None
    with pytest.raises(drafts.DraftError):
        assets.load_map(drafts.PREFIX + "nosuchdraft")


def test_a_draft_never_reaches_the_filesystem(draft):
    """`find_map`'s path validation is untouched by drafts, because a draft is
    never a path. A draft id that looks like a traversal is simply not a draft."""
    assert assets.find_map(drafts.PREFIX + draft.id) is None
    with pytest.raises(drafts.DraftError):
        assets.load_map(drafts.PREFIX + "../../etc/passwd")


def test_a_blank_draft_builds():
    """An empty document is solid rock, which is a legal map and a sensible place
    to start from — it is just not a playable one, and the lint says so."""
    made = drafts.create()
    try:
        built = drafts.compiled(made.id)
        assert built.type.count(0) == built.cubic_size
        assert {f.code for f in drafts.lint(made.id)} >= {"spawn.none"}
    finally:
        drafts.close(made.id)


# ---- edits invert -----------------------------------------------------------------

EVERY_OP = [
    {"op": "brush.add", "brush": {"op": "solid", "rect": [20, 20, 3, 3], "wtex": 12}},
    {"op": "brush.add", "index": 0, "brush": {"op": "room", "rect": [8, 8, 6, 6]}},
    {"op": "brush.update", "index": 1, "patch": {"ceil": 15}},
    {"op": "brush.replace", "index": 2, "brush": {"op": "solid", "rect": [9, 9, 2, 2]}},
    {"op": "brush.reorder", "from": 0, "to": 3},
    {"op": "brush.remove", "index": 0},
    {"op": "ent.add", "entity": {"type": "health", "x": 30, "y": 30, "z": 0}},
    {"op": "ent.update", "index": 0, "patch": {"yaw": 90}},
    {"op": "ent.replace", "index": 1, "entity": {"type": "ammo", "x": 12, "y": 12}},
    {"op": "ent.remove", "index": 2},
    {"op": "map.set", "key": "title", "value": "Edited"},
    {"op": "map.set", "key": "ambient", "value": 90},
]


def test_applying_every_op_and_undoing_it_all_restores_the_document(draft):
    """The closure property, end to end.

    Every op's inverse is another op, replayed through `_apply_one` — so there is
    no separate backwards code path that can disagree with the forwards one. This
    is what that buys, and it is checked on the whole document rather than on the
    field each edit touched, because the failure worth catching is an inverse that
    is *nearly* right.
    """
    start = copy.deepcopy(draft.doc)
    for edit in EVERY_OP:
        drafts.apply(draft.id, edit)
    assert draft.doc != start
    assert draft.revision == len(EVERY_OP)

    for _ in EVERY_OP:
        drafts.undo(draft.id)
    assert draft.doc == start


def test_redo_replays_what_undo_took_back(draft):
    for edit in EVERY_OP:
        drafts.apply(draft.id, edit)
    after = copy.deepcopy(draft.doc)
    for _ in EVERY_OP:
        drafts.undo(draft.id)
    for _ in EVERY_OP:
        drafts.redo(draft.id)
    assert draft.doc == after


def test_a_new_edit_discards_the_redo_branch(draft):
    """Redo leads back to a state this document has been in. A fresh edit means it
    has not been in that state, and offering it anyway would replay an edit against
    indices that have moved."""
    drafts.apply(draft.id, {"op": "map.set", "key": "title", "value": "One"})
    drafts.undo(draft.id)
    assert draft.redo
    drafts.apply(draft.id, {"op": "map.set", "key": "title", "value": "Two"})
    assert not draft.redo


def test_undo_and_redo_refuse_when_there_is_nothing_to_do(draft):
    with pytest.raises(drafts.DraftError, match="nothing to undo"):
        drafts.undo(draft.id)
    with pytest.raises(drafts.DraftError, match="nothing to redo"):
        drafts.redo(draft.id)


def test_history_is_bounded(draft):
    for n in range(drafts.MAX_HISTORY + 20):
        drafts.apply(draft.id, {"op": "map.set", "key": "title", "value": f"t{n}"})
    assert len(draft.undo) == drafts.MAX_HISTORY


def test_an_update_with_a_null_clears_a_field_and_undo_puts_it_back(draft):
    """A null clears rather than sets, so a brush can go back to its default —
    which is not the same as pinning it to whatever that default is today. The
    inverse is a whole-object replace for exactly this case: there is no patch
    that says "put the key back"."""
    drafts.apply(draft.id, {"op": "brush.update", "index": 0, "patch": {"ceil": 12}})
    assert draft.doc["brushes"][0]["ceil"] == 12
    drafts.apply(draft.id, {"op": "brush.update", "index": 0, "patch": {"ceil": None}})
    assert "ceil" not in draft.doc["brushes"][0]
    drafts.undo(draft.id)
    assert draft.doc["brushes"][0]["ceil"] == 12


# ---- a refused edit changes nothing -----------------------------------------------

REFUSED = [
    ({"op": "brush.add", "brush": {"op": "room", "rect": [0, 0, 64, 64]}}, "border"),
    (
        {
            "op": "brush.add",
            "brush": {"op": "room", "rect": [4, 4, 8, 8], "floor": 9, "ceil": 3},
        },
        "ceiling",
    ),
    (
        {"op": "brush.add", "brush": {"op": "nonsense", "rect": [4, 4, 4, 4]}},
        "unknown op",
    ),
    ({"op": "brush.add", "brush": "not an object"}, "must be an object"),
    ({"op": "brush.update", "index": 9999, "patch": {}}, "no brush at index"),
    ({"op": "brush.update", "index": "one", "patch": {}}, "must be an integer"),
    ({"op": "ent.remove", "index": -1}, "no entity at index"),
    ({"op": "map.set", "key": "brushes", "value": []}, "not a settable"),
    ({"op": "map.set", "key": "sfactor", "value": 99}, "sfactor"),
    ({"op": "wat"}, "unknown edit op"),
]


@pytest.mark.parametrize("edit,expected", REFUSED)
def test_a_refused_edit_leaves_the_document_exactly_as_it_was(draft, edit, expected):
    before = copy.deepcopy(draft.doc)
    revision = draft.revision
    with pytest.raises((drafts.DraftError, CgzError), match=expected):
        drafts.apply(draft.id, edit)
    assert draft.doc == before
    assert draft.revision == revision
    assert not draft.undo, "a refused edit must not enter the history"


def test_map_set_only_reaches_the_fields_it_declares(draft):
    """`brushes` and `entities` are structural: routing them through `map.set`
    would sidestep the ops that know how to invert a structural change."""
    assert "brushes" not in drafts.SETTABLE and "entities" not in drafts.SETTABLE


# ---- lint -------------------------------------------------------------------------


def test_the_bundled_maps_lint_clean():
    """The bar the editor holds itself to is the one these three already clear."""
    for name in mapsource.bundled_names():
        assert maplint.lint(mapsource.load_bundled(name)) == []


def test_sealing_the_map_is_reported_with_the_cells_it_cut_off(draft):
    """The finding this whole module exists for.

    A slab across a corridor is invisible in a brush list and obvious on a floor
    painted red, which is why a finding carries cells rather than only a count.
    """
    drafts.apply(
        draft.id,
        {"op": "brush.add", "brush": {"op": "solid", "rect": [4, 4, 56, 6]}},
    )
    found = {f.code: f for f in drafts.lint(draft.id)}
    assert "world.cutoff" in found
    cutoff = found["world.cutoff"]
    assert cutoff.severity == "error"
    assert cutoff.cells and cutoff.cell_count >= len(cutoff.cells)
    assert all(len(cell) == 2 for cell in cutoff.cells)


def test_flooding_the_map_is_reported(draft):
    drafts.apply(draft.id, {"op": "map.set", "key": "waterlevel", "value": 60})
    assert "water.floods" in {f.code for f in drafts.lint(draft.id)}


def test_lint_findings_are_capped_but_say_how_many_there_really_were(draft):
    drafts.apply(
        draft.id,
        {"op": "brush.add", "brush": {"op": "solid", "rect": [4, 4, 56, 6]}},
    )
    cutoff = next(f for f in drafts.lint(draft.id) if f.code == "world.cutoff")
    assert len(cutoff.cells) <= maplint._CELL_CAP
    assert cutoff.cell_count > len(cutoff.cells)


def test_errors_sort_before_warnings(draft):
    drafts.apply(
        draft.id,
        {"op": "brush.add", "brush": {"op": "solid", "rect": [4, 4, 56, 6]}},
    )
    severities = [f.severity for f in drafts.lint(draft.id)]
    assert severities == sorted(severities, key=lambda s: 0 if s == "error" else 1)


# ---- brush ownership --------------------------------------------------------------


def test_owners_names_the_brush_that_last_painted_each_cell(draft):
    """Brushes compose by overwrite, so ownership is only knowable while they are
    being applied. This is what turns a crosshair on a wall into a brush."""
    owned = drafts.owners(draft.id)
    assert len(owned) == drafts.compiled(draft.id).cubic_size
    assert max(owned) == len(draft.doc["brushes"]) - 1
    assert -1 in owned, "the untouched border belongs to no brush"

    # A brush painted last owns its cells, whatever was under it.
    drafts.apply(
        draft.id,
        {"op": "brush.add", "brush": {"op": "solid", "rect": [20, 20, 4, 4]}},
    )
    built = drafts.compiled(draft.id)
    owned = drafts.owners(draft.id)
    last = len(draft.doc["brushes"]) - 1
    assert owned[21 * built.ssize + 21] == last


def test_owners_is_not_a_cube_plane():
    """`plane_order` describes the map format, which has no such field, and all
    three clients check their cube payload length against it."""
    assert "owner" not in PLANE_ORDER and "owners" not in PLANE_ORDER


# ---- saving -----------------------------------------------------------------------


def test_saving_writes_a_reviewable_brush_list(draft, sandbox_maps):
    """The saved artifact is the document, not a `.cgz`.

    That is the whole reason this format is the source of truth: a committed
    binary cannot be reviewed or diffed, and a map here is a few dozen rectangles.
    """
    drafts.apply(draft.id, {"op": "map.set", "key": "title", "value": "Scratch"})
    name = drafts.save(draft.id, "scratch")
    assert name == "hd_scratch"

    written = json.loads((sandbox_maps / "hd_scratch.json").read_text(encoding="utf-8"))
    assert written["title"] == "Scratch"
    assert written["brushes"] == draft.doc["brushes"]
    assert isinstance(written["brushes"][0]["rect"], list)


def test_saving_makes_the_map_immediately_loadable(draft, sandbox_maps):
    """Both catalog caches are keyed by name with no mtime in them — bundled maps
    were never meant to change under a running process. They do now."""
    drafts.save(draft.id, "fresh")
    assert "hd_fresh" in mapsource.bundled_names()
    assert mapsource.load_bundled("hd_fresh") is not None
    assert assets.load_map("hd_fresh") is not None


def test_saving_refuses_to_clobber_without_being_told_to(draft, sandbox_maps):
    with pytest.raises(drafts.DraftError, match="already exists"):
        drafts.save(draft.id, SEED)
    drafts.apply(draft.id, {"op": "map.set", "key": "title", "value": "Replaced"})
    assert drafts.save(draft.id, SEED, overwrite=True) == SEED
    assert mapsource.load_bundled(SEED).title == "Replaced"


@pytest.mark.parametrize("name", ["../evil", "hd_a/b", "hd_a b", "sub/dir"])
def test_saving_refuses_a_name_that_is_not_a_name(draft, sandbox_maps, name):
    with pytest.raises(drafts.DraftError, match="not a usable map name"):
        drafts.save(draft.id, name)


def test_saving_adds_the_prefix_every_map_here_carries(draft, sandbox_maps):
    assert drafts.save(draft.id, "plain") == "hd_plain"
    assert drafts.save(draft.id, "hd_already", overwrite=True) == "hd_already"


def test_saving_leaves_no_temporary_behind(draft, sandbox_maps):
    drafts.save(draft.id, "tidy")
    assert not list(sandbox_maps.glob("*.tmp"))


# ---- the served schema ------------------------------------------------------------


def test_the_schema_describes_the_ops_that_actually_exist():
    """Served rather than written out again in TypeScript and Rust. The failure
    this guards is a form offering a field the compiler stopped accepting."""
    schema = mapsource.schema()
    assert {op["name"] for op in schema["brushes"]} == set(mapsource._OPS)
    assert schema["entity_names"] == list(mapsource.ENTITY_NAMES)
    for spec in schema["entities"]:
        assert spec["name"] in mapsource.ENTITY_NAMES


def test_every_field_the_schema_advertises_is_one_a_brush_accepts():
    """A field named here has to survive a build, or the picker offers a knob that
    does nothing."""
    rect = {"rect": [4, 4, 6, 6]}
    for spec in mapsource.schema()["brushes"]:
        brush = {"op": spec["name"], **rect}
        for field in spec["fields"]:
            if field["name"] == "rect" or field["default"] is None:
                continue
            brush[field["name"]] = field["default"]
        if spec["name"] == "stairs":
            brush["axis"] = "x"
        mapsource.build({"sfactor": 6, "brushes": [brush]}, name="hd_probe")


# ---- the texture palette ----------------------------------------------------------


def test_the_palette_matches_the_colour_both_renderers_already_draw():
    """The property that makes the palette additive: naming a slot must not change
    how it looks, so the catalogue's colours are read off the renderers' own hue
    step rather than chosen beside it."""
    assert textures.color_for(0) == "#a57373"
    assert textures.color_for(12) == "#73a58c"
    # id 255 is where an f32 implementation with a truncated constant would have
    # drifted away from the f64 one both clients actually use.
    assert textures.color_for(255) == "#7388a5"


def test_an_uncatalogued_slot_is_described_rather_than_refused():
    described = textures.describe(200)
    assert described["color"] == textures.color_for(200)
    assert described["group"] == "unnamed"


def test_every_bundled_map_paints_with_slots_the_palette_names():
    used = set()
    for name in mapsource.bundled_names():
        source = json.loads(
            (mapsource.MAPS_DIR / f"{name}.json").read_text(encoding="utf-8")
        )
        for brush in source["brushes"]:
            used.update(
                brush[k] for k in ("wtex", "ftex", "ctex", "utex") if k in brush
            )
    unnamed = {slot for slot in used if textures.get(slot) is None}
    assert not unnamed, f"bundled maps paint with unnamed slots {sorted(unnamed)}"


# ---- over HTTP --------------------------------------------------------------------
#
# Asserted against the response body, not the objects behind it: a response model
# silently filters any field it does not declare, so a served value that never
# reaches the browser looks perfectly fine from the Python side.


def test_the_designer_round_trips_over_http(client):
    made = client.post(f"{API}/maps/drafts", json={"from": SEED})
    assert made.status_code == 200
    body = made.json()
    draft_id, map_name = body["id"], body["mapName"]
    try:
        assert body["revision"] == 0 and body["lint"] == []

        # The identity proof, through the routes the clients actually use.
        assert (
            client.get(f"{API}/maps/{map_name}/cubes").content
            == client.get(f"{API}/maps/{SEED}/cubes").content
        )
        mine = client.get(f"{API}/maps/{map_name}").json()
        theirs = client.get(f"{API}/maps/{SEED}").json()
        assert mine["entities"] == theirs["entities"]
        assert mine["items"] == theirs["items"]
        assert mine["plane_order"] == theirs["plane_order"]

        edited = client.patch(
            f"{API}/maps/drafts/{draft_id}",
            json={"op": "brush.add", "brush": {"op": "solid", "rect": [4, 4, 56, 6]}},
        )
        assert edited.status_code == 200
        payload = edited.json()
        assert payload["revision"] == 1 and payload["canUndo"] is True
        cutoff = next(f for f in payload["lint"] if f["code"] == "world.cutoff")
        assert cutoff["cells"] and cutoff["cellCount"] > len(cutoff["cells"])

        assert client.post(f"{API}/maps/drafts/{draft_id}/undo").json()["revision"] == 2
        assert (
            client.post(f"{API}/maps/drafts/{draft_id}/redo").json()["canRedo"] is False
        )
    finally:
        assert client.delete(f"{API}/maps/drafts/{draft_id}").json() == {"closed": True}


def test_a_bad_edit_is_a_status_not_a_traceback(client, draft):
    bad = client.patch(
        f"{API}/maps/drafts/{draft.id}",
        json={"op": "brush.add", "brush": {"op": "room", "rect": [0, 0, 64, 64]}},
    )
    assert bad.status_code == 422 and "border" in bad.json()["detail"]
    assert (
        client.patch(
            f"{API}/maps/drafts/{draft.id}", json={"op": "map.set", "key": "nope"}
        ).status_code
        == 400
    )
    assert (
        client.patch(
            f"{API}/maps/drafts/nosuch",
            json={"op": "map.set", "key": "title", "value": "x"},
        ).status_code
        == 404
    )


def test_a_missing_draft_is_a_404_not_a_500(client):
    """`_load` has to translate a missing draft the way it translates a missing
    map — `DraftError` is not a `CgzError`, so without that branch this is a 500."""
    assert client.get(f"{API}/maps/draft:gone").status_code == 404
    assert client.get(f"{API}/maps/drafts/gone").status_code == 404
    assert client.get(f"{API}/maps/drafts/gone/lint").status_code == 404


def test_schema_is_not_swallowed_by_the_map_route(client):
    """`/maps/schema` and `/maps/{name}` are the same shape, and FastAPI matches in
    declaration order — so the designer routes have to come first."""
    served = client.get(f"{API}/maps/schema")
    assert served.status_code == 200
    assert {op["name"] for op in served.json()["brushes"]} == {
        "room",
        "solid",
        "stairs",
    }


def test_the_texture_palette_is_served(client):
    served = client.get(f"{API}/textures")
    assert served.status_code == 200
    rows = served.json()
    assert rows and all(row["color"].startswith("#") for row in rows)
    assert {row["pattern"] for row in rows} <= set(textures.PATTERNS)


def test_owners_is_served_as_uint16(client, draft):
    served = client.get(f"{API}/maps/drafts/{draft.id}/owners")
    assert served.status_code == 200
    cells = int(served.headers["X-Map-Cells"])
    assert len(served.content) == cells * 2
    assert served.headers["X-Map-Owner-None"] == "65535"
    first = int.from_bytes(served.content[:2], "little")
    assert first == 65535, "the border belongs to no brush"


def test_a_draft_can_be_exported_as_cgz(client, draft):
    """Exporting our own document is allowed where re-serving an install's
    copyright maps is not."""
    exported = client.get(f"{API}/maps/{drafts.PREFIX + draft.id}/download")
    assert exported.status_code == 200
    assert exported.content[:2] == b"\x1f\x8b"


# ---- the console half -------------------------------------------------------------
#
# The typed half of the editor. It matters more than it looks: `console.py` owns
# the command registry and `GET /console/definitions` serves it, so these commands
# are rendered by the native client *and* by the browser's existing console pane
# without either learning anything new.


@pytest.fixture
def console():
    from backend.modules.hassault import console as module

    registry = module.console_registry
    registry.active_draft = ""
    registry.selection = {}
    yield registry
    if registry.active_draft:
        drafts.close(registry.active_draft)
    registry.active_draft = ""
    registry.selection = {}


def run(registry, command: str):
    from backend.modules.hassault import console as module

    return asyncio.run(registry.execute(module.ConsoleExecRequest(command=command)))


def test_a_whole_playable_map_can_be_built_from_the_console(console, sandbox_maps):
    """The end-to-end shape of the feature, without a renderer.

    It also shows the lint doing its job as a *guide* rather than a gate: the map
    is full of errors while it is a room with nothing in it, and clean once it has
    somewhere to spawn and something to pick up.
    """
    assert run(console, "edit.new name:Arena").ok
    assert run(console, "edit.brush.add room 8 8 40 40 0 14").ok
    assert run(console, "edit.brush.add solid 20 20 4 4").ok

    findings = run(console, "edit.lint").result_data["findings"]
    assert any(f["code"] == "spawn.none" for f in findings)

    for x, y in [
        (12, 12),
        (44, 12),
        (12, 44),
        (44, 44),
        (28, 10),
        (28, 46),
        (10, 28),
        (46, 28),
    ]:
        assert run(console, f"edit.ent.add playerstart {x} {y}").ok
    for kind, x, y in [
        ("health", 16, 28),
        ("health", 40, 28),
        ("ammo", 28, 16),
        ("ammo", 28, 40),
        ("armour", 28, 28),
        ("clips", 18, 18),
        ("grenade", 38, 38),
        ("helmet", 18, 38),
    ]:
        assert run(console, f"edit.ent.add {kind} {x} {y}").ok

    assert run(console, "edit.lint").result_data["findings"] == []

    saved = run(console, "edit.save name:arena")
    assert saved.ok and saved.result_data["saved"] == "hd_arena"

    # The real proof: the saved document is a map the game can load and the
    # playability suite would accept.
    built = mapsource.load_bundled("hd_arena")
    assert built is not None
    assert maplint.lint(built) == []


def test_the_console_edits_the_same_draft_the_routes_do(console, client):
    """One editor with two front ends, not two editors."""
    run(console, f"edit.open map:{SEED}")
    draft_id = console.active_draft
    run(console, "edit.brush.add solid 20 20 4 4")

    served = client.get(f"{API}/maps/drafts/{draft_id}").json()
    assert served["revision"] == 1
    assert served["doc"]["brushes"][-1] == {"op": "solid", "rect": [20, 20, 4, 4]}

    client.post(f"{API}/maps/drafts/{draft_id}/undo")
    assert run(console, "edit.status").result_data["revision"] == 2


def test_selection_is_what_set_and_remove_act_on(console):
    run(console, f"edit.open map:{SEED}")
    run(console, "edit.select brush 0")
    assert console.selection["brush"] == 0
    run(console, "edit.brush.set ceil 18")
    draft = drafts.require(console.active_draft)
    assert draft.doc["brushes"][0]["ceil"] == 18


def test_adding_something_selects_it(console):
    """So `edit.ent.add light` then `edit.ent.set radius` means the light you just
    placed, which is the only reading anyone wants."""
    run(console, f"edit.open map:{SEED}")
    run(console, "edit.ent.add light 30 30")
    run(console, "edit.ent.set radius 120")
    draft = drafts.require(console.active_draft)
    assert draft.doc["entities"][-1] == {
        "type": "light",
        "x": 30,
        "y": 30,
        "radius": 120,
    }


def test_a_colour_can_be_typed_the_way_a_person_types_one(console):
    run(console, f"edit.open map:{SEED}")
    run(console, "edit.ent.add light 30 30")
    run(console, "edit.ent.set color 255,240,206")
    draft = drafts.require(console.active_draft)
    assert draft.doc["entities"][-1]["color"] == [255, 240, 206]


def test_commands_refuse_clearly_with_nothing_open(console):
    failed = run(console, "edit.brush.add room 8 8 8 8")
    assert not failed.ok and "edit.open" in failed.error


def test_an_unknown_entity_type_lists_the_ones_that_exist(console):
    run(console, f"edit.open map:{SEED}")
    failed = run(console, "edit.ent.add sofa 10 10")
    assert not failed.ok
    assert "playerstart" in failed.error and "mapmodel" in failed.error


def test_a_refused_command_does_not_move_the_document(console):
    run(console, f"edit.open map:{SEED}")
    draft = drafts.require(console.active_draft)
    before, revision = copy.deepcopy(draft.doc), draft.revision
    assert not run(console, "edit.brush.add room 0 0 64 64").ok
    assert draft.doc == before and draft.revision == revision


def test_playtest_loads_the_draft_into_a_match(console):
    """`server.map` for a draft, and it needs no special handling anywhere: the
    match server loads `draft:<id>` through the same `assets.load_map` as any
    other map."""
    from backend.modules.hassault import match

    run(console, f"edit.open map:{SEED}")
    result = run(console, "edit.playtest")
    assert result.ok
    room = match.match_server.rooms[result.result_data["room"]]
    try:
        assert room.map_name == drafts.PREFIX + console.active_draft
        assert room.spawns and room.items.placements()
    finally:
        match.match_server.rooms.pop(room.id, None)


def test_the_edit_commands_are_served_to_both_clients(client):
    """They are declared in the registry the definitions route serves, so the
    native client and the browser console render them without a client-side list
    that could offer a command this node has never heard of."""
    served = client.get(f"{API}/console/definitions").json()
    names = {c["name"] for c in served["commands"]}
    assert {"edit.open", "edit.brush.add", "edit.lint", "edit.save"} <= names
    add = next(c for c in served["commands"] if c["name"] == "edit.brush.add")
    assert {p["name"] for p in add["parameters"]} >= {"op", "x", "y", "w", "h"}


# ---- the console argument parser --------------------------------------------------


def test_a_positional_value_containing_a_colon_is_not_an_argument_name():
    """`draft:9f2c` is a map name. Reading it as an argument called `draft` is
    what made `server.map` silently lose its only parameter — and the old rule
    exempted exactly one prefix (`http`), which is the same bug with a shorter
    list. The parser knows the parameter names, so it asks."""
    from backend.modules.hassault import console as module

    registry = module.console_registry
    command = registry.commands["server.map"]
    parsed = registry._parse_command_args(command, ["draft:9f2c"])
    assert parsed == {"map_name": "draft:9f2c"}


def test_named_arguments_still_bind_by_name():
    from backend.modules.hassault import console as module

    registry = module.console_registry
    command = registry.commands["server.bots.add"]
    assert registry._parse_command_args(command, ["3", "skill:hard"]) == {
        "count": 3,
        "skill": "hard",
    }
    assert registry._parse_command_args(command, ["count:2"]) == {"count": 2}


def test_the_first_separator_wins():
    """`radius=1:2` is the key `radius`; splitting on the colon would invent a key
    of `radius=1`."""
    from backend.modules.hassault.console import _split_named

    assert _split_named("radius=1:2") == ("radius", "=", "1:2")
    assert _split_named("draft:9f2c") == ("draft", ":", "9f2c")
    assert _split_named("plain") == ("", "", "plain")

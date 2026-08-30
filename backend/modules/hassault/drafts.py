"""Maps being edited: the document, its history, and the compile behind it.

A draft is a map source document held in memory while somebody works on it. The
one idea the rest of this module falls out of: **a draft is addressed as a map
name.** `assets.load_map` is the single chokepoint every map route already goes
through, so a `draft:` branch there makes `GET /maps/{name}`,
`/maps/{name}/cubes` and `/maps/{name}/download` all work on a document that is
not on disk — with no new read routes, and with the native client's existing boot
path (`map_info` then `map_cubes` then `World::new`) unchanged. Every edit is
then just a re-fetch of a map, which is a thing all three clients already know
how to do.

**`mapsource.build` stays the only compiler.** It is a pure function over a plain
dict, so compiling an unsaved document costs nothing extra and needs no disk. The
alternative — a second compiler in the editor, or a port into the native client —
would fail the way this project's physics ports fail when they drift: not with an
error, but with an editor that shows you a different map from the one the server
will serve.

## Edits are typed, and that is what makes undo possible

A `PATCH` carries one operation, never a whole document, because **the edit is
also the undo record**. A whole-document write cannot be inverted, whereas
`brush.update` on index 3 inverts to a `brush.replace` carrying the brush as it
was. So the history is a by-product of the wire format rather than something
bolted on beside it.

## An edit that does not compile is not applied

Every mutation is followed by a build, and a build that raises puts the document
back. The reason is the round trip above: a client re-fetches the map after each
edit, so a document that cannot compile is not "a draft with a problem in it" —
it is a client with nothing to render and no way back. Lint findings are the
opposite case: a map that compiles fine and would play badly, which is reported
rather than refused.
"""

from __future__ import annotations

import copy
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from backend.modules.hassault import maplint, mapsource
from backend.modules.hassault.cgz import CgzError, CgzMap

#: How a draft is named as a map. `assets.load_map` splits on this.
PREFIX = "draft:"

#: How far back undo goes. Bounded because a draft lives in the process that also
#: runs matches, and an unbounded history of map documents is a memory leak with
#: a friendly name.
MAX_HISTORY = 200

#: Drafts nobody has touched for this long are collected. A draft is a working
#: file, not a document store — the persistent artifact is the JSON it saves to.
IDLE_TIMEOUT = 6 * 60 * 60

#: The document keys `map.set` may write. Everything else about a map is a brush
#: or an entity, and letting `map.set` reach `brushes` would route a structural
#: edit around the ops that know how to invert it.
SETTABLE = frozenset(
    {"title", "author", "license", "sfactor", "waterlevel", "watercolor", "ambient"}
)

_BLANK: dict[str, Any] = {
    "title": "Untitled",
    "author": "",
    "license": "CC0-1.0",
    "sfactor": 7,
    "waterlevel": -100,
    "ambient": 40,
    "brushes": [],
    "entities": [],
}


class DraftError(ValueError):
    """A draft operation that cannot be performed. The message is for the user."""


@dataclass(slots=True)
class Draft:
    id: str
    #: The map this was seeded from, and the default name to save back to.
    name: str
    doc: dict[str, Any]
    revision: int = 0
    touched: float = field(default_factory=time.time)
    #: Inverses, newest last. Applying one walks the document back a step.
    undo: list[dict[str, Any]] = field(default_factory=list)
    #: Forward edits popped off by undo, newest last.
    redo: list[dict[str, Any]] = field(default_factory=list)
    #: `(revision, map, owners)`. Compiling is cheap but not free, and a client
    #: fetches info and cubes as two requests against the same revision.
    cache: tuple[int, CgzMap, list[int]] | None = None

    def info(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "mapName": PREFIX + self.id,
            "doc": self.doc,
            "revision": self.revision,
            "canUndo": bool(self.undo),
            "canRedo": bool(self.redo),
        }


_drafts: dict[str, Draft] = {}
_lock = threading.Lock()


# ---- the store --------------------------------------------------------------------


def _sweep() -> None:
    cutoff = time.time() - IDLE_TIMEOUT
    for draft_id in [d.id for d in _drafts.values() if d.touched < cutoff]:
        _drafts.pop(draft_id, None)


def read_source(name: str) -> dict[str, Any]:
    """A bundled map's source document, by name.

    Read from the JSON on disk rather than from `mapsource.load_bundled`, which
    returns a compiled `CgzMap` — and a compiled map cannot be turned back into
    the rects it was painted from. The document is the thing being edited.
    """
    if not mapsource.is_bundled_name(name):
        raise DraftError(f"{name!r} is not one of ours; only bundled maps have sources")
    path = mapsource.MAPS_DIR / f"{name}.json"
    if not path.is_file():
        raise DraftError(f"no bundled map named {name!r}")
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise DraftError(f"{name} is not an object")
    return doc


def create(from_map: str | None = None) -> Draft:
    """A new draft, blank or seeded from a bundled map's source document."""
    if from_map:
        doc = read_source(from_map)
        name = from_map
    else:
        doc = copy.deepcopy(_BLANK)
        name = ""
    with _lock:
        _sweep()
        draft = Draft(id=uuid.uuid4().hex[:12], name=name, doc=doc)
        _drafts[draft.id] = draft
    # Compiled once now, so a document that cannot build fails at open rather
    # than on the client's first fetch.
    compiled(draft.id)
    return draft


def get(draft_id: str) -> Draft | None:
    draft = _drafts.get(draft_id)
    if draft is not None:
        draft.touched = time.time()
    return draft


def require(draft_id: str) -> Draft:
    draft = get(draft_id)
    if draft is None:
        raise DraftError(f"no open draft {draft_id!r}")
    return draft


def close(draft_id: str) -> bool:
    return _drafts.pop(draft_id, None) is not None


def open_ids() -> list[str]:
    return list(_drafts)


# ---- compiling --------------------------------------------------------------------


def _compile(draft: Draft) -> tuple[CgzMap, list[int]]:
    if draft.cache is not None and draft.cache[0] == draft.revision:
        return draft.cache[1], draft.cache[2]
    built, owned = mapsource.build_with_owners(draft.doc, name=PREFIX + draft.id)
    draft.cache = (draft.revision, built, owned)
    return built, owned


def compiled(draft_id: str) -> CgzMap:
    """The draft as a map. Raises `CgzError` for a document that cannot build."""
    return _compile(require(draft_id))[0]


def owners(draft_id: str) -> list[int]:
    """Per cell, the index of the brush that painted it, or -1 for untouched rock.

    Deliberately **not** a tenth entry in `PLANE_ORDER`: that tuple is a contract
    three clients validate their payload length against, and it describes the map
    *format*, which has no such field. This is a property of the document, exists
    only for a draft, and a brush index does not fit in a byte on a map with 300
    brushes. So it gets its own route, at its own width.
    """
    return _compile(require(draft_id))[1]


def lint(draft_id: str) -> list[maplint.Finding]:
    return maplint.lint(compiled(draft_id))


# ---- edits ------------------------------------------------------------------------
#
# Eight ops, and every one of them inverts to another of the eight. That closure
# is the whole trick: undo replays an inverse through the same `_apply_one` that
# made the original, so there is no second code path walking a document backwards
# that can disagree with the one walking it forwards.


def _brushes(doc: dict[str, Any]) -> list[dict[str, Any]]:
    items = doc.setdefault("brushes", [])
    if not isinstance(items, list):
        raise DraftError("this document's brushes are not a list")
    return items


def _entities(doc: dict[str, Any]) -> list[dict[str, Any]]:
    items = doc.setdefault("entities", [])
    if not isinstance(items, list):
        raise DraftError("this document's entities are not a list")
    return items


def _list_for(doc: dict[str, Any], op: str) -> tuple[list[dict[str, Any]], str, str]:
    """The list an op addresses, the word for one of its items, and the op prefix."""
    if op.startswith("brush."):
        return _brushes(doc), "brush", "brush"
    return _entities(doc), "entity", "ent"


def _at(items: list[dict[str, Any]], index: Any, what: str, limit: int) -> int:
    if not isinstance(index, int) or isinstance(index, bool):
        raise DraftError(f"{what} index must be an integer, got {index!r}")
    if not 0 <= index <= limit:
        raise DraftError(f"no {what} at index {index} (there are {len(items)})")
    return index


def _object(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DraftError(f"{what} must be an object, got {value!r}")
    return copy.deepcopy(value)


def _apply_one(doc: dict[str, Any], edit: dict[str, Any]) -> dict[str, Any]:
    """Apply one edit in place and return the edit that undoes it."""
    op = edit.get("op")
    if not isinstance(op, str):
        raise DraftError(f"an edit needs an op, got {op!r}")

    if op in ("brush.add", "ent.add"):
        items, what, prefix = _list_for(doc, op)
        value = _object(edit.get(what, edit.get("value")), what)
        # An absent index appends, which is what "draw a new room" means. Brushes
        # compose by paint order, so where a new one lands is a real decision and
        # the editor is allowed to make it explicitly.
        index = edit.get("index")
        index = len(items) if index is None else _at(items, index, what, len(items))
        items.insert(index, value)
        return {"op": prefix + ".remove", "index": index}

    if op in ("brush.remove", "ent.remove"):
        items, what, prefix = _list_for(doc, op)
        index = _at(items, edit.get("index"), what, len(items) - 1)
        return {"op": prefix + ".add", "index": index, what: items.pop(index)}

    if op in ("brush.update", "ent.update", "brush.replace", "ent.replace"):
        items, what, prefix = _list_for(doc, op)
        index = _at(items, edit.get("index"), what, len(items) - 1)
        previous = copy.deepcopy(items[index])
        if op.endswith(".replace"):
            items[index] = _object(edit.get(what, edit.get("value")), what)
        else:
            merged = copy.deepcopy(items[index])
            for key, value in _object(edit.get("patch"), "patch").items():
                # An explicit null clears a field, which is how a brush goes back
                # to its default rather than being pinned to what that default
                # happens to be today — the two stop agreeing the moment one
                # changes.
                if value is None:
                    merged.pop(key, None)
                else:
                    merged[key] = value
            items[index] = merged
        # Always a replace, never an inverse patch: a patch that cleared a key has
        # no inverse patch, and inventing one is where undo starts disagreeing
        # with the document.
        return {"op": prefix + ".replace", "index": index, what: previous}

    if op == "brush.reorder":
        items = _brushes(doc)
        source = _at(items, edit.get("from"), "brush", len(items) - 1)
        target = _at(items, edit.get("to"), "brush", len(items) - 1)
        items.insert(target, items.pop(source))
        return {"op": "brush.reorder", "from": target, "to": source}

    if op == "map.set":
        key = edit.get("key")
        if key not in SETTABLE:
            raise DraftError(
                f"{key!r} is not a settable map field; expected one of "
                + ", ".join(sorted(SETTABLE))
            )
        previous = copy.deepcopy(doc.get(key))
        value = edit.get("value")
        if value is None:
            doc.pop(key, None)
        else:
            doc[key] = copy.deepcopy(value)
        return {"op": "map.set", "key": key, "value": previous}

    raise DraftError(f"unknown edit op {op!r}")


def _mutate(draft: Draft, edit: dict[str, Any]) -> dict[str, Any]:
    """Apply an edit, or leave the document exactly as it was.

    The rollback is the point. A client re-fetches the map after every edit, so a
    document left in a state that will not compile is not a draft with a problem
    in it — it is a client with nothing to render and no way back to a state that
    renders.
    """
    if not isinstance(edit, dict):
        raise DraftError("an edit must be an object")
    before = copy.deepcopy(draft.doc)
    try:
        inverse = _apply_one(draft.doc, edit)
        mapsource.build(draft.doc, name=PREFIX + draft.id)
    except (DraftError, CgzError):
        draft.doc = before
        raise
    draft.revision += 1
    return inverse


def apply(draft_id: str, edit: dict[str, Any]) -> Draft:
    """One edit, recorded so it can be undone."""
    draft = require(draft_id)
    with _lock:
        inverse = _mutate(draft, edit)
        draft.undo.append(inverse)
        del draft.undo[: max(0, len(draft.undo) - MAX_HISTORY)]
        # A new edit is a new branch, and the old redo path no longer leads
        # anywhere this document has been.
        draft.redo.clear()
    return draft


def undo(draft_id: str) -> Draft:
    draft = require(draft_id)
    with _lock:
        if not draft.undo:
            raise DraftError("nothing to undo")
        draft.redo.append(_mutate(draft, draft.undo.pop()))
    return draft


def redo(draft_id: str) -> Draft:
    draft = require(draft_id)
    with _lock:
        if not draft.redo:
            raise DraftError("nothing to redo")
        draft.undo.append(_mutate(draft, draft.redo.pop()))
    return draft


# ---- saving -----------------------------------------------------------------------


def save(draft_id: str, name: str | None = None, overwrite: bool = False) -> str:
    """Write the document to `maps/<name>.json`. Returns the name it saved as.

    Written atomically for the reason every write in this repo now is: a reader
    catching the file mid-write sees a truncated document, and here that reader is
    the map catalog — a half-written map is one the game lists and cannot open.
    """
    draft = require(draft_id)
    target = (name or draft.name or "").strip()
    if not target:
        raise DraftError("a draft needs a name before it can be saved")
    if not target.startswith(mapsource.BUNDLED_PREFIX):
        target = mapsource.BUNDLED_PREFIX + target
    if not mapsource.is_bundled_name(target):
        raise DraftError(
            f"{target!r} is not a usable map name; letters, digits, - and _ only"
        )

    # It has to compile before it is written, for the same reason an edit does:
    # the catalog would otherwise list a map nothing can open.
    mapsource.build(draft.doc, name=target)

    path = mapsource.MAPS_DIR / f"{target}.json"
    if path.exists() and not overwrite:
        raise DraftError(f"{target} already exists; pass overwrite to replace it")

    payload = json.dumps(draft.doc, indent=2, ensure_ascii=False) + "\n"
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, path)

    # Both caches are keyed by name alone, with no mtime in them — bundled maps
    # ship with the code and were never meant to change under a running process.
    # They do now.
    mapsource.load_bundled.cache_clear()
    mapsource.bundled_names.cache_clear()

    draft.name = target
    return target

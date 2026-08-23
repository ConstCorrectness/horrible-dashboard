"""Where a design lives on disk: a `.py` you can take away, and a sidecar you can't.

Two files per design, under `$HORRIBLE_DATA_DIR/model-graphs/`:

    <name>.py           the generated module — regenerated on every save
    <name>.graph.json   the graph, plus the canvas layout

The split is the point. The `.py` is the deliverable: openable in any editor,
runnable in any venv, and the thing the round-trip parser will read back. The
sidecar holds what Python has nowhere sane to put — node positions, frames, collapse
state — keyed by node id, which is exactly what the `# horrible:node=` markers in the
`.py` recover. Losing the sidecar costs you a tidy layout and nothing else; losing
the `.py` costs you the model.

Writes go through `atomic_write` for the reason that module exists: a save and a
read race, and a half-written record read as a missing one is how a design that
exists answers 404.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from backend.atomic_write import read_text_or_none, write_text_atomic
from backend.modules.interpretability.graph import codegen
from backend.modules.interpretability.graph.models import DesignGraph
from backend.paths import data_dir

logger = logging.getLogger(__name__)

#: A design name is also a filename and a Python class name, so it is restricted at
#: the door rather than sanitised on the way out — `..` is how a save route becomes
#: an arbitrary-file-write route.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,63}$")


class NodeLayout(BaseModel):
    """One node's cosmetics. Never structure — see the module docstring."""

    x: float = 0.0
    y: float = 0.0
    collapsed: bool = False
    label: str = ""
    #: Id of the frame (Blender's visual grouping box) this node sits in, if any.
    frame: str = ""


class FrameBox(BaseModel):
    """A frame: a labelled rectangle behind a set of nodes. Purely visual."""

    id: str
    label: str = ""
    color: str = ""


class Layout(BaseModel):
    nodes: dict[str, NodeLayout] = Field(default_factory=dict)
    frames: list[FrameBox] = Field(default_factory=list)
    #: Canvas pan/zoom, so reopening a design lands where you left it.
    viewport: dict[str, float] = Field(default_factory=dict)


class StoredDesign(BaseModel):
    """What the pane loads: the graph, its cosmetics, and the source it generates."""

    name: str
    graph: DesignGraph
    layout: Layout = Field(default_factory=Layout)
    source: str = ""
    #: Non-null when the graph cannot currently be turned into code. The design is
    #: still saved and still editable — a model mid-edit is the normal state of a
    #: canvas, and refusing to persist it would lose work over an unfinished wire.
    codeError: str | None = None


class NameError_(ValueError):
    """The requested name is not one we will put on the filesystem."""


def root() -> Path:
    path = data_dir() / "model-graphs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def check_name(name: str) -> str:
    if not _SAFE_NAME.match(name or ""):
        raise NameError_(
            f"{name!r} is not a usable design name — letters, digits, spaces, dashes and underscores only."
        )
    return name


def paths_for(name: str) -> tuple[Path, Path]:
    check_name(name)
    return root() / f"{name}.py", root() / f"{name}.graph.json"


def listing() -> list[dict[str, object]]:
    """Every saved design, newest first, without parsing any of them.

    The library pane shows a name and a size; opening a dozen graphs to render a
    list would make the list slower than the thing it lists.
    """
    out: list[dict[str, object]] = []
    for path in root().glob("*.graph.json"):
        try:
            stat = path.stat()
        except OSError:
            continue
        out.append(
            {
                "name": path.name[: -len(".graph.json")],
                "modified": stat.st_mtime,
                "bytes": stat.st_size,
            }
        )
    return sorted(out, key=lambda row: row["modified"], reverse=True)  # type: ignore[arg-type,return-value]


def load(name: str) -> StoredDesign | None:
    """The saved design, or None if there isn't one. A corrupt sidecar is `None` too,
    logged — a design we cannot read is not a design that half-exists."""
    _, sidecar = paths_for(name)
    raw = read_text_or_none(sidecar)
    if raw is None:
        return None
    try:
        payload = json.loads(raw)
        return StoredDesign.model_validate(payload)
    except (ValueError, ValidationError) as exc:
        logger.warning("model-graph %s is unreadable: %s", name, exc)
        return None


def save(name: str, graph: DesignGraph, layout: Layout | None = None) -> StoredDesign:
    """Persist the graph and its sidecar, and regenerate the `.py` beside them."""
    module_path, sidecar = paths_for(name)
    graph = graph.model_copy(update={"name": graph.name or name})

    result = codegen.generate(graph)
    stored = StoredDesign(
        name=name,
        graph=graph,
        layout=layout or (load(name) or StoredDesign(name=name, graph=graph)).layout,
        source=result.source,
        codeError=result.error,
    )

    write_text_atomic(sidecar, json.dumps(stored.model_dump(), indent=2))
    if result.source:
        write_text_atomic(module_path, result.source)
    return stored


def delete(name: str) -> bool:
    """Remove both files. Returns whether there was anything to remove."""
    module_path, sidecar = paths_for(name)
    found = False
    for path in (module_path, sidecar):
        try:
            path.unlink()
            found = True
        except FileNotFoundError:
            continue
        except OSError as exc:
            logger.warning("could not delete %s: %s", path, exc)
    return found

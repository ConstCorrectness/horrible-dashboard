"""Domain-neutral `.ipynb` IO.

nbformat v4.5 on disk is the source of truth. Cells always carry ids so the UI,
the kernel session, and agent tools can address them stably. Writes are atomic:
serialize to a temp file in the same directory, then `os.replace` — via
`backend.atomic_write`, which also handles the Windows half (a replace fails while
a reader holds the destination open, and an open fails while a replace is in
flight). The kernel's save debounce fires from a worker thread while panes and
agent tools read the same file, so both halves are load-bearing here.

Callers pass resolved paths (see `resolve_path` for the escape-guarded helper);
domain modules layer their own root/model wrappers on top (e.g. `training.notebooks`
keeps `notebook_path(project, rel)`).
"""

from __future__ import annotations

import os
import tempfile
import warnings
from pathlib import Path
from typing import Any

import nbformat

from backend.atomic_write import read_text_or_none, replace_with_retry
from backend.notebook_core.models import CellModel, NotebookModel

NBFORMAT_MINOR = 5  # cell ids


def resolve_path(root: Path, rel_path: str) -> Path:
    """Resolve a notebook path inside `root`, refusing escapes."""
    root = root.resolve()
    resolved = (root / rel_path).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"notebook path escapes root: {rel_path}")
    return resolved


def new_notebook(
    path: Path, cells: list[dict[str, Any]], metadata: dict[str, Any] | None = None
) -> None:
    """Create a fresh notebook from raw nbformat cell dicts and save it atomically."""
    nb = nbformat.v4.new_notebook()
    nb.nbformat_minor = max(nb.nbformat_minor, NBFORMAT_MINOR)
    for raw in cells:
        if raw.get("cell_type") == "markdown":
            cell = nbformat.v4.new_markdown_cell(raw.get("source", ""))
        else:
            cell = nbformat.v4.new_code_cell(raw.get("source", ""))
        nb.cells.append(cell)
    if metadata:
        nb.metadata.update(metadata)
    save(path, nb)


def load(path: Path) -> nbformat.NotebookNode:
    # Read the text ourselves rather than letting nbformat open the file, so a save
    # landing mid-open is retried instead of raising (see `backend.atomic_write`).
    raw = read_text_or_none(path)
    if raw is None:
        raise FileNotFoundError(str(path))
    # Reading a legacy (< 4.5) notebook warns about missing cell ids; we fix that
    # by normalizing right after, so the read-time warning is just noise.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", nbformat.validator.MissingIDFieldWarning)
        nb = nbformat.reads(raw, as_version=4)
    # Older notebooks may predate cell ids (< 4.5): bump the minor version first —
    # normalize only assigns ids where the declared schema has them.
    nb.nbformat_minor = max(nb.nbformat_minor, NBFORMAT_MINOR)
    _, nb = nbformat.validator.normalize(nb)
    return nb


def save(path: Path, nb: nbformat.NotebookNode) -> None:
    """Atomic write: temp file in the same directory, then replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".ipynb.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            nbformat.write(nb, f)
        replace_with_retry(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def apply_op(nb: nbformat.NotebookNode, op: dict[str, Any]) -> str:
    """Apply one cell operation to an in-memory doc; returns the affected cell id.

    Ops (`{op: insert|edit|delete|move, ...}`):
      insert: {cellType?, source?, afterCellId? | index?} — omitted position = end
      edit:   {cellId, source}
      delete: {cellId}
      move:   {cellId, index}
    Raises ValueError for unknown ops/cells (surfaced to the caller as an error).
    """
    kind = str(op.get("op", ""))
    if kind == "insert":
        cell_type = str(op.get("cellType", "code"))
        source = str(op.get("source", ""))
        node = (
            nbformat.v4.new_markdown_cell(source)
            if cell_type == "markdown"
            else nbformat.v4.new_code_cell(source)
        )
        # A client may assign the id up front (so it can edit/run the new cell
        # without waiting to learn a server-generated id); otherwise keep nbformat's.
        if op.get("cellId"):
            node["id"] = str(op["cellId"])
        index = _insert_index(nb, op)
        nb.cells.insert(index, node)
        return str(node["id"])
    index = _find_cell(nb, str(op.get("cellId", "")))
    if kind == "edit":
        nb.cells[index]["source"] = str(op.get("source", ""))
    elif kind == "delete":
        del nb.cells[index]
    elif kind == "move":
        node = nb.cells.pop(index)
        target = max(0, min(int(op.get("index", 0)), len(nb.cells)))
        nb.cells.insert(target, node)
    else:
        raise ValueError(f"unknown cell op: {kind!r}")
    return str(op.get("cellId", ""))


def _find_cell(nb: nbformat.NotebookNode, cell_id: str) -> int:
    for i, cell in enumerate(nb.cells):
        if cell.get("id") == cell_id:
            return i
    raise ValueError(f"unknown cell: {cell_id}")


def _insert_index(nb: nbformat.NotebookNode, op: dict[str, Any]) -> int:
    after = op.get("afterCellId")
    if after is not None:
        return _find_cell(nb, str(after)) + 1
    index = op.get("index")
    if index is not None:
        return max(0, min(int(index), len(nb.cells)))
    return len(nb.cells)


def to_model(nb: nbformat.NotebookNode, rel_path: str) -> NotebookModel:
    cells = [
        CellModel(
            id=str(cell.get("id", "")),
            cell_type=cell.cell_type,
            source=cell.source,
            outputs=[dict(o) for o in cell.get("outputs", [])],
            execution_count=cell.get("execution_count"),
        )
        for cell in nb.cells
        if cell.cell_type in ("code", "markdown")
    ]
    return NotebookModel(path=rel_path, cells=cells, metadata=dict(nb.metadata))


def from_model(model: NotebookModel) -> nbformat.NotebookNode:
    """Rebuild an nbformat doc from a whole-document model (PUT /notebook)."""
    nb = nbformat.v4.new_notebook()
    nb.nbformat_minor = max(nb.nbformat_minor, NBFORMAT_MINOR)
    nb.metadata.update(model.metadata)
    for cell in model.cells:
        if cell.cell_type == "markdown":
            node = nbformat.v4.new_markdown_cell(cell.source)
        else:
            node = nbformat.v4.new_code_cell(cell.source)
            node.outputs = [nbformat.from_dict(o) for o in cell.outputs]
            node.execution_count = cell.execution_count
        if cell.id:
            node["id"] = cell.id
        nb.cells.append(node)
    return nb

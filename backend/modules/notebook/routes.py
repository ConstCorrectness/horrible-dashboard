"""REST surface for the notebook module (`/api/notebook/*`).

Cell execution and live output happen over the `notebook` ws channel (see
`manager.py`); these routes cover the file catalog, creating notebooks, loading a
document for initial render, and the execution-mode flag. The document format is
nbformat `.ipynb` on disk under the `notebook.root` setting.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from backend.modules.notebook import env
from backend.modules.notebook.manager import notebook_manager, notebook_root, resolve
from backend.modules.notebook.models import (
    CreateNotebookRequest,
    NotebookFile,
    NotebookListResponse,
    NotebookModel,
    SetModeRequest,
)
from backend.notebook_core import notebooks

router = APIRouter(prefix="/notebook", tags=["notebook"])

STARTER_CELLS = [
    {"cell_type": "markdown", "source": "# New notebook\n"},
    {"cell_type": "code", "source": ""},
]


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


@router.get("/env")
def env_status() -> dict[str, bool]:
    """Whether the kernel interpreter is ready (first open otherwise bootstraps it)."""
    return {"ready": env.python_ready()}


@router.get("/files", response_model=NotebookListResponse)
async def list_files() -> NotebookListResponse:
    root = notebook_root()

    def scan() -> list[NotebookFile]:
        if not root.is_dir():
            return []
        out: list[NotebookFile] = []
        for p in sorted(root.rglob("*.ipynb")):
            if any(part in (".venv", ".ipynb_checkpoints") for part in p.parts):
                continue
            try:
                mtime = p.stat().st_mtime
            except OSError:
                mtime = 0.0
            out.append(NotebookFile(path=_rel(p, root), name=p.name, modified=mtime))
        return out

    files = await asyncio.to_thread(scan)
    return NotebookListResponse(root=str(root), files=files)


@router.get("/doc", response_model=NotebookModel)
async def get_doc(path: str = Query(...)) -> NotebookModel:
    try:
        abs_path = resolve(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not abs_path.is_file():
        raise HTTPException(status_code=404, detail=f"notebook not found: {path}")
    nb = await asyncio.to_thread(notebooks.load, abs_path)
    return notebooks.to_model(nb, path)


@router.post("", response_model=NotebookModel)
async def create(req: CreateNotebookRequest) -> NotebookModel:
    rel = req.path.replace("\\", "/")
    if not rel.endswith(".ipynb"):
        rel += ".ipynb"
    try:
        abs_path = resolve(rel)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if abs_path.exists():
        raise HTTPException(status_code=409, detail=f"already exists: {rel}")
    metadata = {"horrible": {"execution_mode": req.mode}}
    await asyncio.to_thread(notebooks.new_notebook, abs_path, STARTER_CELLS, metadata)
    nb = await asyncio.to_thread(notebooks.load, abs_path)
    return notebooks.to_model(nb, rel)


@router.put("/mode", response_model=NotebookModel)
async def set_mode(req: SetModeRequest) -> NotebookModel:
    rel = req.path.replace("\\", "/")
    try:
        abs_path = resolve(rel)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not abs_path.is_file():
        raise HTTPException(status_code=404, detail=f"notebook not found: {rel}")

    def apply() -> NotebookModel:
        session = notebook_manager.session_for(f"nb:{rel}")
        if session is not None:
            # A live session's in-memory doc is authoritative — update + flush it.
            with session.doc_lock:
                session.doc.metadata.setdefault("horrible", {})["execution_mode"] = (
                    req.mode
                )
            session.save_now()
            return notebooks.to_model(session.doc, rel)
        nb = notebooks.load(abs_path)
        nb.metadata.setdefault("horrible", {})["execution_mode"] = req.mode
        notebooks.save(abs_path, nb)
        return notebooks.to_model(nb, rel)

    return await asyncio.to_thread(apply)

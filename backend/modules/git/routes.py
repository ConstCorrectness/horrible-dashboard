"""HTTP surface for the git provenance pane. Path access reuses the files module's
workspace-root boundary (`_resolve`/`_roots`). See docs/modules/git.mdx."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from backend.modules.files.routes import _resolve, _roots
from backend.modules.git import service
from backend.modules.git.models import (
    BlameResult,
    CommitRequest,
    CommitResult,
    DiffResult,
    LogResult,
)

router = APIRouter(prefix="/git", tags=["git"])


def _repo_hint(path: str | None) -> Path:
    """A path inside the repo to locate it — a given workspace path, else the first root."""
    if path:
        return _resolve(path)
    roots = _roots()
    if not roots:
        raise HTTPException(status_code=400, detail="no workspace roots configured")
    return roots[0]


@router.get("/blame", response_model=BlameResult)
def blame(path: str) -> BlameResult:
    return service.blame(_resolve(path))


@router.get("/log", response_model=LogResult)
def log(limit: int = 30, path: str | None = None) -> LogResult:
    return service.log(_repo_hint(path), limit)


@router.get("/show", response_model=DiffResult)
def show(sha: str, path: str | None = None) -> DiffResult:
    return service.show(_repo_hint(path), sha)


@router.post("/commit", response_model=CommitResult)
def commit(body: CommitRequest) -> CommitResult:
    resolved = [str(_resolve(p)) for p in body.paths] if body.paths else None
    return service.commit(_repo_hint(body.path), body.message, resolved)

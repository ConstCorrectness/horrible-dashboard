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
    ScmStatus,
    StageRequest,
    StageResult,
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


@router.get("/scm-status", response_model=ScmStatus)
def scm_status(path: str | None = None) -> ScmStatus:
    """The working tree grouped for source control. Unlike `/files/git-status`,
    this keeps git's two status characters apart — staged and unstaged are the
    whole shape of this view, and the collapsed category cannot express them."""
    return service.scm_status(_repo_hint(path))


@router.get("/diff", response_model=DiffResult)
def diff(path: str | None = None, staged: bool = False) -> DiffResult:
    """The working-tree diff for a path (or the whole tree), or the index diff."""
    target = _resolve(path) if path else None
    return service.working_diff(_repo_hint(path), target, staged)


@router.post("/stage", response_model=StageResult)
def stage(body: StageRequest) -> StageResult:
    # Every path crosses the workspace-root boundary before reaching git, the
    # same rule `commit` follows.
    return service.stage(_repo_hint(body.path), [str(_resolve(p)) for p in body.paths])


@router.post("/unstage", response_model=StageResult)
def unstage(body: StageRequest) -> StageResult:
    return service.unstage(
        _repo_hint(body.path), [str(_resolve(p)) for p in body.paths]
    )

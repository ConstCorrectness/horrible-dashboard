"""Workspace file access, rooted at configured **workspace roots**.

Every path the API touches must resolve (symlinks included) to inside one of the
configured roots — the path-traversal boundary lives here, not in the UI, so a
remote backend can never be coaxed into serving paths outside its roots. Roots are
configured in settings (`files.roots`, a list of absolute paths; see
docs/modules/settings.md) with an env override (`HORRIBLE_WORKSPACE_ROOTS`,
os.pathsep-separated) for dev/test. See docs/modules/file-explorer.md.

This is the HTTP surface (list/read + create/write/rename/delete). Live watch
events over the `files.*` WS channels are a follow-up; clients re-list to refresh.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException

from backend.modules.files.models import (
    CreateRequest,
    DeleteRequest,
    DirListing,
    FileContent,
    FileEntry,
    OpResult,
    RenameRequest,
    RootInfo,
    WriteRequest,
)
from backend.modules.settings.routes import get_value

router = APIRouter(prefix="/files", tags=["files"])

# Cap a single read so a huge file can't blow up the response / editor.
MAX_READ_BYTES = 2_000_000


def _roots() -> list[Path]:
    """Resolved, existing workspace-root directories. Settings first, then the
    env override appended (deduped)."""
    raw: list[str] = []
    configured = get_value("files.roots", [])
    if isinstance(configured, list):
        raw.extend(str(p) for p in configured)
    env = os.environ.get("HORRIBLE_WORKSPACE_ROOTS")
    if env:
        raw.extend(part for part in env.split(os.pathsep) if part)

    roots: list[Path] = []
    seen: set[Path] = set()
    for entry in raw:
        try:
            resolved = Path(entry).expanduser().resolve()
        except OSError:
            continue
        if resolved.is_dir() and resolved not in seen:
            seen.add(resolved)
            roots.append(resolved)
    return roots


def _resolve(raw: str, *, must_exist: bool = True) -> Path:
    """Resolve a requested path and enforce that it lives inside a workspace root.
    `resolve()` collapses `..` and follows symlinks on existing components, so a
    symlink or `../` escape lands outside every root and is rejected."""
    if not raw:
        raise HTTPException(status_code=400, detail="path is required")
    try:
        resolved = Path(raw).expanduser().resolve()
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"bad path: {exc}") from exc

    roots = _roots()
    if not roots:
        raise HTTPException(status_code=400, detail="no workspace roots configured")
    if not any(resolved == root or resolved.is_relative_to(root) for root in roots):
        raise HTTPException(status_code=403, detail="path outside workspace roots")
    if must_exist and not resolved.exists():
        raise HTTPException(status_code=404, detail="not found")
    return resolved


def _entry(path: Path) -> FileEntry:
    is_dir = path.is_dir()
    try:
        stat = path.stat()
        size = None if is_dir else stat.st_size
        mtime = stat.st_mtime
    except OSError:
        size = mtime = None
    return FileEntry(
        name=path.name,
        path=str(path),
        kind="dir" if is_dir else "file",
        size=size,
        mtime=mtime,
    )


@router.get("/roots", response_model=list[RootInfo])
def list_roots() -> list[RootInfo]:
    return [RootInfo(name=root.name or str(root), path=str(root)) for root in _roots()]


@router.get("/list", response_model=DirListing)
def list_dir(path: str) -> DirListing:
    target = _resolve(path)
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="not a directory")
    try:
        children = sorted(
            target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())
        )
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"cannot list: {exc}") from exc
    return DirListing(path=str(target), entries=[_entry(c) for c in children])


@router.get("/read", response_model=FileContent)
def read_file(path: str) -> FileContent:
    target = _resolve(path)
    if not target.is_file():
        raise HTTPException(status_code=400, detail="not a file")
    data = target.read_bytes()
    truncated = len(data) > MAX_READ_BYTES
    if truncated:
        data = data[:MAX_READ_BYTES]
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=415, detail="binary or non-UTF-8 file") from exc
    return FileContent(path=str(target), content=content, truncated=truncated)


@router.post("/create", response_model=FileEntry)
def create(body: CreateRequest) -> FileEntry:
    target = _resolve(body.path, must_exist=False)
    if target.exists():
        raise HTTPException(status_code=409, detail="already exists")
    try:
        if body.kind == "dir":
            target.mkdir(parents=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body.content, encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"cannot create: {exc}") from exc
    return _entry(target)


@router.put("/write", response_model=FileEntry)
def write_file(body: WriteRequest) -> FileEntry:
    target = _resolve(body.path, must_exist=False)
    if target.exists() and target.is_dir():
        raise HTTPException(status_code=400, detail="path is a directory")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body.content, encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"cannot write: {exc}") from exc
    return _entry(target)


@router.post("/rename", response_model=FileEntry)
def rename(body: RenameRequest) -> FileEntry:
    source = _resolve(body.path)
    dest = _resolve(body.new_path, must_exist=False)
    if dest.exists():
        raise HTTPException(status_code=409, detail="destination exists")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        source.rename(dest)
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"cannot rename: {exc}") from exc
    return _entry(dest)


@router.post("/delete", response_model=OpResult)
def delete(body: DeleteRequest) -> OpResult:
    target = _resolve(body.path)
    try:
        if target.is_dir():
            if body.recursive:
                _rmtree(target)
            else:
                target.rmdir()  # fails if non-empty — non-recursive guard
        else:
            target.unlink()
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"cannot delete: {exc}") from exc
    return OpResult(ok=True, path=str(target))


def _rmtree(path: Path) -> None:
    for child in path.iterdir():
        if child.is_dir():
            _rmtree(child)
        else:
            child.unlink()
    path.rmdir()

"""Workspace file access, rooted at configured **workspace roots**.

Every path the API touches must resolve (symlinks included) to inside one of the
configured roots — the path-traversal boundary lives here, not in the UI, so a
remote backend can never be coaxed into serving paths outside its roots. Roots are
configured in settings (`files.roots`, a list of absolute paths; see
docs/modules/settings.md) with an env override (`HORRIBLE_WORKSPACE_ROOTS`,
os.pathsep-separated) for dev/test. When neither is set, the backend sets up a
default workspace — `~/Projects`, created if missing — so file features work out of
the box on the *user's* projects rather than the app's own checkout;
`HORRIBLE_NO_DEFAULT_ROOT=1` restores the fail-closed boundary for hardened
deployments. See docs/modules/file-explorer.md.

This is the HTTP surface (list/read + create/write/rename/delete). Live watch
events ship separately in `watcher.py` (the `files` `/ws` channel).

**Virtual roots.** A path carrying a registered URI scheme (`gdrive:/…`) belongs to a
provider, not the filesystem, and is dispatched in `providers.py` *before* `_resolve`
ever sees it — `_resolve` anchors relative paths to a root, so letting a URI reach it
would produce `<root>/gdrive:/abc` and a nonsense 403. The traversal boundary itself is
unchanged and still governs every real path.
"""

from __future__ import annotations

import os
from pathlib import Path

import anyio.to_thread
from fastapi import APIRouter, HTTPException

from backend.modules.files import providers
from backend.modules.files.git import git_status
from backend.modules.files.models import (
    CreateRequest,
    DeleteRequest,
    DirListing,
    FileContent,
    FileEntry,
    GitStatus,
    OpResult,
    RenameRequest,
    RootInfo,
    WriteRequest,
)
from backend.modules.settings.routes import get_value

router = APIRouter(prefix="/files", tags=["files"])

# Cap a single read so a huge file can't blow up the response / editor.
MAX_READ_BYTES = 2_000_000


def _default_root() -> Path | None:
    """A sensible default workspace root so file features work **out of the box**
    when nothing is configured: `~/Projects`, created if it doesn't exist — a *user*
    workspace, deliberately not the backend's launch directory (which in dev and
    desktop is this app's own repo checkout). Cross-platform via `Path.home()`.

    Opt out with `HORRIBLE_NO_DEFAULT_ROOT=1` for hardened/remote deployments that
    want the fail-closed boundary (no implicit root, and nothing created)."""
    if os.environ.get("HORRIBLE_NO_DEFAULT_ROOT"):
        return None
    try:
        projects = (Path.home() / "Projects").resolve()
        projects.mkdir(parents=True, exist_ok=True)
    except (OSError, RuntimeError):  # no resolvable home / can't create — fail closed
        return None
    return projects


def _roots() -> list[Path]:
    """Resolved, existing workspace-root directories. Settings first, then the env
    override appended (deduped). When neither is configured, falls back to a single
    default root (`_default_root`) so the file explorer and `files.*` tools work out
    of the box."""
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

    if not roots:
        default = _default_root()
        if default is not None:
            roots.append(default)
    return roots


def _resolve_relative(rel: Path, roots: list[Path]) -> Path:
    """Anchor a *workspace-relative* path to a root. A leading segment that names a
    root (its basename, e.g. ``myrepo/src/x.py``) selects that root; otherwise the
    first root is used. Agents typically pass bare/relative paths (``notes.txt``)
    because they don't know absolute root paths — without this they'd resolve against
    the backend CWD and be rejected as outside every root."""
    parts = rel.parts
    if parts:
        for root in roots:
            if root.name == parts[0]:
                return root.joinpath(*parts[1:]).resolve()
    return (roots[0] / rel).resolve()


def _resolve(raw: str, *, must_exist: bool = True) -> Path:
    """Resolve a requested path and enforce that it lives inside a workspace root.
    Absolute paths resolve as-is; relative paths are anchored to a root (see
    `_resolve_relative`). `resolve()` collapses `..` and follows symlinks on existing
    components, so a symlink or `../` escape lands outside every root and is rejected
    by the boundary check below — true for relative inputs too, since the check runs
    after anchoring."""
    if not raw:
        raise HTTPException(status_code=400, detail="path is required")
    roots = _roots()
    if not roots:
        raise HTTPException(status_code=400, detail="no workspace roots configured")
    try:
        candidate = Path(raw).expanduser()
        resolved = (
            candidate.resolve()
            if candidate.is_absolute()
            else _resolve_relative(candidate, roots)
        )
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"bad path: {exc}") from exc

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


def _reject_if_virtual(*paths: str) -> None:
    """Guard the write routes. Providers are read-only, and saying so with a 403 beats
    letting the request fall through to `_resolve` and fail as "outside workspace
    roots", which would misdescribe why."""
    for path in paths:
        if providers.is_virtual(path):
            raise HTTPException(status_code=403, detail="this location is read-only")


@router.get("/roots", response_model=list[RootInfo])
async def list_roots() -> list[RootInfo]:
    local = [RootInfo(name=root.name or str(root), path=str(root)) for root in _roots()]
    return local + await providers.all_roots()


@router.get("/git-status", response_model=GitStatus)
def git_status_route(path: str) -> GitStatus:
    """Working-tree status for a workspace root (path must be one of the roots).
    Returns `is_repo=False` if it isn't inside a git repository."""
    # A virtual root can't be a git repo. Answer that plainly rather than erroring —
    # the tree asks this of every root and would otherwise log a failure per refresh.
    if providers.is_virtual(path):
        return GitStatus(is_repo=False, root=path, branch=None, entries=[])
    target = _resolve(path)
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="not a directory")
    return git_status(target)


@router.get("/list", response_model=DirListing)
async def list_dir(path: str, fresh: bool = False) -> DirListing:
    """List a directory. `fresh=1` bypasses a provider's cache (no effect locally)."""
    if provider := providers.provider_for(path):
        return await provider.list(path, fresh=fresh)
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
async def read_file(path: str) -> FileContent:
    if provider := providers.provider_for(path):
        return await provider.read(path)
    target = _resolve(path)
    if not target.is_file():
        raise HTTPException(status_code=400, detail="not a file")
    # Up to 2 MB off the event loop — this route is async now that providers await.
    data = await anyio.to_thread.run_sync(target.read_bytes)
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
    _reject_if_virtual(body.path)
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
    _reject_if_virtual(body.path)
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
    # Both ends: renaming *into* a read-only mount is as impossible as out of one.
    _reject_if_virtual(body.path, body.new_path)
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
    _reject_if_virtual(body.path)
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

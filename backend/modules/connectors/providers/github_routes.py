"""HTTP surface for the GitHub repo viewer (`/api/connectors/github/*`).

Lives beside the connector rather than in a `backend/modules/github/` of its own,
mirroring `google_routes.py`. The reason is the token: it's held by `github.py` and
reached through `github_tools._request`, and a separate module would have to import
another module's private helpers — which the module conventions forbid. Here it's an
ordinary intra-package call.

**Whole-tree fetch, not per-directory.** `git/trees/{ref}?recursive=1` returns every
path in one request; walking `contents/` per expanded folder costs a round-trip each
time and eats the 5000/hr budget on a repo the user is only browsing. The one case it
can't serve is a repo big enough for GitHub to set `truncated` (~100k entries or 7 MB),
so `/contents` stays as the lazy fallback.

Responses keep `github_tools`' errors-as-values convention at the boundary and turn them
into HTTP status codes here, because the caller is a browser, not an agent loop.
"""

from __future__ import annotations

import base64
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.modules.connectors.providers import github_tools

router = APIRouter(prefix="/connectors/github", tags=["connectors"])

MAX_FILE_BYTES = 100_000

# GitHub allows 5000 authenticated requests/hour. Browsing a tree is read-heavy and
# highly repetitive (re-open a file, switch tabs back), so short TTLs cut the request
# count hard without making the viewer feel stale. Refs are immutable-ish; repo lists
# change under the user's own hands, hence the shorter window.
TTL_REPOS_S = 60.0
TTL_META_S = 300.0
CACHE_MAX = 300

_cache: dict[str, tuple[float, float, Any]] = {}


def _cached(key: str) -> Any | None:
    hit = _cache.get(key)
    if hit is None:
        return None
    stored_at, ttl, value = hit
    if time.monotonic() - stored_at > ttl:
        _cache.pop(key, None)
        return None
    return value


def _store(key: str, value: Any, ttl: float) -> None:
    if len(_cache) >= CACHE_MAX:
        _cache.pop(next(iter(_cache)), None)
    _cache[key] = (time.monotonic(), ttl, value)


def clear_cache() -> None:
    _cache.clear()


def _check(data: Any) -> Any:
    """Turn `github_tools`' `{"error": …}` into an HTTP error."""
    if isinstance(data, dict) and data.get("error"):
        message = str(data["error"])
        # "not connected" is the user's to fix; everything else is upstream's.
        status = 409 if "isn't connected" in message else 502
        raise HTTPException(status_code=status, detail=message)
    return data


async def _fetch(key: str, path: str, ttl: float, *, fresh: bool, **kwargs: Any) -> Any:
    if not fresh and (hit := _cached(key)) is not None:
        return hit
    data = _check(await github_tools._request("GET", path, **kwargs))
    _store(key, data, ttl)
    return data


class RepoSummary(BaseModel):
    full_name: str
    description: str | None = None
    private: bool = False
    language: str | None = None
    stars: int = 0
    default_branch: str = ""
    updated_at: str | None = None
    url: str | None = None


class TreeEntry(BaseModel):
    """One path in a repo tree. `kind` mirrors the files module's vocabulary rather
    than GitHub's `blob`/`tree`, so the viewer's tree code reads like the file tree's."""

    path: str
    kind: str  # "file" | "dir"
    size: int | None = None


class TreeResponse(BaseModel):
    ref: str
    entries: list[TreeEntry] = []
    # GitHub gave up on a repo this large; the viewer must fall back to lazy listing.
    truncated: bool = False


class FileResponse(BaseModel):
    path: str
    content: str
    truncated: bool = False
    url: str | None = None


class DirResponse(BaseModel):
    path: str
    entries: list[TreeEntry] = []


class ReadmeResponse(BaseModel):
    path: str
    content: str


def _summary(repo: dict[str, Any]) -> RepoSummary:
    return RepoSummary(
        full_name=str(repo.get("full_name") or ""),
        description=repo.get("description"),
        private=bool(repo.get("private")),
        language=repo.get("language"),
        stars=int(repo.get("stargazers_count") or 0),
        default_branch=str(repo.get("default_branch") or ""),
        updated_at=repo.get("updated_at"),
        url=repo.get("html_url"),
    )


@router.get("/repos", response_model=list[RepoSummary])
async def list_repos(fresh: bool = False) -> list[RepoSummary]:
    """The connected user's repositories, most recently updated first."""
    data = await _fetch(
        "repos",
        "/user/repos",
        TTL_REPOS_S,
        fresh=fresh,
        params={
            "per_page": 100,
            "sort": "updated",
            "affiliation": "owner,collaborator,organization_member",
        },
    )
    return [_summary(r) for r in (data or [])]


@router.get("/search/repos", response_model=list[RepoSummary])
async def search_repos(q: str) -> list[RepoSummary]:
    if not q.strip():
        return []
    data = _check(
        await github_tools._request(
            "GET", "/search/repositories", params={"q": q, "per_page": 30}
        )
    )
    return [_summary(r) for r in ((data or {}).get("items") or [])]


@router.get("/repos/{owner}/{repo}", response_model=RepoSummary)
async def get_repo(owner: str, repo: str, fresh: bool = False) -> RepoSummary:
    data = await _fetch(
        f"repo:{owner}/{repo}", f"/repos/{owner}/{repo}", TTL_META_S, fresh=fresh
    )
    return _summary(data or {})


@router.get("/repos/{owner}/{repo}/branches", response_model=list[str])
async def list_branches(owner: str, repo: str, fresh: bool = False) -> list[str]:
    data = await _fetch(
        f"branches:{owner}/{repo}",
        f"/repos/{owner}/{repo}/branches",
        TTL_META_S,
        fresh=fresh,
        params={"per_page": 100},
    )
    return [str(b.get("name") or "") for b in (data or [])]


@router.get("/repos/{owner}/{repo}/tree", response_model=TreeResponse)
async def get_tree(
    owner: str, repo: str, ref: str, fresh: bool = False
) -> TreeResponse:
    """Every path in the repo at `ref`, in one request. See the module docstring."""
    data = await _fetch(
        f"tree:{owner}/{repo}@{ref}",
        f"/repos/{owner}/{repo}/git/trees/{ref}",
        TTL_META_S,
        fresh=fresh,
        params={"recursive": "1"},
    )
    entries = [
        TreeEntry(
            path=str(node.get("path") or ""),
            kind="dir" if node.get("type") == "tree" else "file",
            size=node.get("size"),
        )
        for node in ((data or {}).get("tree") or [])
    ]
    return TreeResponse(
        ref=ref, entries=entries, truncated=bool((data or {}).get("truncated"))
    )


@router.get("/repos/{owner}/{repo}/contents", response_model=DirResponse)
async def list_contents(
    owner: str, repo: str, path: str = "", ref: str = "", fresh: bool = False
) -> DirResponse:
    """One directory's entries — the lazy fallback for repos too large for `/tree`."""
    data = await _fetch(
        f"contents:{owner}/{repo}@{ref}:{path}",
        f"/repos/{owner}/{repo}/contents/{path.lstrip('/')}",
        TTL_META_S,
        fresh=fresh,
        params={"ref": ref} if ref else None,
    )
    if not isinstance(data, list):
        raise HTTPException(status_code=400, detail="not a directory")
    return DirResponse(
        path=path,
        entries=[
            TreeEntry(
                path=str(e.get("path") or ""),
                kind="dir" if e.get("type") == "dir" else "file",
                size=e.get("size"),
            )
            for e in data
        ],
    )


@router.get("/repos/{owner}/{repo}/file", response_model=FileResponse)
async def read_file(owner: str, repo: str, path: str, ref: str = "") -> FileResponse:
    """A single file's text. Backs the `github:` editor source scheme."""
    result = _check(
        await github_tools._read_file(
            {"repo": f"{owner}/{repo}", "path": path, "ref": ref}
        )
    )
    if "directory" in result:
        raise HTTPException(status_code=400, detail="path is a directory")
    return FileResponse(
        path=str(result.get("path") or path),
        content=str(result.get("content") or ""),
        truncated=bool(result.get("truncated")),
        url=result.get("url"),
    )


@router.get("/repos/{owner}/{repo}/readme", response_model=ReadmeResponse)
async def get_readme(owner: str, repo: str, ref: str = "") -> ReadmeResponse:
    """The repo's README, whatever it's called. 404 when there isn't one — a missing
    README is an ordinary state for the viewer, not an upstream failure."""
    raw = await github_tools._request(
        "GET",
        f"/repos/{owner}/{repo}/readme",
        params={"ref": ref} if ref else None,
    )
    if isinstance(raw, dict) and raw.get("error"):
        if "404" in str(raw["error"]):
            raise HTTPException(status_code=404, detail="no README in this repository")
        _check(raw)

    if (raw or {}).get("encoding") != "base64":
        raise HTTPException(status_code=415, detail="README isn't readable text")
    try:
        data = base64.b64decode(raw["content"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=502, detail=f"couldn't decode README: {exc}"
        ) from exc
    return ReadmeResponse(
        path=str(raw.get("path") or "README.md"),
        content=data[:MAX_FILE_BYTES].decode("utf-8", errors="replace"),
    )

"""HTTP surface for the Hugging Face browser (`/api/connectors/huggingface/*`).

Beside the connector rather than in a module of its own, for the same reason as
`github_routes.py` and `google_routes.py`: the token is held by `huggingface.py` and
reached through `huggingface_tools._request`, and a separate module would have to
import another module's private helpers, which the conventions forbid. Here it is an
ordinary intra-package call.

The agent has had `huggingface.searchModels` / `searchDatasets` / `repoInfo` /
`readFile` since the connector landed; a human had nothing. These routes are the same
four capabilities addressed to a browser, so "find me a dataset" and "show me a
dataset" are the same operation seen from two ends rather than two implementations
that can drift.

Errors keep `huggingface_tools`' errors-as-values convention right up to this
boundary, then become status codes — the caller here is a fetch, not an agent loop
that reads a message and decides what to do next.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.modules.connectors.providers import huggingface_tools as hf

router = APIRouter(prefix="/connectors/huggingface", tags=["connectors"])

# The Hub's anonymous limit is generous but browsing is repetitive — switching
# between two repos and back, re-opening the same config.json. Search results move
# slowly enough that a minute of staleness is invisible; a repo's file list and a
# file's contents at a pinned revision move even less.
TTL_SEARCH_S = 60.0
TTL_REPO_S = 300.0
CACHE_MAX = 200

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
    """Turn `huggingface_tools`' `{"error": …}` into an HTTP error.

    409 for "not connected" because that is the user's to fix and the pane renders a
    connect prompt for it; everything else is upstream's problem and reads as 502.
    """
    if isinstance(data, dict) and data.get("error"):
        message = str(data["error"])
        status = 409 if "isn't connected" in message else 502
        raise HTTPException(status_code=status, detail=message)
    return data


class RepoHit(BaseModel):
    """One search hit. Mirrors `huggingface_tools._repo_line` exactly — the agent and
    the pane are looking at the same rows."""

    id: str = ""
    type: str = "model"
    private: bool | None = None
    downloads: int | None = None
    likes: int | None = None
    updated_at: str | None = None
    # Only models carry a pipeline tag, and it's the single most useful field for
    # deciding whether a hit is the right kind of model at all.
    task: str | None = None
    tags: list[str] = Field(default_factory=list)
    url: str | None = None


class SearchResponse(BaseModel):
    results: list[RepoHit] = Field(default_factory=list)


class RepoInfo(RepoHit):
    files: list[str] = Field(default_factory=list)
    # Three-state, and the pane renders it that way: True/`"auto"`/`"manual"` mean
    # you must accept a licence on the Hub, False means open, None means the Hub
    # didn't say. "We don't know" is not "it's open".
    gated: Any | None = None
    library: str | None = None


class RepoFile(BaseModel):
    repo: str
    type: str
    path: str
    revision: str
    content: str
    truncated: bool = False
    url: str | None = None


def _kind(value: str) -> str:
    if value not in hf.REPO_TYPES:
        raise HTTPException(
            status_code=400, detail=f"type must be one of {sorted(hf.REPO_TYPES)}"
        )
    return value


@router.get("/search", response_model=SearchResponse)
async def search(
    q: str = Query(..., min_length=1),
    type: str = "model",
    task: str = "",
    sort: str = "downloads",
    fresh: bool = False,
) -> SearchResponse:
    """Search the Hub. `sort` is passed through (`downloads` | `likes` | `lastModified`)."""
    kind = _kind(type)
    key = f"search:{kind}:{q}:{task}:{sort}"
    if not fresh and (hit := _cached(key)) is not None:
        return SearchResponse(results=hit)
    data = _check(
        await hf._search(kind, {"query": q, "task": task, "sort": sort}),
    )
    results = list(data.get("results") or [])
    _store(key, results, TTL_SEARCH_S)
    return SearchResponse(results=[RepoHit(**r) for r in results])


@router.get("/mine", response_model=SearchResponse)
async def my_repos(type: str = "model", fresh: bool = False) -> SearchResponse:
    """The connected account's own repos."""
    kind = _kind(type)
    key = f"mine:{kind}"
    if not fresh and (hit := _cached(key)) is not None:
        return SearchResponse(results=hit)
    data = _check(await hf._list_repos({"type": kind}))
    results = list(data.get("results") or [])
    _store(key, results, TTL_SEARCH_S)
    return SearchResponse(results=[RepoHit(**r) for r in results])


@router.get("/repo", response_model=RepoInfo)
async def repo_info(
    repo: str = Query(..., min_length=1), type: str = "model", fresh: bool = False
) -> RepoInfo:
    """One repo's metadata and file list.

    `repo` is a query parameter rather than a path segment because a Hub id is
    `owner/name` — as a path it would need escaping at every call site, and one
    caller forgetting is a 404 that looks like a missing repo.
    """
    kind = _kind(type)
    key = f"repo:{kind}:{repo}"
    if not fresh and (hit := _cached(key)) is not None:
        return RepoInfo(**hit)
    data = _check(await hf._repo_info({"repo": repo, "type": kind}))
    _store(key, data, TTL_REPO_S)
    return RepoInfo(**data)


@router.get("/file", response_model=RepoFile)
async def repo_file(
    repo: str = Query(..., min_length=1),
    path: str = Query(..., min_length=1),
    type: str = "model",
    revision: str = "main",
    fresh: bool = False,
) -> RepoFile:
    """One text file from a repo — a README, a `config.json`, a dataset card.

    Binary files are refused upstream rather than streamed: `_read_file` stops at
    `MAX_FILE_BYTES` and reports a binary file as an error, so a click on a
    `.safetensors` shard can't pull gigabytes through the backend.
    """
    kind = _kind(type)
    key = f"file:{kind}:{repo}:{revision}:{path}"
    if not fresh and (hit := _cached(key)) is not None:
        return RepoFile(**hit)
    data = _check(
        await hf._read_file(
            {"repo": repo, "path": path, "type": kind, "revision": revision}
        )
    )
    _store(key, data, TTL_REPO_S)
    return RepoFile(**data)

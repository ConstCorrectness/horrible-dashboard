"""Agent tools for the Hugging Face connector.

Backend tools rather than frontend-declared ones: the token is held server-side, and
these have to work with no tab attached (a cron run, the `dash` REPL, `agent.ask_peer`).

Every tool name is `huggingface.*` — the orchestrator derives a tool's group from its
name prefix, so the prefix must match the connector id or the tools get split off from
the connector's blurb and guide.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.modules.connectors.providers import huggingface
from backend.sdk.registry import registry
from backend.sdk.types import AgentTool

logger = logging.getLogger(__name__)

API = "https://huggingface.co/api"
HUB = "https://huggingface.co"

# Enough for the model to work with, small enough not to blow the context window.
MAX_RESULTS = 20
MAX_FILE_BYTES = 100_000

# The Hub's two repo namespaces that this connector reads. Spaces are deliberately
# out: their interesting content is a running app, not a file tree.
REPO_TYPES = {"model": "models", "dataset": "datasets"}

_NOT_CONNECTED = {
    "error": (
        "Hugging Face isn't connected — connect it from the home page, then try again."
    )
}


async def _request(path: str, *, params: dict[str, Any] | None = None) -> Any:
    """One authenticated Hub API call, with errors as values."""
    import httpx

    token = await huggingface.token()
    if not token:
        return _NOT_CONNECTED
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            res = await client.get(
                f"{API}{path}",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.HTTPError as exc:
        return {"error": f"couldn't reach Hugging Face: {exc}"}
    return _decode(res, path)


def _decode(res: Any, what: str) -> Any:
    """Shared error mapping for a Hub response."""
    if res.status_code == 401:
        return {
            "error": (
                "Hugging Face rejected the stored token — reconnect it from the home "
                "page."
            )
        }
    if res.status_code == 403:
        return {
            "error": (
                f"no access to {what} — it may be private, or gated behind a licence "
                "you haven't accepted on the Hub."
            )
        }
    if res.status_code == 404:
        return {"error": f"{what} not found on the Hub"}
    if res.status_code >= 400:
        detail = ""
        try:
            detail = str(res.json().get("error") or "")
        except ValueError:
            detail = res.text[:200]
        return {"error": f"Hugging Face returned {res.status_code}: {detail}"}
    try:
        return res.json()
    except ValueError:
        return {"error": "Hugging Face returned an unreadable response"}


def _repo_line(repo: dict[str, Any], kind: str) -> dict[str, Any]:
    """One search hit, flattened. `id` is the owner/name the other tools take."""
    return {
        "id": repo.get("id"),
        "type": kind,
        "private": repo.get("private"),
        "downloads": repo.get("downloads"),
        "likes": repo.get("likes"),
        "updated_at": repo.get("lastModified"),
        # Only models carry a pipeline tag; it's the single most useful field for
        # deciding whether a hit is the right kind of model.
        "task": repo.get("pipeline_tag"),
        "tags": (repo.get("tags") or [])[:8],
        "url": f"{HUB}/{'datasets/' if kind == 'dataset' else ''}{repo.get('id')}",
    }


async def _search(kind: str, args: dict[str, Any]) -> Any:
    query = str(args.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}
    params: dict[str, Any] = {
        "search": query,
        "limit": MAX_RESULTS,
        "sort": str(args.get("sort") or "downloads"),
        "direction": -1,
        "full": "true",
    }
    if task := str(args.get("task") or "").strip():
        params["filter"] = task
    data = await _request(f"/{REPO_TYPES[kind]}", params=params)
    if isinstance(data, dict):
        return data
    return {"results": [_repo_line(r, kind) for r in (data or [])[:MAX_RESULTS]]}


async def _search_models(args: dict[str, Any]) -> Any:
    return await _search("model", args)


async def _search_datasets(args: dict[str, Any]) -> Any:
    return await _search("dataset", args)


async def _list_repos(args: dict[str, Any]) -> Any:
    """The connected user's own repos. Needs the username, which whoami gives us —
    the Hub has no "/user/repos" equivalent that infers it from the token."""
    import httpx

    token = await huggingface.token()
    if not token:
        return _NOT_CONNECTED
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            me = await client.get(
                f"{API}/whoami-v2", headers={"Authorization": f"Bearer {token}"}
            )
    except httpx.HTTPError as exc:
        return {"error": f"couldn't reach Hugging Face: {exc}"}
    profile = _decode(me, "your account")
    if isinstance(profile, dict) and profile.get("error"):
        return profile
    author = str((profile or {}).get("name") or "")
    if not author:
        return {"error": "couldn't determine the connected username"}

    kind = str(args.get("type") or "model")
    if kind not in REPO_TYPES:
        return {"error": f"type must be one of {sorted(REPO_TYPES)}"}
    data = await _request(
        f"/{REPO_TYPES[kind]}",
        params={"author": author, "limit": MAX_RESULTS, "sort": "lastModified"},
    )
    if isinstance(data, dict):
        return data
    return {
        "author": author,
        "repos": [_repo_line(r, kind) for r in (data or [])[:MAX_RESULTS]],
    }


async def _repo_info(args: dict[str, Any]) -> Any:
    repo = str(args.get("repo") or "").strip().strip("/")
    if not repo:
        return {"error": "repo (owner/name) is required"}
    kind = str(args.get("type") or "model")
    if kind not in REPO_TYPES:
        return {"error": f"type must be one of {sorted(REPO_TYPES)}"}
    data = await _request(f"/{REPO_TYPES[kind]}/{repo}")
    if isinstance(data, dict) and data.get("error"):
        return data
    info = _repo_line(data or {}, kind)
    info["files"] = [s.get("rfilename") for s in (data or {}).get("siblings") or []][
        :60
    ]
    info["gated"] = (data or {}).get("gated")
    info["library"] = (data or {}).get("library_name")
    return info


async def _read_file(args: dict[str, Any]) -> Any:
    """Read one file out of a repo.

    Note this bypasses `/api` entirely: file content lives on the `resolve` CDN path,
    not the JSON API, so it can't go through `_request`.
    """
    import httpx

    repo = str(args.get("repo") or "").strip().strip("/")
    path = str(args.get("path") or "").strip().lstrip("/")
    if not repo or not path:
        return {"error": "repo (owner/name) and path are required"}
    kind = str(args.get("type") or "model")
    if kind not in REPO_TYPES:
        return {"error": f"type must be one of {sorted(REPO_TYPES)}"}
    revision = str(args.get("revision") or "main")

    token = await huggingface.token()
    if not token:
        return _NOT_CONNECTED
    prefix = "datasets/" if kind == "dataset" else ""
    url = f"{HUB}/{prefix}{repo}/resolve/{revision}/{path}"
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            # Streamed so a multi-gigabyte weights file can't be pulled into memory
            # before we notice it isn't text.
            async with client.stream(
                "GET", url, headers={"Authorization": f"Bearer {token}"}
            ) as res:
                if res.status_code >= 400:
                    await res.aread()
                    return _decode(res, f"{repo}/{path}")
                kind_header = res.headers.get("content-type", "")
                raw = b""
                async for chunk in res.aiter_bytes():
                    raw += chunk
                    if len(raw) > MAX_FILE_BYTES:
                        break
    except httpx.HTTPError as exc:
        return {"error": f"couldn't reach Hugging Face: {exc}"}

    truncated = len(raw) > MAX_FILE_BYTES
    text = raw[:MAX_FILE_BYTES].decode("utf-8", errors="replace")
    # Binary weights decode to replacement-character soup; say so instead of handing
    # the model a page of noise it will try to interpret.
    if "\x00" in text[:1024]:
        return {
            "error": (
                f"{path} looks like a binary file ({kind_header or 'unknown type'}) — "
                "read a config, README, or other text file instead."
            )
        }
    return {
        "repo": repo,
        "type": kind,
        "path": path,
        "revision": revision,
        "content": text,
        "truncated": truncated,
        "url": url,
    }


_TOOLS = [
    AgentTool(
        name="huggingface.searchModels",
        description=(
            "Search models on the Hugging Face Hub by name, author, or tag. Use `task` "
            "to filter to a pipeline (e.g. text-generation) when the user names one."
        ),
        parameters={
            "query": {"type": "string", "description": "Search terms."},
            "task": {
                "type": "string",
                "description": (
                    "Pipeline tag to filter by, e.g. text-generation, "
                    "text-to-image, automatic-speech-recognition."
                ),
            },
            "sort": {
                "type": "string",
                "enum": ["downloads", "likes", "lastModified"],
                "description": "Ranking. Defaults to downloads.",
            },
        },
        required=["query"],
        handler=_search_models,
        group="huggingface",
    ),
    AgentTool(
        name="huggingface.searchDatasets",
        description="Search datasets on the Hugging Face Hub by name, author, or tag.",
        parameters={
            "query": {"type": "string", "description": "Search terms."},
            "sort": {
                "type": "string",
                "enum": ["downloads", "likes", "lastModified"],
                "description": "Ranking. Defaults to downloads.",
            },
        },
        required=["query"],
        handler=_search_datasets,
        group="huggingface",
    ),
    AgentTool(
        name="huggingface.listRepos",
        description=(
            "List the connected user's own models or datasets, most recently updated "
            "first. Includes private repos."
        ),
        parameters={
            "type": {
                "type": "string",
                "enum": ["model", "dataset"],
                "description": "Which namespace to list. Defaults to model.",
            },
        },
        required=[],
        handler=_list_repos,
        group="huggingface",
    ),
    AgentTool(
        name="huggingface.repoInfo",
        description=(
            "Metadata for one model or dataset: task, library, licence gating, and the "
            "list of files it contains. Use this before readFile to find a path."
        ),
        parameters={
            "repo": {"type": "string", "description": "Repo id as owner/name."},
            "type": {
                "type": "string",
                "enum": ["model", "dataset"],
                "description": "Defaults to model.",
            },
        },
        required=["repo"],
        handler=_repo_info,
        group="huggingface",
    ),
    AgentTool(
        name="huggingface.readFile",
        description=(
            "Read a text file from a Hub repo — README.md, config.json, a dataset "
            "script. Text only; weights and other binaries are refused."
        ),
        parameters={
            "repo": {"type": "string", "description": "Repo id as owner/name."},
            "path": {
                "type": "string",
                "description": "Path within the repo, e.g. README.md or config.json.",
            },
            "type": {
                "type": "string",
                "enum": ["model", "dataset"],
                "description": "Defaults to model.",
            },
            "revision": {
                "type": "string",
                "description": "Branch, tag, or commit. Defaults to main.",
            },
        },
        required=["repo", "path"],
        handler=_read_file,
        group="huggingface",
    ),
]


def register_agent_tools() -> None:
    for tool in _TOOLS:
        registry.agent_tools[tool.name] = tool

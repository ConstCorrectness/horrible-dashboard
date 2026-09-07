"""Agent tools for the Hugging Face connector.

Backend tools rather than frontend-declared ones: the token is held server-side, and
these have to work with no tab attached (a cron run, the `dash` REPL, `agent.ask_peer`).

Every tool name is `huggingface.*` — the orchestrator derives a tool's group from its
name prefix, so the prefix must match the connector id or the tools get split off from
the connector's blurb and guide.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
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



# --- writing back -------------------------------------------------------------
#
# `write-repos` only: this connector never asks for `manage-repos`, so nothing here
# can delete a repo. Uploading a bad checkpoint wastes bandwidth; deleting a repo
# destroys work, and the two are different scopes for exactly that reason.


def _write_refusal() -> dict[str, Any] | None:
    """The two checks both write tools share, in the order that gives the right
    message.

    **Not connected is checked first.** A disconnected account told "this
    connection was granted read access only" would go looking for a scope problem
    that does not exist. Only a real, connected, read-scoped token gets the
    reconnect instruction — which it needs, because a connection made before
    `write-repos` was requested still works for reads and would otherwise fail with
    a bare 403 several megabytes into an upload.
    """
    from backend.modules.connectors import store

    if not store.is_connected(huggingface.CONNECTOR_ID):
        return dict(_NOT_CONNECTED)
    if huggingface.can_write():
        return None
    return {
        "error": (
            "this Hugging Face connection was granted read access only. Disconnect "
            "and reconnect the Hugging Face tile to grant upload access — it asks "
            "for `write-repos`, which can create and upload but never delete."
        )
    }


async def _create_repo(args: dict[str, Any]) -> dict[str, Any]:
    refusal = _write_refusal()
    if refusal:
        return refusal
    tok = await huggingface.token()
    if not tok:
        return {"error": "Hugging Face is not connected"}
    repo_id = str(args.get("repo_id") or "")
    if not repo_id:
        return {"error": "repo_id is required (e.g. `me/my-model`)"}
    private = bool(args.get("private", True))
    repo_type = str(args.get("repo_type") or "model")

    def work() -> str:
        from huggingface_hub import HfApi

        return HfApi(token=tok).create_repo(
            repo_id=repo_id, repo_type=repo_type, private=private, exist_ok=True
        )

    try:
        url = await asyncio.to_thread(work)
    except Exception as exc:  # noqa: BLE001 — vendor errors are shown verbatim
        return {"error": f"could not create the repo: {exc}"}
    return {"repo": repo_id, "url": str(url), "private": private}


async def _upload_folder(args: dict[str, Any]) -> dict[str, Any]:
    refusal = _write_refusal()
    if refusal:
        return refusal
    tok = await huggingface.token()
    if not tok:
        return {"error": "Hugging Face is not connected"}
    repo_id = str(args.get("repo_id") or "")
    folder = str(args.get("folder") or "")
    if not repo_id or not folder:
        return {"error": "repo_id and folder are both required"}

    path = Path(folder).expanduser().resolve()
    if not path.is_dir():
        return {"error": f"no such directory: {folder}"}
    private = bool(args.get("private", True))
    repo_type = str(args.get("repo_type") or "model")
    message = str(args.get("message") or "Uploaded from horrible-dashboard")

    def work() -> str:
        from huggingface_hub import HfApi

        api = HfApi(token=tok)
        api.create_repo(
            repo_id=repo_id, repo_type=repo_type, private=private, exist_ok=True
        )
        return api.upload_folder(
            folder_path=str(path),
            repo_id=repo_id,
            repo_type=repo_type,
            commit_message=message,
            # A checkpoint directory carries optimizer state nobody wants on the
            # Hub, and a project venv would be a catastrophe to upload.
            ignore_patterns=["**/.venv/**", "**/__pycache__/**", "**/optimizer.pt"],
        )

    try:
        url = await asyncio.to_thread(work)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"upload failed: {exc}"}
    return {"repo": repo_id, "url": str(url), "uploaded": str(path)}


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
    AgentTool(
        name="huggingface.createRepo",
        description="Create a Hugging Face repo (model or dataset), private by "
        "default. Needs the write scope — reconnect the tile if it was granted "
        "read-only.",
        parameters={
            "repo_id": {"type": "string", "description": "e.g. `me/my-model`"},
            "repo_type": {"type": "string", "description": "model | dataset"},
            "private": {"type": "boolean", "description": "Default true"},
        },
        required=["repo_id"],
        side_effect=True,
        specifier_template="{repo_id}",
        handler=_create_repo,
        group="huggingface",
    ),
    AgentTool(
        name="huggingface.uploadFolder",
        description="Upload a local folder — a trained checkpoint, or a dataset "
        "built here — to a Hugging Face repo, creating it if needed. Skips venvs, "
        "caches and optimizer state.",
        parameters={
            "repo_id": {"type": "string", "description": "e.g. `me/my-model`"},
            "folder": {"type": "string", "description": "Absolute path to upload"},
            "repo_type": {"type": "string", "description": "model | dataset"},
            "private": {"type": "boolean", "description": "Default true"},
            "message": {"type": "string", "description": "Commit message"},
        },
        required=["repo_id", "folder"],
        side_effect=True,
        specifier_template="{folder} to {repo_id}",
        handler=_upload_folder,
        group="huggingface",
    ),
]


def register_agent_tools() -> None:
    for tool in _TOOLS:
        registry.agent_tools[tool.name] = tool

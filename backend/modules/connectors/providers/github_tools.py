"""Agent tools for the GitHub connector.

Backend tools rather than frontend-declared ones: the token is held server-side, and
these have to work with no tab attached (a cron run, the `dash` REPL, `agent.ask_peer`).

Every tool name is `github.*` — the orchestrator derives a tool's group from its name
prefix, so the prefix must match the connector id or the tools get split off from the
connector's blurb and guide.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.modules.connectors.providers import github
from backend.sdk.registry import registry
from backend.sdk.types import AgentTool

logger = logging.getLogger(__name__)

API = "https://api.github.com"

# Enough for the model to work with, small enough not to blow the context window.
MAX_RESULTS = 20
MAX_FILE_BYTES = 100_000

_NOT_CONNECTED = {
    "error": "GitHub isn't connected — connect it from the home page, then try again."
}


async def _request(
    method: str, path: str, *, params: dict[str, Any] | None = None, json: Any = None
) -> Any:
    """One authenticated GitHub API call, with errors as values."""
    import httpx

    token = await github.token()
    if not token:
        return _NOT_CONNECTED
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            res = await client.request(
                method,
                f"{API}{path}",
                params=params,
                json=json,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
    except httpx.HTTPError as exc:
        return {"error": f"couldn't reach GitHub: {exc}"}

    if res.status_code == 401:
        return {
            "error": "GitHub rejected the stored token — reconnect GitHub from the home page."
        }
    if res.status_code == 403 and "rate limit" in res.text.lower():
        return {"error": "GitHub rate limit hit — wait a minute and try again."}
    if res.status_code >= 400:
        detail = ""
        try:
            detail = str(res.json().get("message") or "")
        except ValueError:
            detail = res.text[:200]
        return {"error": f"GitHub returned {res.status_code}: {detail}"}
    return res.json()


def _repo_line(repo: dict[str, Any]) -> dict[str, Any]:
    return {
        "repo": repo.get("full_name"),
        "description": repo.get("description"),
        "private": repo.get("private"),
        "language": repo.get("language"),
        "stars": repo.get("stargazers_count"),
        "updated_at": repo.get("updated_at"),
        "url": repo.get("html_url"),
    }


async def _search_code(args: dict[str, Any]) -> Any:
    query = str(args.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}
    repo = str(args.get("repo") or "").strip()
    if repo:
        query = f"{query} repo:{repo}"
    data = await _request(
        "GET", "/search/code", params={"q": query, "per_page": MAX_RESULTS}
    )
    if isinstance(data, dict) and data.get("error"):
        return data
    items = (data or {}).get("items") or []
    if not items:
        return {
            "results": [],
            "hint": (
                "No matches. GitHub code search only covers the default branch, and "
                "needs a scoping qualifier for broad terms — try repo:owner/name."
            ),
        }
    return {
        "total": (data or {}).get("total_count"),
        "results": [
            {
                "repo": (i.get("repository") or {}).get("full_name"),
                "path": i.get("path"),
                "url": i.get("html_url"),
            }
            for i in items[:MAX_RESULTS]
        ],
    }


async def _search_repos(args: dict[str, Any]) -> Any:
    query = str(args.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}
    data = await _request(
        "GET", "/search/repositories", params={"q": query, "per_page": MAX_RESULTS}
    )
    if isinstance(data, dict) and data.get("error"):
        return data
    return {
        "total": (data or {}).get("total_count"),
        "results": [
            _repo_line(r) for r in ((data or {}).get("items") or [])[:MAX_RESULTS]
        ],
    }


async def _list_repos(args: dict[str, Any]) -> Any:
    data = await _request(
        "GET",
        "/user/repos",
        params={
            "per_page": MAX_RESULTS,
            "sort": "updated",
            "affiliation": "owner,collaborator,organization_member",
        },
    )
    if isinstance(data, dict) and data.get("error"):
        return data
    return {"repos": [_repo_line(r) for r in (data or [])[:MAX_RESULTS]]}


async def _read_file(args: dict[str, Any]) -> Any:
    import base64

    repo = str(args.get("repo") or "").strip()
    path = str(args.get("path") or "").strip().lstrip("/")
    if not repo or not path:
        return {"error": "repo (owner/name) and path are required"}
    params = {"ref": args["ref"]} if args.get("ref") else None
    data = await _request("GET", f"/repos/{repo}/contents/{path}", params=params)
    if isinstance(data, dict) and data.get("error"):
        return data
    if isinstance(data, list):
        return {
            "directory": path,
            "entries": [{"name": e.get("name"), "type": e.get("type")} for e in data],
        }
    if (data or {}).get("encoding") != "base64":
        return {"error": f"{path} isn't a readable text file"}
    try:
        raw = base64.b64decode(data["content"])
    except (KeyError, ValueError) as exc:
        return {"error": f"couldn't decode {path}: {exc}"}
    truncated = len(raw) > MAX_FILE_BYTES
    text = raw[:MAX_FILE_BYTES].decode("utf-8", errors="replace")
    return {
        "repo": repo,
        "path": path,
        "content": text,
        "truncated": truncated,
        "url": data.get("html_url"),
    }


async def _list_issues(args: dict[str, Any]) -> Any:
    repo = str(args.get("repo") or "").strip()
    if not repo:
        return {"error": "repo (owner/name) is required"}
    state = str(args.get("state") or "open")
    data = await _request(
        "GET", f"/repos/{repo}/issues", params={"state": state, "per_page": MAX_RESULTS}
    )
    if isinstance(data, dict) and data.get("error"):
        return data
    return {
        "issues": [
            {
                "number": i.get("number"),
                "title": i.get("title"),
                "state": i.get("state"),
                "author": (i.get("user") or {}).get("login"),
                # The REST issues endpoint returns PRs too; the caller usually cares.
                "is_pull_request": "pull_request" in i,
                "url": i.get("html_url"),
            }
            for i in (data or [])[:MAX_RESULTS]
        ]
    }


_TOOLS = [
    AgentTool(
        name="github.searchCode",
        description=(
            "Search code on GitHub. Only indexes the default branch. Broad terms need "
            "a scope — pass `repo` (or a repo:/org: qualifier) or expect no results."
        ),
        parameters={
            "query": {
                "type": "string",
                "description": "Search terms, optionally with GitHub qualifiers like language: or path:.",
            },
            "repo": {
                "type": "string",
                "description": "Limit to one repository, as owner/name.",
            },
        },
        required=["query"],
        handler=_search_code,
        group="github",
    ),
    AgentTool(
        name="github.searchRepos",
        description="Search for repositories on GitHub by name, topic, or description.",
        parameters={
            "query": {"type": "string", "description": "Search terms."},
        },
        required=["query"],
        handler=_search_repos,
        group="github",
    ),
    AgentTool(
        name="github.listRepos",
        description="List the connected user's own repositories, most recently updated first.",
        parameters={},
        required=[],
        handler=_list_repos,
        group="github",
    ),
    AgentTool(
        name="github.readFile",
        description=(
            "Read a file from a repository at a known path. Prefer this over "
            "github.searchCode when you already know where the file is."
        ),
        parameters={
            "repo": {"type": "string", "description": "Repository as owner/name."},
            "path": {"type": "string", "description": "Path within the repository."},
            "ref": {
                "type": "string",
                "description": "Branch, tag, or commit SHA. Defaults to the default branch.",
            },
        },
        required=["repo", "path"],
        handler=_read_file,
        group="github",
    ),
    AgentTool(
        name="github.listIssues",
        description="List issues and pull requests in a repository.",
        parameters={
            "repo": {"type": "string", "description": "Repository as owner/name."},
            "state": {
                "type": "string",
                "enum": ["open", "closed", "all"],
                "description": "Which issues to return. Defaults to open.",
            },
        },
        required=["repo"],
        handler=_list_issues,
        group="github",
    ),
]


def register_agent_tools() -> None:
    for tool in _TOOLS:
        registry.agent_tools[tool.name] = tool

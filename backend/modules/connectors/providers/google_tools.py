"""Agent tools for the Google connector.

Named `google.*` to match the connector id — the orchestrator groups tools by their
name prefix, so the prefix is what ties these to the connector's blurb and guide.

Read-only, so they pass the permission gate straight through, which is consistent with
the `drive.readonly` scope the connector actually holds.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.modules.connectors.providers import drive_api
from backend.sdk.registry import registry
from backend.sdk.types import AgentTool

logger = logging.getLogger(__name__)


def _escape(term: str) -> str:
    """Escape a term for a Drive `q` string literal. Drive uses backslash escapes
    inside single quotes; an unescaped apostrophe is a query syntax error, which is a
    silly way to lose a search for "Rob's notes"."""
    return term.replace("\\", "\\\\").replace("'", "\\'")


def _summarize(f: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f.get("id"),
        "name": f.get("name"),
        "type": f.get("mimeType"),
        "modified": f.get("modifiedTime"),
        "url": f.get("webViewLink"),
    }


async def _drive_search(args: dict[str, Any]) -> Any:
    text = str(args.get("query") or "").strip()
    if not text:
        return {"error": "query is required"}
    limit = min(int(args.get("limit") or 10), drive_api.MAX_TOOL_RESULTS)

    clauses = [
        f"name contains '{_escape(text)}' or fullText contains '{_escape(text)}'"
    ]
    clauses.append("trashed = false")
    if args.get("readable_only", True):
        types = " or ".join(f"mimeType='{m}'" for m in sorted(drive_api.READABLE_MIMES))
        clauses.append(f"({types})")
    q = " and ".join(f"({c})" for c in clauses)

    data = await drive_api.list_files(query=q, page_size=limit)
    if isinstance(data, dict) and data.get("error"):
        return data
    files = (data or {}).get("files") or []
    if not files:
        return {
            "results": [],
            "hint": (
                "No matches. Drive's fullText index only covers documents it has "
                "indexed, and matching is on whole words — try a distinctive term from "
                "the document, or search by file name."
            ),
        }
    return {"results": [_summarize(f) for f in files[:limit]]}


async def _drive_read(args: dict[str, Any]) -> Any:
    file_id = str(args.get("file_id") or "").strip()
    if not file_id:
        return {"error": "file_id is required (get one from google.driveSearch)"}

    meta = await drive_api.request(
        "GET", f"/files/{file_id}", params={"fields": drive_api.FILE_FIELDS}
    )
    if isinstance(meta, dict) and meta.get("error"):
        return meta

    mime = str(meta.get("mimeType") or "")
    name = str(meta.get("name") or "")
    text = await drive_api.extract_text(file_id, mime, name)
    if isinstance(text, dict):
        return text
    return {
        "id": file_id,
        "name": name,
        "type": mime,
        "url": meta.get("webViewLink"),
        "content": text,
        "truncated": len(text) >= drive_api.MAX_TEXT_CHARS,
    }


async def _drive_sync(args: dict[str, Any]) -> Any:
    """Queue a sync rather than run it inline: a full crawl can take minutes, and an
    agent turn shouldn't block on it."""
    from backend.modules.connectors import store
    from backend.modules.connectors.providers import google_sync

    if not store.is_connected("google"):
        return drive_api.NOT_CONNECTED
    library = google_sync.target_library(
        {"library": args["library"]} if args.get("library") else {}
    )
    task_id = google_sync.enqueue_sync(library, full=bool(args.get("full")))
    return {
        "queued": True,
        "task_id": task_id,
        "library": library,
        "note": (
            "Running in the background. Search it with library.search once it finishes; "
            "a first sync of a large Drive takes a while."
        ),
    }


_TOOLS = [
    AgentTool(
        name="google.driveSearch",
        description=(
            "Search the connected Google Drive by file name and document contents. "
            "Returns file ids to pass to google.driveRead."
        ),
        parameters={
            "query": {"type": "string", "description": "Words to look for."},
            "limit": {"type": "number", "description": "Max results (default 10)."},
            "readable_only": {
                "type": "boolean",
                "description": (
                    "Only return files whose text can be read (Docs, PDFs, text). "
                    "Default true."
                ),
            },
        },
        required=["query"],
        handler=_drive_search,
        group="google",
    ),
    AgentTool(
        name="google.driveRead",
        description=(
            "Read a Drive file's text by id: Google Docs are exported, PDFs are "
            "parsed, text files are downloaded."
        ),
        parameters={
            "file_id": {
                "type": "string",
                "description": "Drive file id, from google.driveSearch.",
            },
        },
        required=["file_id"],
        handler=_drive_read,
        group="google",
    ),
    AgentTool(
        name="google.syncDrive",
        description=(
            "Sync Google Drive documents into a knowledge library so they can be "
            "semantically searched with library.search. Runs in the background. Use "
            "this for 'remember my Drive' style asks; use google.driveSearch to look "
            "something up right now."
        ),
        parameters={
            "library": {
                "type": "string",
                "description": "Target library. Defaults to the configured one.",
            },
            "full": {
                "type": "boolean",
                "description": (
                    "Re-crawl everything instead of only what changed. Rarely needed."
                ),
            },
        },
        required=[],
        handler=_drive_sync,
        # Writes into a library, so it goes through the permission gate — unlike the
        # read-only Drive tools.
        side_effect=True,
        specifier_template="{library}",
        group="google",
    ),
]


def register_agent_tools() -> None:
    for tool in _TOOLS:
        registry.agent_tools[tool.name] = tool

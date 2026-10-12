"""The `kb` agent tool group: retrieval over the user's knowledge base, and curating it.

Backend tools, so retrieval works with no browser tab attached (the library's own
`library.search` is browser-declared and goes dead without one). Grouped, so they
cost nothing on a turn that never touches the knowledge base — the main agent learns
the KB exists from a one-line nudge in its system prompt and loads the group when a
question calls for it.

`kb.search` is the one that carries the weight. Its passages come back numbered and
with a ready-made citation (`Title › Section`) and a deep link, because the failure
this exists to prevent is an answer that *used* the user's material without saying
which part of it.

Importing a site **is** a tool here, unlike docviewer's capture: the user asked for
the agent to be able to grab sites. It is `side_effect=True`, so it goes through the
permission gate, and it says up front how many pages it will fetch.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from backend.modules.kb import imports, profiles, service, store
from backend.modules.kb.models import SearchRequest
from backend.sdk.registry import registry
from backend.sdk.types import AgentTool

logger = logging.getLogger(__name__)

_GROUP = "kb"


def _agent_scope() -> list[str] | None:
    """The calling agent's knowledge scope (`AgentSpec.knowledge_scope`), if any."""
    from backend.modules.agent.current import current_agent_id
    from backend.modules.agent.roster import get_agent

    spec = get_agent(current_agent_id())
    scope = getattr(spec, "knowledge_scope", None) if spec is not None else None
    return list(scope) if scope else None


def _cite(index: int, p: Any) -> str:
    label = p.title + (f" › {p.section}" if p.section else "")
    return f"[{index}] {label}"


async def _search(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}
    collection = args.get("collection")
    collections = [str(collection)] if collection else _agent_scope()
    try:
        res = await service.search(
            SearchRequest(
                query=query,
                collections=collections,
                item_id=args.get("itemId") or None,
                limit=max(1, min(int(args.get("limit") or 8), 20)),
            )
        )
    except service.SearchUnavailable as exc:
        return {"error": str(exc)}
    if not res.passages:
        summary = store.summary()
        return {
            "query": query,
            "passages": [],
            "note": (
                "Nothing in the knowledge base matched"
                + (f" within {', '.join(collections)}" if collections else "")
                + f". It holds {summary['indexed']} indexed items across "
                f"{len(res.libraries)} sources. Empty means not in the user's "
                "material, not that the answer doesn't exist."
            ),
        }
    return {
        "query": query,
        "scope": collections or "all",
        "passages": [
            {
                "n": i,
                "cite": _cite(i, p),
                "link": p.link,
                "itemId": p.item_id,
                "offline": bool(p.docpage_id or p.artifact_id),
                "score": round(p.score, 3),
                "text": p.text,
            }
            for i, p in enumerate(res.passages, start=1)
        ],
    }


def _item_brief(item: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": item.id,
        "kind": item.kind,
        "title": item.title,
        "url": item.url,
        "status": item.status,
        "indexed": item.index_status,
    }
    if item.collections:
        out["collections"] = item.collections
    if item.tags:
        out["tags"] = item.tags
    if item.progress is not None and item.progress.total:
        out["progress"] = f"{item.progress.done}/{item.progress.total}"
    if item.notes:
        out["notes"] = item.notes[:300]
    return out


async def _list(args: dict[str, Any]) -> dict[str, Any]:
    rows, total = store.list_items(
        collection=args.get("collection") or None,
        status=args.get("status") or None,
        kind=args.get("kind") or None,
        q=args.get("query") or None,
        top_level=not args.get("query"),
        limit=max(1, min(int(args.get("limit") or 30), 100)),
    )
    return {
        "total": total,
        "items": [_item_brief(i) for i in service.enrich(rows)],
        "collections": [c["name"] for c in store.list_collections()],
    }


async def _get(args: dict[str, Any]) -> dict[str, Any]:
    item_id = str(args.get("itemId") or "")
    node = service.tree(item_id)
    if node is None:
        return {"error": "no such item"}
    out = _item_brief(node.item)
    out["notes"] = node.item.notes

    def walk(n: Any, depth: int) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for child in n.children:
            rows.append(
                {
                    "id": child.item.id,
                    "depth": depth,
                    "kind": child.item.kind,
                    "title": child.item.title,
                    "status": child.item.status,
                }
            )
            rows.extend(walk(child, depth + 1))
        return rows

    children = walk(node, 1)
    if children:
        out["children"] = children[:200]
    return out


async def _save(args: dict[str, Any]) -> dict[str, Any]:
    url = str(args.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return {"error": "url must be an http(s) URL"}
    fields = {
        k: v
        for k, v in {
            "kind": args.get("kind"),
            "title": args.get("title"),
            "notes": args.get("notes"),
            "tags": args.get("tags"),
        }.items()
        if v
    }
    collections = args.get("collections") or None
    row, created = store.upsert_by_url(url, collections=collections, **fields)
    if created and "kind" not in fields:
        store.update_item(row["id"], kind="article")
    mode = str(args.get("index") or "text")
    if mode in ("text", "archive") and row["index_mode"] != mode:
        try:
            await service.set_index_mode(row["id"], mode)
        except ValueError as exc:
            return {"error": str(exc)}
    service.emit(row["id"])
    item = service.item_model(row["id"])
    return {"saved": _item_brief(item), "created": created}


async def _set_status(args: dict[str, Any]) -> dict[str, Any]:
    status = str(args.get("status") or "")
    if status not in store.STATUSES:
        return {"error": f"status must be one of {', '.join(store.STATUSES)}"}
    row = store.update_item(str(args.get("itemId") or ""), status=status)
    if row is None:
        return {"error": "no such item"}
    service.emit(row["id"])
    return {"item": _item_brief(service.item_model(row["id"]))}


async def _import(args: dict[str, Any]) -> dict[str, Any]:
    try:
        started = await imports.start_import(
            profile_id=args.get("profileId") or None,
            url=args.get("url") or None,
            max_pages=int(args["maxPages"]) if args.get("maxPages") else None,
            collections=args.get("collections") or None,
        )
    except imports.ImportFailed as exc:
        return {"error": str(exc)}
    plan = started["plan"]
    return {
        "started": not started["resumed"],
        "resumed": started["resumed"],
        "rootItemId": started["root_item_id"],
        "profile": plan["id"],
        "maxPages": plan.get("max_pages"),
        "note": (
            "Running in the background (headless browser, ~10-40 s a page). Pages "
            "appear in the Knowledge pane as they land; kb.search finds them once "
            "indexed."
        ),
    }


_PROFILE_GUIDE = Path(__file__).with_name("profile_guide.md")


async def _profiles(args: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "profiles": [
            {
                "id": p["id"],
                "label": p.get("label"),
                "description": p.get("description", ""),
                "builtin": p.get("builtin", False),
                "matches": p.get("match", []),
            }
            for p in profiles.list_profiles()
        ]
    }
    if args.get("howToWrite"):
        # The authoring guide rides here rather than in the group guide: it is only
        # wanted on the rare turn that writes a profile, not on every search.
        out["howToWrite"] = _PROFILE_GUIDE.read_text(encoding="utf-8")
    return out


async def _save_profile(args: dict[str, Any]) -> dict[str, Any]:
    spec = args.get("profile")
    if not isinstance(spec, dict):
        return {"error": "profile must be an object"}
    try:
        saved = profiles.save_profile(spec)
    except ValueError as exc:
        return {"error": str(exc)}
    return {"saved": saved["id"]}


_TOOLS = [
    AgentTool(
        name="kb.search",
        description=(
            "Search the user's knowledge base — their saved articles, imported ML "
            "primers and course lessons — and return numbered passages with a "
            "citation ('[n] Title › Section') and a link. Prefer this over web search "
            "for conceptual questions; cite the passages you use."
        ),
        handler=_search,
        parameters={
            "query": {"type": "string", "description": "What to look for."},
            "collection": {
                "type": "string",
                "description": "Optional: only this collection (e.g. 'ml').",
            },
            "itemId": {
                "type": "string",
                "description": "Optional: only this item and everything under it "
                "(one course, one site).",
            },
            "limit": {
                "type": "number",
                "description": "Passages to return. Default 8.",
            },
        },
        required=["query"],
        group=_GROUP,
    ),
    AgentTool(
        name="kb.list",
        description=(
            "List items in the knowledge base (courses, primers, bookmarks…) with "
            "their reading status and progress. Filter by collection, status "
            "(to_read|reading|done|archived), kind, or a text query."
        ),
        handler=_list,
        parameters={
            "collection": {"type": "string", "description": "Collection name."},
            "status": {"type": "string", "description": "Reading status."},
            "kind": {"type": "string", "description": "e.g. course, primer, bookmark."},
            "query": {"type": "string", "description": "Match titles, URLs and notes."},
            "limit": {"type": "number", "description": "Default 30."},
        },
        group=_GROUP,
    ),
    AgentTool(
        name="kb.get",
        description=(
            "One item's details: notes, status, and for a course or site its "
            "chapters/lessons with their status."
        ),
        handler=_get,
        parameters={
            "itemId": {"type": "string", "description": "From kb.list/kb.search."}
        },
        required=["itemId"],
        group=_GROUP,
    ),
    AgentTool(
        name="kb.save",
        description=(
            "Save a URL to the knowledge base. index: 'link' (just track it), 'text' "
            "(default — fetch and index it for search) or 'archive' (also keep an "
            "offline copy). Saving a URL that is already saved updates it."
        ),
        handler=_save,
        parameters={
            "url": {"type": "string", "description": "The page to save."},
            "kind": {"type": "string", "description": "article, paper, video, course…"},
            "title": {"type": "string", "description": "Optional title."},
            "collections": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Collections to file it in.",
            },
            "tags": {"type": "array", "items": {"type": "string"}},
            "notes": {"type": "string", "description": "Why it's worth keeping."},
            "index": {"type": "string", "enum": ["link", "text", "archive"]},
        },
        required=["url"],
        side_effect=True,
        group=_GROUP,
    ),
    AgentTool(
        name="kb.setStatus",
        description="Mark an item to_read, reading, done or archived.",
        handler=_set_status,
        parameters={
            "itemId": {"type": "string"},
            "status": {
                "type": "string",
                "enum": ["to_read", "reading", "done", "archived"],
            },
        },
        required=["itemId", "status"],
        side_effect=True,
        group=_GROUP,
    ),
    AgentTool(
        name="kb.import",
        description=(
            "Import a whole site or course into the knowledge base: every page is "
            "archived for offline reading and indexed for kb.search, as a tree with "
            "progress. Pass a URL (a matching profile is chosen — an apxml course URL "
            "imports that course) or a profileId from kb.profiles. Runs for minutes in "
            "the background; re-running resumes."
        ),
        handler=_import,
        parameters={
            "url": {"type": "string", "description": "A page of the site or course."},
            "profileId": {"type": "string", "description": "From kb.profiles."},
            "maxPages": {
                "type": "number",
                "description": "Page cap (default: the profile's).",
            },
            "collections": {"type": "array", "items": {"type": "string"}},
        },
        side_effect=True,
        specifier_template="{url}{profileId}",
        group=_GROUP,
    ),
    AgentTool(
        name="kb.profiles",
        description=(
            "List the site import profiles — which sites kb.import knows how to "
            "crawl, and the URL patterns each one matches."
        ),
        handler=_profiles,
        parameters={
            "howToWrite": {
                "type": "boolean",
                "description": "Also return the guide to writing a new profile.",
            }
        },
        group=_GROUP,
    ),
    AgentTool(
        name="kb.saveProfile",
        description=(
            "Add or replace a site import profile (kb.profiles with howToWrite=true "
            "explains the fields). Validated before it is saved; built-in ids can't be "
            "replaced."
        ),
        handler=_save_profile,
        parameters={
            "profile": {
                "type": "object",
                "description": "id, label, start_urls, allow_domains, allow_patterns, "
                "deny_patterns, index_pages, frontier, content_selector, hierarchy, "
                "path_root, path_kinds, page_kind, keep_scripts, max_pages, "
                "collections, tags, match, title_strip.",
            }
        },
        required=["profile"],
        side_effect=True,
        group=_GROUP,
    ),
]


def register_agent_tools() -> None:
    """Register the group. Called from `backend/app.py` at startup."""
    for tool in _TOOLS:
        registry.agent_tools[tool.name] = tool

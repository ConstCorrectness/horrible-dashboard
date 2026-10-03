"""The `scrive` agent tool group: read, search, draft and edit MyST pages.

Backend tools, so they work with no tab open (cron, the `dash` REPL, a delegated
peer); an open editor picks up every write through the watcher, which marks it
`origin: agent` so the page shows it as a reviewable edit (changed blocks marked,
Keep / Undo).

The agent speaks **MyST text**, never editor JSON, and edits by section
(`sections.py`) so every byte it does not touch stays as the author wrote it.

Two rules this group exists to keep:

- **No tool publishes or sends anything.** The agent drafts; publishing is a
  person's click (see docs/modules/scrive.mdx). There is deliberately no tool here
  that pushes a site, posts a thread or uploads a video.
- **Plan, then write.** For a whole new page the agent proposes an outline
  (`scrive.proposeOutline`), a person approves it in the outline pane, and only then
  does the agent fill the sections. Approval is a route behind a button, not a tool.

The tool prefix is `scrive`, matching the module id: the orchestrator groups tools
by name prefix.
"""

from __future__ import annotations

import functools
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from backend.modules.scrive import (
    clips,
    media,
    critique,
    outbox,
    outlines,
    search,
    sections,
    store,
    templates,
    themes,
)
from backend.modules.ws import broadcast_event
from backend.sdk.registry import registry
from backend.sdk.types import AgentTool

logger = logging.getLogger(__name__)

#: Past this, `readPage` returns the outline and asks for a section instead.
_MAX_TEXT = 40_000
_SCENE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

Handler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class ToolError(Exception):
    pass


def _tool(fn: Handler) -> Handler:
    """Answer the store's refusals as `{error}` the model can act on, not a crash."""

    @functools.wraps(fn)
    async def run(args: dict[str, Any]) -> dict[str, Any]:
        try:
            return await fn(args or {})
        except (
            ToolError,
            store.StoreError,
            sections.EditError,
            templates.TemplateError,
            outlines.OutlineError,
            outbox.OutboxError,
            clips.ClipError,
            FileExistsError,
        ) as exc:
            return {"error": str(exc)}
        except FileNotFoundError as exc:
            return {"error": f"not found: {exc}"}

    return run


def _site(args: dict[str, Any]) -> str:
    site = str(args.get("site") or "").strip()
    if site:
        store.site_dir(site)
        return site
    sites = store.list_sites()
    if len(sites) == 1:
        return sites[0].id
    names = ", ".join(s.id for s in sites) or "none yet"
    raise ToolError(f"which site? pass `site` (sites: {names})")


def _path(args: dict[str, Any]) -> str:
    path = str(args.get("path") or "").strip().lstrip("/")
    if not path:
        raise ToolError("pass the page's `path` (from scrive.listPages)")
    return path


def _pending(text: str) -> list[dict[str, str]]:
    return [
        {"heading": p.heading or "(lead)", "intent": p.intent}
        for p in sections.pending_blocks(text)
    ]


def _wrote(site: str, page: Any) -> None:
    store.note_agent_write(site, page.meta.path, page.meta.revision)


# --- reading -------------------------------------------------------------------------


@_tool
async def list_pages(args: dict[str, Any]) -> dict[str, Any]:
    if not str(args.get("site") or "").strip() and len(store.list_sites()) != 1:
        sites = store.list_sites()
        return {
            "sites": [{"id": s.id, "title": s.title, "pages": s.pages} for s in sites],
            "note": "pass `site` to list one site's pages"
            if sites
            else "no Scrive sites yet — the person creates one in the Scrive pane",
        }
    site = _site(args)
    base = store.site_dir(site)
    pages = store.list_pages(site)
    theme = themes.site_theme(site)
    scenes = (
        sorted(p.relative_to(base).as_posix() for p in (base / "scenes").rglob("*.tsx"))
        if (base / "scenes").is_dir()
        else []
    )
    return {
        "site": site,
        "pages": [
            {
                "path": m.path,
                "kind": m.kind,
                "title": m.title,
                **({"status": m.status} if m.status else {}),
                **({"date": m.date} if m.date else {}),
                **({"tags": m.tags} if m.tags else {}),
            }
            for m in pages[:150]
        ],
        "scenes": scenes,
        "templates": [
            {
                "id": t.id,
                "name": t.name,
                "description": t.description,
                "inputs": {
                    k: v.description + (" (required)" if v.required else "")
                    for k, v in t.inputs.items()
                },
            }
            for t in templates.list_templates(site)
        ],
        "outlines_awaiting_review": [
            {"id": o.id, "title": o.title}
            for o in outlines.list_outlines(site, "proposed")
        ],
        # The site theme's brand and voice guide (THEME.md): how to write for it.
        "theme": {"id": theme.id, "name": theme.name, "guide": theme.guide},
    }


@_tool
async def read_page(args: dict[str, Any]) -> dict[str, Any]:
    site = _site(args)
    if args.get("template"):
        info, text, skill = templates.get_template(site, str(args["template"]))
        return {"template": info.model_dump(), "text": text, "skill": skill}
    path = _path(args)
    if path.endswith(".tsx"):
        source = store.resolve_asset(site, path).read_text("utf-8", "replace")
        return {"site": site, "path": path, "text": source}
    page = store.read_page(site, path)
    text = page.content
    result: dict[str, Any] = {
        "site": site,
        "path": path,
        "revision": page.meta.revision,
        "title": page.meta.title,
        "kind": page.meta.kind,
        "frontmatter": sections.frontmatter_of(text),
        "outline": [
            {"level": h.level, "heading": h.text} for h in sections.headings(text)
        ],
        "pending": _pending(text),
    }
    if args.get("section"):
        result["text"] = sections.read_section(text, str(args["section"]))
    elif len(text) > _MAX_TEXT:
        result["text"] = text[:_MAX_TEXT]
        result["truncated"] = (
            "the page is long; read it by `section` (headings are in `outline`) "
            "before editing anything past this point"
        )
    else:
        result["text"] = text
    return result


@_tool
async def search_site(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        raise ToolError("pass a `query`")
    limit = max(1, min(int(args.get("limit") or 8), 20))
    return await search.search_site(_site(args), query, limit)


@_tool
async def critique_page(args: dict[str, Any]) -> dict[str, Any]:
    site = _site(args)
    path = _path(args)
    page = store.read_page(site, path)
    findings = critique.critique(page.content, store.site_dir(site), path)
    counts: dict[str, int] = {}
    for f in findings:
        counts[f.severity] = counts.get(f.severity, 0) + 1
    return {
        "path": path,
        "revision": page.meta.revision,
        "counts": counts,
        "findings": [f.model_dump() for f in findings[:40]],
    }


# --- writing -------------------------------------------------------------------------


@_tool
async def create_page(args: dict[str, Any]) -> dict[str, Any]:
    site = _site(args)
    title = str(args.get("title") or "").strip()
    if not title:
        raise ToolError("pass a `title`")
    kind = "page" if args.get("kind") == "page" else "post"
    template = str(args.get("template") or "").strip()
    content = args.get("content")
    if template and content:
        raise ToolError("pass `template` or `content`, not both")
    skill = ""
    body: str | None = None
    if template:
        info, body = templates.instantiate(
            site, template, dict(args.get("inputs") or {})
        )
        kind = info.kind if not args.get("kind") else kind
        skill = templates.get_template(site, template)[2]
    elif content:
        body = str(content)
    page = store.create_page(site, kind, title, body=body)
    _wrote(site, page)
    result: dict[str, Any] = {
        "path": page.meta.path,
        "revision": page.meta.revision,
        "pending": _pending(page.content),
    }
    if skill:
        result["skill"] = skill
    if result["pending"]:
        result["next"] = "fill each placeholder with scrive.fillSection, in order"
    return result


@_tool
async def edit_page(args: dict[str, Any]) -> dict[str, Any]:
    site = _site(args)
    path = _path(args)
    base = str(args.get("base_revision") or "")
    ops = args.get("ops")
    if not isinstance(ops, list) or not ops:
        raise ToolError("pass `ops`: a list of edit operations")
    page = store.read_page(site, path)
    if not base:
        raise ToolError(
            f"pass `base_revision` — the revision you read (currently {page.meta.revision})"
        )
    if base != page.meta.revision:
        raise ToolError(
            f"the page changed since you read it (now {page.meta.revision}); read it "
            "again and redo the edit on what is there — the person may have written it"
        )
    text, summary = sections.apply_ops(page.content, ops)
    if text == page.content:
        return {"revision": page.meta.revision, "summary": summary, "note": "no change"}
    # `status: published` is what lets a page out on the person's next publish, so
    # it is theirs to set, by any route (setFrontmatter, replaceText…).
    before = sections.frontmatter_of(page.content).get("status")
    if sections.frontmatter_of(text).get("status") != before:
        raise ToolError(
            "the page's `status` is the person's to change (it decides what gets "
            "published); leave it as it is and redo the edit"
        )
    try:
        saved = store.save_page(site, path, text, base)
    except store.Conflict as conflict:
        raise ToolError(
            f"the page changed while editing (now {conflict.current.meta.revision}); read it again"
        ) from None
    _wrote(site, saved)
    return {
        "revision": saved.meta.revision,
        "summary": summary,
        "outline": [h.text for h in sections.headings(text)],
        "pending": _pending(text),
    }


@_tool
async def fill_section(args: dict[str, Any]) -> dict[str, Any]:
    site = _site(args)
    path = _path(args)
    heading = str(args.get("heading") or "")
    content = str(args.get("content") or "")
    for _attempt in range(2):
        page = store.read_page(site, path)
        text = sections.fill_pending(page.content, heading, content)
        try:
            saved = store.save_page(site, path, text, page.meta.revision)
        except store.Conflict:
            continue  # written between the read and the save: fill the fresh copy
        _wrote(site, saved)
        remaining = _pending(text)
        return {
            "revision": saved.meta.revision,
            "remaining": remaining,
            "next": (
                f"fill {remaining[0]['heading']!r} next"
                if remaining
                else "every section is written — run scrive.critiquePage and fix what it finds"
            ),
        }
    raise ToolError("the page keeps changing under the edit; read it again")


@_tool
async def propose_outline(args: dict[str, Any]) -> dict[str, Any]:
    site = _site(args)
    raw = args.get("sections")
    if not isinstance(raw, list) or not raw:
        raise ToolError("pass `sections`: [{heading, intent, figure?, level?}]")
    items = []
    for entry in raw:
        if isinstance(entry, str):
            entry = {"heading": entry}
        if not isinstance(entry, dict):
            raise ToolError("each section is {heading, intent, figure?, level?}")
        try:
            level = int(entry.get("level") or 2)
        except (TypeError, ValueError):
            level = 2
        items.append(
            outlines.OutlineSection(
                heading=str(entry.get("heading") or ""),
                intent=str(entry.get("intent") or ""),
                figure=str(entry.get("figure") or ""),
                level=min(4, max(2, level)),
            )
        )
    tags = args.get("tags")
    outline = outlines.propose(
        site,
        title=str(args.get("title") or ""),
        sections_=items,
        kind="page" if args.get("kind") == "page" else "post",
        template=str(args.get("template") or ""),
        inputs={str(k): str(v) for k, v in dict(args.get("inputs") or {}).items()},
        prompt=str(args.get("prompt") or ""),
        lead=str(args.get("lead") or ""),
        description=str(args.get("description") or ""),
        tags=[str(t) for t in tags] if isinstance(tags, list) else [],
    )
    await broadcast_event(
        "scrive",
        "outline.changed",
        {"site": site, "id": outline.id, "status": outline.status},
    )
    return {
        "outline_id": outline.id,
        "status": "proposed",
        "next": (
            "The outline is open in Scrive for the person to review and approve. "
            "Stop here and tell them it is ready. Do not create or write the page: "
            "approving it creates the page, and you will then be asked to fill it."
        ),
    }


@_tool
async def write_scene(args: dict[str, Any]) -> dict[str, Any]:
    site = _site(args)
    name = str(args.get("name") or "").strip().removesuffix(".tsx")
    name = name.removeprefix("scenes/")
    if not _SCENE_NAME.match(name):
        raise ToolError(
            "`name` is lowercase letters, digits and dashes, e.g. 'orbit-demo'"
        )
    source = str(args.get("source") or "")
    if "export default" not in source:
        raise ToolError("a scene module must `export default` its component")
    rel = f"scenes/{name}.tsx"
    target = store.site_dir(site) / rel
    if target.exists() and not args.get("overwrite"):
        raise ToolError(
            f"{rel} already exists — read it with scrive.readPage, then pass overwrite: true to replace it"
        )
    store.write_bytes_atomic(target, source.encode("utf-8"))
    return {
        "path": rel,
        "embed_from_post": f"```{{r3f}} ../{rel}\n:height: 360\n```",
        "note": "the person sees it render in the page's Preview; a scene error shows there",
    }


# --- registration --------------------------------------------------------------------


@_tool
async def draft_social(args: dict[str, Any]) -> dict[str, Any]:
    """An outbox **draft** for X, LinkedIn or YouTube. Never approved or sent here."""
    from backend.modules.scrive.runner import announce

    site = _site(args)
    page = str(args.get("page") or args.get("path") or "").strip()
    target = str(args.get("target") or "").strip().lower()
    payload = args.get("payload")
    item = outbox.create(
        site,
        page,
        target,
        payload if isinstance(payload, dict) else None,
        created_by="agent",
        note=str(args.get("note") or ""),
    )
    await announce(item)
    findings = outbox.check(item.id)
    return {
        "id": item.id,
        "status": item.status,
        "target": item.target,
        "payload": item.payload,
        "findings": [
            {"rule": f.rule, "message": f.message, "blocking": f.blocking}
            for f in findings
        ],
        "note": "Drafted. The person reviews it in Scrive's Share pane and decides "
        "whether to approve and send it; you cannot, and must not say it was posted.",
    }


@_tool
async def make_clip(args: dict[str, Any]) -> dict[str, Any]:
    """Write an edit list for a site video and render it — local work on the site's
    own files. Sharing the result is a separate draft (scrive.draftSocial)."""
    import asyncio

    site = _site(args)
    source = str(args.get("source") or "").strip()
    if not source:
        raise ToolError("`source` is the site path of a video, e.g. 'media/demo.mp4'")
    edit: dict[str, Any] = {}
    segments = args.get("segments")
    if isinstance(segments, list) and segments:
        edit["segments"] = [
            {"in": s[0], "out": s[1]} if isinstance(s, (list, tuple)) else s
            for s in segments
        ]
    for key in ("crop", "speed", "captions", "overlays", "audio"):
        if args.get(key) is not None:
            edit[key] = args[key]
    preset = str(args.get("preset") or "x")
    edit["output"] = {"preset": preset, **(args.get("output") or {})}
    doc = await asyncio.to_thread(
        clips.create_clip,
        site,
        source,
        name=str(args.get("name") or ""),
        page=str(args.get("page") or ""),
        edit=edit,
    )
    errors = [f.message for f in doc.findings if f.severity == "error"]
    result: dict[str, Any] = {
        "clip": doc.path,
        "output": doc.output,
        "findings": [f.message for f in doc.findings],
    }
    if errors or args.get("render") is False:
        result["note"] = (
            "Not rendered: " + "; ".join(errors) if errors else "Written, not rendered."
        )
        return result
    loop = asyncio.get_running_loop()
    job = await asyncio.to_thread(media.jobs.start_render, site, doc.path, loop)
    job = await media.jobs.wait(job.id, float(args.get("wait") or 90))
    result.update(
        job=job.id,
        status=job.status,
        error=job.error,
        findings=result["findings"] + [f.message for f in job.findings],
    )
    if job.status == "running":
        result["note"] = "Still rendering; the person sees it finish in the clip pane."
    elif job.status == "done":
        directive = "figure" if doc.output.endswith(".gif") else "video"
        result["embed_from_post"] = f"```{{{directive}}} ../{doc.output}\n```"
    return result


_SITE = {
    "type": "string",
    "description": "Site id (from scrive.listPages). May be omitted when there is only one site.",
}
_PATH = {
    "type": "string",
    "description": "Page path relative to the site, e.g. 'posts/2026-10-02-priors.md'.",
}


def register_agent_tools() -> None:
    tools = [
        AgentTool(
            name="scrive.listPages",
            description=(
                "List Scrive sites, or one site's pages (path, title, status, date, tags), "
                "its 3D scenes, its post templates with their inputs, outlines "
                "awaiting review, and its theme's writing guide. Start here."
            ),
            handler=list_pages,
            group="scrive",
            parameters={"site": _SITE},
        ),
        AgentTool(
            name="scrive.readPage",
            description=(
                "Read a page as MyST text with its revision (needed to edit), outline "
                "and the {pending} placeholders still to write. Pass `section` for one "
                "heading's section, a scenes/*.tsx path for a scene's source, or "
                "`template` (an id) for a template and its writing guidance."
            ),
            handler=read_page,
            group="scrive",
            parameters={
                "site": _SITE,
                "path": _PATH,
                "section": {
                    "type": "string",
                    "description": "A heading: read only its section.",
                },
                "template": {
                    "type": "string",
                    "description": "A template id to read instead of a page.",
                },
            },
        ),
        AgentTool(
            name="scrive.searchSite",
            description=(
                "Search a site's posts, pages, scenes and templates by keywords and "
                "meaning. Use before writing, to link to and reuse what exists."
            ),
            handler=search_site,
            group="scrive",
            parameters={
                "site": _SITE,
                "query": {"type": "string", "description": "What to look for."},
                "limit": {"type": "integer", "description": "Max hits (default 8)."},
            },
            required=["query"],
        ),
        AgentTool(
            name="scrive.proposeOutline",
            description=(
                "Propose the outline of a NEW page for the person to review: title, "
                "the lead's intent, and sections (heading, one-line intent, planned "
                "figure). Writes nothing to the site. After calling it, stop — the "
                "person approves it in Scrive, which creates the page, and then you "
                "fill it with scrive.fillSection."
            ),
            handler=propose_outline,
            group="scrive",
            parameters={
                "site": _SITE,
                "title": {"type": "string"},
                "kind": {"type": "string", "enum": ["post", "page"]},
                "template": {
                    "type": "string",
                    "description": "Template id to base it on (from scrive.listPages).",
                },
                "inputs": {"type": "object", "description": "The template's inputs."},
                "lead": {
                    "type": "string",
                    "description": "What the opening paragraph says.",
                },
                "description": {
                    "type": "string",
                    "description": "One sentence for link cards.",
                },
                "tags": {"type": "array", "items": {"type": "string"}},
                "sections": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "heading": {"type": "string"},
                            "intent": {"type": "string"},
                            "figure": {"type": "string"},
                            "level": {
                                "type": "integer",
                                "description": "2 (default), 3 or 4",
                            },
                        },
                        "required": ["heading", "intent"],
                    },
                },
                "prompt": {
                    "type": "string",
                    "description": "The person's request, verbatim.",
                },
            },
            required=["title", "sections"],
        ),
        AgentTool(
            name="scrive.fillSection",
            description=(
                "Write one section of a page: replaces the {pending} placeholder under "
                "`heading` with `content` (MyST). Use heading '' for the lead before the "
                "first heading. Refuses a section that is already written. Answers what "
                "is left to fill."
            ),
            handler=fill_section,
            group="scrive",
            parameters={
                "site": _SITE,
                "path": _PATH,
                "heading": {
                    "type": "string",
                    "description": "The section's heading, or '' for the lead.",
                },
                "content": {
                    "type": "string",
                    "description": "The section body as MyST, without its heading.",
                },
            },
            required=["path", "heading", "content"],
            side_effect=True,
            specifier_template="{site}/{path}",
        ),
        AgentTool(
            name="scrive.editPage",
            description=(
                "Edit an existing page by operations, all or nothing. ops: "
                "{op:'replaceSection', heading, text} | {op:'deleteSection', heading} | "
                "{op:'insert', text, after_heading|before_heading|after_text|at:'start'|'end'} | "
                "{op:'replaceText', find, text, all?} | {op:'setFrontmatter', key, value}. "
                "Needs the `base_revision` from scrive.readPage. Everything not named "
                "in an op is left byte-for-byte."
            ),
            handler=edit_page,
            group="scrive",
            parameters={
                "site": _SITE,
                "path": _PATH,
                "base_revision": {"type": "string"},
                "ops": {"type": "array", "items": {"type": "object"}},
            },
            required=["path", "base_revision", "ops"],
            side_effect=True,
            specifier_template="{site}/{path}",
        ),
        AgentTool(
            name="scrive.createPage",
            description=(
                "Create a page directly — from a `template` (with its `inputs`) or "
                "from full MyST `content`. For a substantial new page, prefer "
                "scrive.proposeOutline so the person approves the plan first."
            ),
            handler=create_page,
            group="scrive",
            parameters={
                "site": _SITE,
                "title": {"type": "string"},
                "kind": {"type": "string", "enum": ["post", "page"]},
                "template": {"type": "string"},
                "inputs": {"type": "object"},
                "content": {"type": "string", "description": "The page body as MyST."},
            },
            required=["title"],
            side_effect=True,
            specifier_template="{site}: {title}",
        ),
        AgentTool(
            name="scrive.draftSocial",
            description=(
                "Draft a post about a page for X (a thread), LinkedIn (a link card) or "
                "YouTube (a video from the site) into Scrive's outbox. Drafts only: a "
                "person approves and sends. Omit `payload` for a first draft from the "
                "page's frontmatter. Payloads — x: {posts: [{text, media: [site paths]}], "
                "link}; linkedin: {text, link, title, description, thumbnail}; youtube: "
                "{video, title, description, tags, privacy}. `{{post.url}}` stands for "
                "the page's published URL."
            ),
            handler=draft_social,
            group="scrive",
            parameters={
                "site": _SITE,
                "page": _PATH,
                "target": {"type": "string", "enum": ["x", "linkedin", "youtube"]},
                "payload": {"type": "object"},
                "note": {
                    "type": "string",
                    "description": "One line for the person reviewing the draft.",
                },
            },
            required=["page", "target"],
            side_effect=True,
            specifier_template="{target}: {page}",
        ),
        AgentTool(
            name="scrive.writeScene",
            description=(
                "Write a React Three Fiber scene to scenes/<name>.tsx for an {r3f} "
                "block. A TSX module whose default export is a component receiving "
                "{ params }; it may import only react, three, @react-three/fiber and "
                "@react-three/drei, and cannot load files by URL."
            ),
            handler=write_scene,
            group="scrive",
            parameters={
                "site": _SITE,
                "name": {
                    "type": "string",
                    "description": "File name without .tsx, e.g. 'orbit-demo'.",
                },
                "source": {"type": "string"},
                "overwrite": {"type": "boolean"},
            },
            required=["name", "source"],
            side_effect=True,
            specifier_template="{site}/scenes/{name}",
        ),
        AgentTool(
            name="scrive.makeClip",
            description=(
                "Cut a site video into a clip and render it (local work; nothing is "
                "posted). Writes an edit list beside the source and renders "
                "media/<name>.<preset>.mp4 (or .gif). segments: [[in, out], ...] in "
                "source seconds, in order; crop: {aspect: source|16:9|9:16|1:1|4:5, x, "
                "y} with x, y the window's centre (0..1); speed 0.25-4; captions and "
                "overlays: [{t0, t1, text}] in output seconds (overlays take pos: "
                "top|center|bottom); preset: x (<=140 s), youtube, web or gif. Waits "
                "for the render up to `wait` seconds. The person can refine it in the "
                "clip pane; to share it, draft with scrive.draftSocial."
            ),
            handler=make_clip,
            group="scrive",
            parameters={
                "site": _SITE,
                "source": {"type": "string", "description": "Site path of the video."},
                "name": {
                    "type": "string",
                    "description": "Clip name; default the source's.",
                },
                "page": {
                    "type": "string",
                    "description": "The page the clip belongs to.",
                },
                "segments": {"type": "array", "items": {}},
                "crop": {"type": "object"},
                "speed": {"type": "number"},
                "captions": {"type": "array", "items": {"type": "object"}},
                "overlays": {"type": "array", "items": {"type": "object"}},
                "preset": {"type": "string", "enum": ["x", "youtube", "web", "gif"]},
                "render": {"type": "boolean"},
                "wait": {"type": "number"},
            },
            required=["source"],
            side_effect=True,
            specifier_template="{site}/{source}",
        ),
        AgentTool(
            name="scrive.critiquePage",
            description=(
                "Review a page before it is shared: placeholders left, dead links, "
                "images without alt text, skipped heading levels, unexplained "
                "equations, claims without a source. Run it after filling a page."
            ),
            handler=critique_page,
            group="scrive",
            parameters={"site": _SITE, "path": _PATH},
            required=["path"],
        ),
    ]
    for tool in tools:
        registry.agent_tools[tool.name] = tool

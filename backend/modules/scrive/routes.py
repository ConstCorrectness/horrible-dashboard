"""Scrive HTTP surface: sites and pages. See docs/modules/scrive.mdx.

Mounted at `/api/scrive`. Errors from the store map onto status codes at this
boundary: an unknown site or page is 404, a refused path or id is 400, an existing
site is 409, and a stale save is 409 **carrying the current page** so the editor can
reconcile without a second round trip (the notes contract).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from fastapi import APIRouter, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response

from backend import extras
from backend.modules.scrive import (
    clips,
    critique,
    exports,
    kernel,
    media,
    outbox,
    outlines,
    publish,
    search,
    social,
    store,
    templates,
    themes,
)
from backend.modules.scrive.runner import announce
from backend.modules.scrive.runner import runner as outbox_runner
from backend.publishing import github_pages
from backend.publishing.errors import PublishError
from backend.modules.ws import broadcast_event
from backend.modules.scrive.models import (
    CreatePage,
    CreateSite,
    Page,
    PageMeta,
    SavePage,
    SiteConfig,
    SiteMeta,
)
from pydantic import BaseModel

router = APIRouter(prefix="/scrive", tags=["scrive"])

T = TypeVar("T")


class SiteDetail(BaseModel):
    site: SiteMeta
    config: SiteConfig


def _call(fn: Callable[[], T]) -> T:
    try:
        return fn()
    except (
        store.StoreError,
        templates.TemplateError,
        outlines.OutlineError,
        themes.ThemeError,
        PublishError,
        clips.ClipError,
    ) as exc:
        raise HTTPException(400, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, f"not found: {exc}") from exc
    except FileExistsError as exc:
        raise HTTPException(409, f"already exists: {exc}") from exc


@router.get("/sites", response_model=list[SiteMeta])
def list_sites() -> list[SiteMeta]:
    return store.list_sites()


@router.post("/sites", response_model=SiteMeta)
def create_site(body: CreateSite) -> SiteMeta:
    return _call(lambda: store.create_site(body.id, body.title))


@router.get("/sites/{site}", response_model=SiteDetail)
def get_site(site: str) -> SiteDetail:
    meta, config = _call(lambda: store.get_site(site))
    return SiteDetail(site=meta, config=config)


@router.get("/sites/{site}/pages", response_model=list[PageMeta])
def list_pages(site: str) -> list[PageMeta]:
    return _call(lambda: store.list_pages(site))


@router.post("/sites/{site}/pages", response_model=Page)
def create_page(site: str, body: CreatePage) -> Page:
    def make() -> Page:
        if not body.template:
            return store.create_page(site, body.kind, body.title, body.slug)
        _info, text = templates.instantiate(site, body.template, body.inputs)
        return store.create_page(site, body.kind, body.title, body.slug, body=text)

    return _call(make)


@router.get("/sites/{site}/page", response_model=Page)
def read_page(site: str, path: str = Query(...)) -> Page:
    return _call(lambda: store.read_page(site, path))


@router.put("/sites/{site}/page", response_model=Page)
def save_page(site: str, body: SavePage, path: str = Query(...)):
    try:
        return _call(
            lambda: store.save_page(site, path, body.content, body.base_revision)
        )
    except store.Conflict as conflict:
        return JSONResponse(
            status_code=409,
            content={
                "detail": "stale revision",
                "current": conflict.current.model_dump(),
            },
        )


@router.delete("/sites/{site}/page")
def delete_page(site: str, path: str = Query(...)) -> dict[str, bool]:
    _call(lambda: store.delete_page(site, path))
    return {"ok": True}


@router.get("/sites/{site}/asset")
def read_asset(site: str, path: str = Query(...)) -> FileResponse:
    """An embedded file, by its path relative to the site root (the page renderer
    resolves a page-relative path before asking). `no-cache` so a re-rendered figure
    shows up without a hard reload; the browser still revalidates cheaply via ETag."""
    resolved = _call(lambda: store.resolve_asset(site, path))
    # Served from the app's own origin, so a file opened directly (an SVG, an HTML
    # page someone dropped in `media/`) must not run script there: `sandbox` gives it
    # an opaque origin with scripts off. Embedding as <img>/<video> is unaffected.
    return FileResponse(
        resolved,
        headers={
            "Cache-Control": "no-cache",
            "Content-Security-Policy": "sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/sites/{site}/cells")
def cached_cells(site: str, path: str = Query(...)) -> dict[str, Any]:
    """A page's cached code-cell outputs (its shadow notebook), read without
    starting a kernel — what Preview and Write show before anything runs."""
    return {"cells": _call(lambda: kernel.cached_cells(site, path))}


RUNTIME = Path(__file__).parent / "static" / "scrive-runtime.js"

#: Served when the runtime has not been built: a scene frame that reports why.
_RUNTIME_MISSING = (
    "window.parent.postMessage({scrive: 1, type: 'error', message: "
    "'The scene runtime is not built. Run: pnpm --filter @horrible/scrive-runtime build'}, '*');"
)


@router.get("/runtime.js")
def scene_runtime() -> Response:
    """The `{r3f}` scene runtime (packages/scrive-runtime), loaded by a sandboxed
    frame as a classic script. Revalidated on every load so a rebuild shows up."""
    if not RUNTIME.is_file():
        return Response(_RUNTIME_MISSING, media_type="text/javascript")
    return FileResponse(
        RUNTIME,
        media_type="text/javascript",
        headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"},
    )


class AssetSaved(BaseModel):
    path: str


@router.post("/sites/{site}/assets", response_model=AssetSaved)
def upload_asset(
    file: UploadFile, site: str, folder: str = Form("media")
) -> AssetSaved:
    """A file pasted or dropped into the editor. Stored under `folder` (default
    `media/`) with a fresh name if taken; answers the site-relative path."""

    def chunks():
        while chunk := file.file.read(1024 * 1024):
            yield chunk

    path = _call(
        lambda: store.save_asset(site, file.filename or "asset", chunks(), folder)
    )
    return AssetSaved(path=path)


# --- templates ------------------------------------------------------------------------


@router.get("/templates", response_model=list[templates.TemplateInfo])
def list_templates(site: str | None = None) -> list[templates.TemplateInfo]:
    """Built-in post templates, plus the site's own when `site` is given."""
    return _call(lambda: templates.list_templates(site))


class SaveTemplate(BaseModel):
    path: str
    id: str
    name: str = ""
    description: str = ""


@router.post("/sites/{site}/templates", response_model=templates.TemplateInfo)
def save_template(site: str, body: SaveTemplate) -> templates.TemplateInfo:
    """ "Save as template": the page's text becomes `templates/<id>.md`."""
    return _call(
        lambda: templates.save_as_template(
            site, body.path, body.id, body.name, body.description
        )
    )


# --- outlines -------------------------------------------------------------------------
# Approve and discard live here and nowhere else: the agent proposes an outline,
# only a person's click turns it into a page.


async def _outline_event(outline: outlines.Outline) -> None:
    await broadcast_event(
        "scrive",
        "outline.changed",
        {"site": outline.site, "id": outline.id, "status": outline.status},
    )


@router.get("/outlines", response_model=list[outlines.Outline])
def list_outlines(
    site: str | None = None, status: outlines.OutlineStatus | None = None
) -> list[outlines.Outline]:
    return _call(lambda: outlines.list_outlines(site, status))


@router.get("/sites/{site}/outlines/{outline_id}", response_model=outlines.Outline)
def get_outline(site: str, outline_id: str) -> outlines.Outline:
    return _call(lambda: outlines.get(site, outline_id))


@router.put("/sites/{site}/outlines/{outline_id}", response_model=outlines.Outline)
async def update_outline(
    site: str, outline_id: str, body: outlines.OutlineEdit
) -> outlines.Outline:
    outline = _call(lambda: outlines.update(site, outline_id, body))
    await _outline_event(outline)
    return outline


class Approved(BaseModel):
    outline: outlines.Outline
    page: Page


@router.post("/sites/{site}/outlines/{outline_id}/approve", response_model=Approved)
async def approve_outline(
    site: str, outline_id: str, body: outlines.OutlineEdit | None = None
) -> Approved:
    """Write the page the outline describes (headings over `{pending}`
    placeholders). The client then asks the agent to fill it."""
    outline, page = _call(lambda: outlines.approve(site, outline_id, body))
    await _outline_event(outline)
    return Approved(outline=outline, page=page)


@router.post(
    "/sites/{site}/outlines/{outline_id}/discard", response_model=outlines.Outline
)
async def discard_outline(site: str, outline_id: str) -> outlines.Outline:
    outline = _call(lambda: outlines.discard(site, outline_id))
    await _outline_event(outline)
    return outline


# --- review and search ---------------------------------------------------------------


@router.get("/sites/{site}/critique", response_model=list[critique.Finding])
def critique_page(site: str, path: str = Query(...)) -> list[critique.Finding]:
    """The page's review findings (see critique.py) — what the agent's
    `scrive.critiquePage` sees, for the page pane's Check button."""

    def run() -> list[critique.Finding]:
        page = store.read_page(site, path)
        return critique.critique(page.content, store.site_dir(site), path)

    return _call(run)


@router.get("/sites/{site}/search")
async def search_site(
    site: str, q: str = Query(..., min_length=1), limit: int = 8
) -> dict[str, Any]:
    _call(lambda: store.site_dir(site))
    return await search.search_site(site, q, max(1, min(limit, 20)))


# --- themes and publishing ------------------------------------------------------------
# Publishing is a person's click, always: no agent tool reaches these routes.


@router.get("/themes", response_model=list[themes.ThemeInfo])
def list_themes(site: str | None = None) -> list[themes.ThemeInfo]:
    """The four built-in site themes, plus the site's own when `site` is given."""
    return _call(lambda: themes.list_themes(site))


class ConfigEdit(BaseModel):
    title: str | None = None
    theme: str | None = None
    pages: publish.PagesConfig | None = None


@router.put("/sites/{site}/config", response_model=SiteConfig)
def update_config(site: str, body: ConfigEdit) -> SiteConfig:
    return _call(
        lambda: publish.update_config(
            site, title=body.title, theme=body.theme, pages=body.pages
        )
    )


class PublishState(BaseModel):
    config: publish.PagesConfig
    record: publish.PublishRecord | None = None
    #: Whether a publish can work right now (GitHub connected, with repo scope).
    available: bool
    reason: str = ""
    #: The local build's folder, when one has been made.
    built: str = ""


@router.get("/sites/{site}/publish", response_model=PublishState)
def publish_state(site: str) -> PublishState:
    def read() -> PublishState:
        available, reason = github_pages.availability()
        built = store.site_dir(site) / publish.BUILD_DIR
        return PublishState(
            config=publish.pages_config(site),
            record=publish.read_record(site),
            available=available,
            reason=reason,
            built=str(built) if (built / "index.html").is_file() else "",
        )

    return _call(read)


@router.post("/sites/{site}/build", response_model=publish.BuildOutcome)
async def build_site(site: str, body: publish.Bundle) -> publish.BuildOutcome:
    """Write the client-built static site to `<site>/_build/site/`, completed with
    media, scenes and share cards, and say what preflight found. Pushes nothing."""
    _call(lambda: store.site_dir(site))
    try:
        return await asyncio.to_thread(publish.build_local, site, body)
    except (store.StoreError, PublishError, themes.ThemeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/sites/{site}/built/{path:path}")
def built_file(site: str, path: str = "") -> FileResponse:
    """The local build, for a preview. Pages here are the site's own HTML (often
    agent-written) served from the app's origin, so `sandbox` gives each an opaque
    origin: its scripts (scene frames) run, but cannot reach the app."""
    resolved = _call(lambda: publish.built_file(site, path))
    return FileResponse(
        resolved,
        headers={
            "Cache-Control": "no-cache",
            "Content-Security-Policy": "sandbox allow-scripts allow-popups "
            "allow-popups-to-escape-sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/export/status", response_model=exports.ExportStatus)
def export_status() -> exports.ExportStatus:
    """Which PDF engines this machine has (typst needs mystmd and Typst on PATH)."""
    return exports.status()


@router.post("/sites/{site}/export", response_model=exports.ExportOutcome)
async def export_page(site: str, body: exports.ExportRequest) -> exports.ExportOutcome:
    """Write the page as a PDF to `<site>/_build/exports/`. Drafts export too: the
    file is for the person, not the published site."""
    _call(lambda: store.site_dir(site))
    try:
        return await asyncio.to_thread(exports.export, site, body)
    except exports.ExportError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (store.StoreError, PublishError, themes.ThemeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, f"not found: {exc}") from exc


@router.get("/sites/{site}/export-file")
def export_file(site: str, path: str = Query(...)) -> FileResponse:
    """An exported PDF, as a download."""
    resolved = _call(lambda: exports.export_file(site, path))
    return FileResponse(
        resolved,
        media_type="application/pdf",
        filename=resolved.name,
        headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"},
    )


@router.post("/sites/{site}/publish", response_model=publish.PublishOutcome)
async def publish_site(
    site: str, body: publish.PublishRequest
) -> publish.PublishOutcome:
    """Push the site to GitHub Pages — or, when preflight found something blocking
    that was not acknowledged, push nothing and answer the findings."""
    _call(lambda: store.site_dir(site))
    try:
        outcome = await publish.publish(site, body)
    except (store.StoreError, PublishError, themes.ThemeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    if outcome.published and outcome.record:
        await broadcast_event(
            "scrive",
            "site.published",
            {"site": site, "url": outcome.record.url, "commit": outcome.record.commit},
        )
    return outcome


# --- the outbox ------------------------------------------------------------------------
# Approve, send, schedule and retry are a person's clicks: these routes are the only
# way a row moves past `draft` (the agent's `scrive.draftSocial` only creates drafts).


def _outbox_call(fn: Callable[[], T]) -> T:
    try:
        return _call(fn)
    except outbox.OutboxError as exc:
        raise HTTPException(409, str(exc)) from exc


async def _changed(item: outbox.OutboxItem) -> outbox.OutboxItem:
    await announce(item)
    return item


@router.get("/outbox", response_model=list[outbox.OutboxItem])
def list_outbox(
    site: str | None = None, page: str | None = None, status: str | None = None
) -> list[outbox.OutboxItem]:
    return outbox.list_items(site, page, status)


@router.get("/outbox/{item_id}", response_model=outbox.OutboxItem)
def get_outbox(item_id: str) -> outbox.OutboxItem:
    return _outbox_call(lambda: outbox.get(item_id))


class NewDraft(BaseModel):
    site: str
    page: str = ""
    target: str
    #: Omitted: a first draft from the page's frontmatter (`social.suggest`).
    payload: dict[str, Any] | None = None


@router.post("/outbox", response_model=outbox.OutboxItem)
async def create_outbox(body: NewDraft) -> outbox.OutboxItem:
    return await _changed(
        _outbox_call(
            lambda: outbox.create(body.site, body.page, body.target, body.payload)
        )
    )


class EditDraft(BaseModel):
    payload: dict[str, Any]


@router.put("/outbox/{item_id}", response_model=outbox.OutboxItem)
async def edit_outbox(item_id: str, body: EditDraft) -> outbox.OutboxItem:
    return await _changed(_outbox_call(lambda: outbox.edit(item_id, body.payload)))


@router.delete("/outbox/{item_id}")
async def delete_outbox(item_id: str) -> dict[str, bool]:
    item = _outbox_call(lambda: outbox.get(item_id))
    _outbox_call(lambda: outbox.delete(item_id))
    await announce(item, deleted=True)
    return {"ok": True}


@router.post("/outbox/{item_id}/check", response_model=list[publish.Finding])
def check_outbox(item_id: str) -> list[publish.Finding]:
    """Preflight without approving: what the composer shows while editing."""
    return _outbox_call(lambda: outbox.check(item_id))


class Approve(BaseModel):
    acknowledged: bool = False


class OutboxApproved(BaseModel):
    item: outbox.OutboxItem
    #: False when preflight stopped it; `item.findings` says why.
    approved: bool


@router.post("/outbox/{item_id}/approve", response_model=OutboxApproved)
async def approve_outbox(item_id: str, body: Approve | None = None) -> OutboxApproved:
    item, approved = _outbox_call(
        lambda: outbox.approve(item_id, acknowledged=bool(body and body.acknowledged))
    )
    await announce(item)
    return OutboxApproved(item=item, approved=approved)


@router.post("/outbox/{item_id}/send", response_model=outbox.OutboxItem)
async def send_outbox(item_id: str) -> outbox.OutboxItem:
    item = _outbox_call(lambda: outbox.send(item_id))
    outbox_runner.enqueue(item_id)
    return await _changed(item)


class Schedule(BaseModel):
    #: Epoch seconds.
    run_at: float


@router.post("/outbox/{item_id}/schedule", response_model=outbox.OutboxItem)
async def schedule_outbox(item_id: str, body: Schedule) -> outbox.OutboxItem:
    return await _changed(_outbox_call(lambda: outbox.schedule(item_id, body.run_at)))


@router.post("/outbox/{item_id}/unschedule", response_model=outbox.OutboxItem)
async def unschedule_outbox(item_id: str) -> outbox.OutboxItem:
    return await _changed(_outbox_call(lambda: outbox.unschedule(item_id)))


@router.post("/outbox/{item_id}/retry", response_model=outbox.OutboxItem)
async def retry_outbox(item_id: str) -> outbox.OutboxItem:
    item = _outbox_call(lambda: outbox.retry(item_id))
    outbox_runner.enqueue(item_id)
    return await _changed(item)


@router.get("/social/suggest")
def suggest_payload(site: str, page: str, target: str) -> dict[str, Any]:
    """A first draft of a payload from the page's frontmatter."""
    if target not in social.TARGETS:
        raise HTTPException(400, f"not a target: {target}")
    return _call(lambda: social.suggest(site, page, target))


# --- clips ----------------------------------------------------------------------------
# Rendering and captioning are local work on the site's own files; nothing here
# publishes. A draft made from a render is an outbox draft like any other.


@router.get("/media/status")
def media_status() -> dict[str, Any]:
    """Whether ffmpeg (rendering) and local speech-to-text (auto-captions) are here."""
    return {
        "ffmpeg": extras.probe("ffmpeg").to_dict(),
        "voice": extras.probe("voice").to_dict(),
    }


@router.get("/sites/{site}/media", response_model=clips.MediaListing)
def list_media(site: str) -> clips.MediaListing:
    return _call(lambda: clips.list_media(site))


@router.get("/sites/{site}/media/probe", response_model=clips.Probe)
async def probe_media(site: str, path: str = Query(...)) -> clips.Probe:
    resolved = _call(lambda: store.resolve_asset(site, path))
    return await asyncio.to_thread(lambda: _call(lambda: clips.probe(resolved)))


@router.get("/sites/{site}/media/filmstrip")
async def media_filmstrip(
    site: str, path: str = Query(...), count: int = 16
) -> FileResponse:
    image = await asyncio.to_thread(
        lambda: _call(lambda: media.filmstrip(site, path, count))
    )
    return FileResponse(
        image, media_type="image/jpeg", headers={"Cache-Control": "no-cache"}
    )


class NewClip(BaseModel):
    source: str
    name: str = ""
    page: str = ""
    edit: dict[str, Any] | None = None


@router.post("/sites/{site}/clips", response_model=clips.ClipDoc)
async def create_clip(site: str, body: NewClip) -> clips.ClipDoc:
    """A new edit list beside `source` (a browser recording is remuxed first so it
    can be seeked)."""
    return await asyncio.to_thread(
        lambda: _call(
            lambda: clips.create_clip(
                site, body.source, name=body.name, page=body.page, edit=body.edit
            )
        )
    )


@router.get("/sites/{site}/clip", response_model=clips.ClipDoc)
async def read_clip(site: str, path: str = Query(...)) -> clips.ClipDoc:
    return await asyncio.to_thread(lambda: _call(lambda: clips.read_clip(site, path)))


class SaveClip(BaseModel):
    edit: dict[str, Any]
    base_revision: str = ""


@router.put("/sites/{site}/clip", response_model=clips.ClipDoc)
async def save_clip(site: str, body: SaveClip, path: str = Query(...)):
    def save():
        try:
            return _call(
                lambda: clips.save_clip(site, path, body.edit, body.base_revision)
            )
        except clips.ClipConflict as conflict:
            return JSONResponse(
                status_code=409,
                content={
                    "detail": "stale revision",
                    "current": conflict.current.model_dump(),
                },
            )

    return await asyncio.to_thread(save)


@router.post("/sites/{site}/clip/render", response_model=media.Job)
async def render_clip(site: str, path: str = Query(...)) -> media.Job:
    """Start rendering (or answer the render already running for this clip)."""
    loop = asyncio.get_running_loop()
    return await asyncio.to_thread(
        lambda: _call(lambda: media.jobs.start_render(site, path, loop))
    )


@router.post("/sites/{site}/clip/captions", response_model=media.Job)
async def caption_clip(site: str, path: str = Query(...)) -> media.Job:
    """Transcribe the clip's sound into caption cues (answered on the job; the pane
    merges them — the clip file is not written)."""
    loop = asyncio.get_running_loop()
    return await asyncio.to_thread(
        lambda: _call(lambda: media.jobs.start_captions(site, path, loop))
    )


@router.get("/clip-jobs", response_model=list[media.Job])
def list_clip_jobs(site: str | None = None, clip: str | None = None) -> list[media.Job]:
    return media.jobs.list(site, clip)


@router.post("/clip-jobs/{job_id}/cancel", response_model=media.Job)
def cancel_clip_job(job_id: str) -> media.Job:
    return _call(lambda: media.jobs.cancel(job_id))


class ClipDraft(BaseModel):
    path: str
    target: str


@router.post("/sites/{site}/clip/draft", response_model=outbox.OutboxItem)
async def draft_clip(site: str, body: ClipDraft) -> outbox.OutboxItem:
    """An X or YouTube **draft** carrying the clip's render. Approve and send stay
    in the Share pane."""
    item = await asyncio.to_thread(
        lambda: _outbox_call(
            lambda: media.draft_from_clip(site, body.path, body.target)
        )
    )
    return await _changed(item)

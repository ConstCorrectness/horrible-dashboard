"""`/api/notebook/publish/*`: preflight, publish, list, unpublish, and HTML export.

Mounted inside the notebook router (see `notebook/routes.py`), so it shares the
notebook root and its escape guard.

Every route reads the **live session's document** when one is open, and the file on
disk otherwise: a kernel session holds the authoritative in-memory copy, and publishing
the stale file behind an open notebook would publish something other than what the
person is looking at.
"""

from __future__ import annotations

import asyncio
import copy
import os
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Response

from backend.modules.notebook.manager import notebook_manager, resolve
from backend.modules.notebook.models import (
    PreflightIn,
    PreflightOut,
    PublicationModel,
    PublicationsOut,
    PublishIn,
    PublishOut,
    PublishTargetOut,
    SavedHtmlOut,
    UnpublishOut,
)
from backend.modules.notebook.publish import store
from backend.modules.notebook.publish.preflight import scan
from backend.modules.notebook.publish.prepare import prepare, title_of
from backend.modules.notebook.publish.render import RenderError, to_html
from backend.modules.notebook.publish.targets import all_targets, get_target
from backend.modules.notebook.publish.targets.base import PublishContext, PublishError
from backend.notebook_core import notebooks

router = APIRouter(prefix="/publish", tags=["notebook"])


def _locate(path: str) -> tuple[str, Path]:
    rel = path.replace("\\", "/").strip()
    try:
        abs_path = resolve(rel)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not abs_path.is_file():
        raise HTTPException(status_code=404, detail=f"notebook not found: {rel}")
    return rel, abs_path


def _load(rel: str, abs_path: Path) -> Any:
    session = notebook_manager.session_for(f"nb:{rel}")
    if session is not None:
        with session.doc_lock:
            return copy.deepcopy(session.doc)
    return notebooks.load(abs_path)


async def _prepared(path: str, strip_outputs: bool) -> tuple[str, Path, Any]:
    rel, abs_path = _locate(path)
    nb = await asyncio.to_thread(_load, rel, abs_path)
    return rel, abs_path, prepare(nb, strip_outputs=strip_outputs)


@router.get("", response_model=PublicationsOut)
async def publications(path: str = Query(...)) -> PublicationsOut:
    rel, _ = _locate(path)
    targets = []
    for target in all_targets():
        available, reason = target.availability()
        targets.append(
            PublishTargetOut(
                id=target.id,
                label=target.label,
                visibilities=list(target.visibilities),
                available=available,
                reason=reason,
                can_unpublish=target.can_unpublish,
            )
        )
    return PublicationsOut(publications=store.list_for(rel), targets=targets)


@router.post("/preflight", response_model=PreflightOut)
async def preflight(body: PreflightIn) -> PreflightOut:
    _, _, nb = await _prepared(body.path, body.strip_outputs)
    findings = scan(nb)
    return PreflightOut(findings=findings, blocking=sum(f.blocking for f in findings))


@router.post("", response_model=PublishOut)
async def publish(body: PublishIn) -> PublishOut:
    target = get_target(body.target)
    if target is None:
        raise HTTPException(
            status_code=404, detail=f"no such publish target: {body.target}"
        )
    if body.visibility not in target.visibilities:
        raise HTTPException(
            status_code=400,
            detail=f"{target.label} cannot publish as {body.visibility}",
        )

    rel, abs_path, nb = await _prepared(body.path, body.strip_outputs)
    findings = scan(nb)
    blocking = [f for f in findings if f.blocking]
    if blocking and not body.acknowledged:
        # Refused *before* any network call: the check is only worth anything if a
        # target never sees the content it flagged.
        count = len(blocking)
        return PublishOut(
            ok=False,
            error=(
                f"{count} thing{'s' if count != 1 else ''} in this notebook look like "
                "secrets. Remove them, or confirm you want to publish anyway."
            ),
            findings=findings,
        )

    stem = abs_path.stem
    title = title_of(nb, stem)
    ctx = PublishContext(
        rel_path=rel,
        stem=stem,
        title=title,
        notebook=nb,
        visibility=body.visibility,
        previous=store.get(rel, target.id),
        render_html=lambda: to_html(nb, title=title),
    )
    try:
        result = await target.publish(ctx)
    except (PublishError, RenderError) as exc:
        return PublishOut(ok=False, error=str(exc), findings=findings)

    record = store.upsert(
        PublicationModel(
            path=rel,
            target=target.id,
            remote_id=result.remote_id,
            url=result.url,
            extra_url=result.extra_url,
            visibility=body.visibility,
            detail=result.detail,
        )
    )
    return PublishOut(ok=True, publication=record, note=result.note, findings=findings)


@router.delete("", response_model=UnpublishOut)
async def unpublish(
    path: str = Query(...),
    target: str = Query(...),
    forget: bool = Query(False),
) -> UnpublishOut:
    """Delete the remote copy and the record — or, with `forget`, only the record.

    A failed delete keeps the record: dropping it would leave a live public copy that
    nothing on this machine remembers.
    """
    rel = path.replace("\\", "/").strip()
    record = store.get(rel, target)
    if record is None:
        return UnpublishOut(ok=True)
    if not forget:
        impl = get_target(target)
        if impl is None:
            raise HTTPException(
                status_code=404, detail=f"no such publish target: {target}"
            )
        try:
            await impl.unpublish(record)
        except PublishError as exc:
            return UnpublishOut(ok=False, error=str(exc))
    store.delete(rel, target)
    return UnpublishOut(ok=True)


@router.get("/html")
async def export_html(
    path: str = Query(...), strip_outputs: bool = Query(False)
) -> Response:
    """The notebook as a downloadable standalone HTML file."""
    _, abs_path, nb = await _prepared(path, strip_outputs)
    title = title_of(nb, abs_path.stem)
    try:
        html = await asyncio.to_thread(to_html, nb, title=title)
    except RenderError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    filename = f"{abs_path.stem}.html"
    return Response(
        content=html,
        media_type="text/html",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"
        },
    )


@router.post("/html", response_model=SavedHtmlOut)
async def save_html(body: PreflightIn) -> SavedHtmlOut:
    """Write `<name>.html` beside the notebook.

    The route the panel uses, because a blob download from a page is not reliably
    saved inside the desktop shell — a file on disk works in both layouts.
    """
    rel, abs_path, nb = await _prepared(body.path, body.strip_outputs)
    title = title_of(nb, abs_path.stem)
    try:
        html = await asyncio.to_thread(to_html, nb, title=title)
    except RenderError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    target = abs_path.with_suffix(".html")

    def write() -> None:
        # Atomic: a half-written HTML file that a browser then caches is worse than
        # the previous export.
        fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".html.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(html)
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    await asyncio.to_thread(write)
    return SavedHtmlOut(path=str(Path(rel).with_suffix(".html").as_posix()))

"""Outlines: the plan an agent proposes before it writes a page (Kombai's plan mode).

"Generate a page" is three steps with a person in the middle:

1. The agent calls `scrive.proposeOutline`: a title, the lead's intent, and the
   sections it means to write (heading, one-line intent, planned figure). Nothing is
   written to the site — the outline is a file in `.scrive/outlines/`.
2. The outline pane shows it. The person edits headings and intents, reorders,
   adds or drops sections, and **approves** (or discards).
3. Approval writes the page: the template's frontmatter if one was chosen, then each
   section as a heading over a `{pending}` placeholder holding its intent. The agent
   fills them one at a time with `scrive.fillSection`, so the page grows section by
   section in the open editor.

Approval is a person's click on a route — no agent tool approves an outline.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from backend.modules.scrive import sections, store, templates
from backend.modules.scrive.models import Page

OutlineStatus = Literal["proposed", "approved", "discarded"]
_ID = re.compile(r"^[a-f0-9]{12}$")


class OutlineSection(BaseModel):
    heading: str
    #: One line: what this section says or shows.
    intent: str = ""
    level: int = Field(2, ge=2, le=4)
    #: A planned figure, scene, table or code cell, if the section has one.
    figure: str = ""


class OutlineEdit(BaseModel):
    """What the person may change before approving."""

    title: str | None = None
    lead: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    sections: list[OutlineSection] | None = None


class Outline(BaseModel):
    id: str
    site: str
    title: str
    kind: Literal["post", "page"] = "post"
    template: str = ""
    inputs: dict[str, str] = Field(default_factory=dict)
    #: What the person asked for, kept so the fill turn can be told.
    prompt: str = ""
    #: The opening paragraph's intent (no heading).
    lead: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    sections: list[OutlineSection]
    status: OutlineStatus = "proposed"
    #: The page approval created, site-relative.
    page: str = ""
    created_at: float
    updated_at: float


class OutlineError(ValueError):
    pass


def _dir(site_id: str) -> Path:
    return store.site_dir(site_id) / ".scrive" / "outlines"


def _path(site_id: str, outline_id: str) -> Path:
    if not _ID.match(outline_id):
        raise store.StoreError(f"not an outline id: {outline_id!r}")
    return _dir(site_id) / f"{outline_id}.json"


def _save(outline: Outline) -> Outline:
    outline.updated_at = time.time()
    data = json.dumps(outline.model_dump(), indent=2).encode("utf-8")
    store.write_bytes_atomic(_path(outline.site, outline.id), data)
    return outline


def get(site_id: str, outline_id: str) -> Outline:
    path = _path(site_id, outline_id)
    if not path.is_file():
        raise FileNotFoundError(f"outline {outline_id}")
    return Outline.model_validate_json(path.read_bytes())


def list_outlines(
    site_id: str | None = None, status: OutlineStatus | None = None
) -> list[Outline]:
    sites = [site_id] if site_id else [s.id for s in store.list_sites()]
    out: list[Outline] = []
    for sid in sites:
        folder = _dir(sid)
        if not folder.is_dir():
            continue
        for path in folder.glob("*.json"):
            try:
                outline = Outline.model_validate_json(path.read_bytes())
            except ValueError:
                continue
            if status is None or outline.status == status:
                out.append(outline)
    return sorted(out, key=lambda o: o.created_at, reverse=True)


def _check_sections(items: list[OutlineSection]) -> list[OutlineSection]:
    cleaned = [
        s.model_copy(update={"heading": s.heading.strip().lstrip("#").strip()})
        for s in items
    ]
    cleaned = [s for s in cleaned if s.heading]
    if not cleaned:
        raise OutlineError("an outline needs at least one section with a heading")
    return cleaned


def propose(
    site_id: str,
    *,
    title: str,
    sections_: list[OutlineSection],
    kind: Literal["post", "page"] = "post",
    template: str = "",
    inputs: dict[str, str] | None = None,
    prompt: str = "",
    lead: str = "",
    description: str = "",
    tags: list[str] | None = None,
) -> Outline:
    store.site_dir(site_id)  # the site must exist
    if not title.strip():
        raise OutlineError("an outline needs a title")
    inputs = {str(k): str(v) for k, v in (inputs or {}).items()}
    if template:
        info, text, _skill = templates.get_template(site_id, template)
        templates.fill_inputs(
            text, info, inputs
        )  # a missing input fails now, not at approval
    now = time.time()
    outline = Outline(
        id=uuid.uuid4().hex[:12],
        site=site_id,
        title=title.strip(),
        kind=kind,
        template=template,
        inputs=inputs,
        prompt=prompt.strip(),
        lead=lead.strip(),
        description=description.strip(),
        tags=[t.strip() for t in tags or [] if t.strip()],
        sections=_check_sections(sections_),
        created_at=now,
        updated_at=now,
    )
    return _save(outline)


def _editable(site_id: str, outline_id: str) -> Outline:
    outline = get(site_id, outline_id)
    if outline.status != "proposed":
        raise OutlineError(f"outline {outline_id} is already {outline.status}")
    return outline


def update(site_id: str, outline_id: str, edit: OutlineEdit) -> Outline:
    outline = _editable(site_id, outline_id)
    patch = edit.model_dump(exclude_none=True)
    if "sections" in patch:
        patch["sections"] = _check_sections(edit.sections or [])
    if "title" in patch and not str(patch["title"]).strip():
        raise OutlineError("an outline needs a title")
    return _save(outline.model_copy(update=patch))


def page_text(outline: Outline) -> str:
    """The page an approved outline becomes: frontmatter (the template's, when there
    is one) and every section as a heading over a `{pending}` placeholder."""
    head = "---\n---\n"
    if outline.template:
        _info, text = templates.instantiate(
            outline.site, outline.template, outline.inputs
        )
        lines = text.split("\n")
        start = sections.body_start(lines)
        if start:
            head = "\n".join(lines[:start]) + "\n"
    if outline.description:
        head = sections.set_field(head, "description", outline.description)
    if outline.tags:
        existing = sections.frontmatter_of(head).get("tags")
        merged = [str(t) for t in existing] if isinstance(existing, list) else []
        merged += [t for t in outline.tags if t not in merged]
        head = sections.set_field(head, "tags", merged)
    blocks: list[str] = []
    if outline.lead:
        blocks.append(sections.pending_directive(outline.lead))
    for item in outline.sections:
        intent = item.intent.strip() or "Write this section."
        if item.figure.strip():
            intent += f"\n\nFigure: {item.figure.strip()}"
        blocks.append(f"{'#' * item.level} {item.heading}")
        blocks.append(sections.pending_directive(intent))
    return head.rstrip("\n") + "\n\n" + "\n\n".join(blocks) + "\n"


def approve(
    site_id: str, outline_id: str, edit: OutlineEdit | None = None
) -> tuple[Outline, Page]:
    if edit is not None:
        update(site_id, outline_id, edit)
    outline = _editable(site_id, outline_id)
    page = store.create_page(
        site_id, outline.kind, outline.title, body=page_text(outline)
    )
    outline.status = "approved"
    outline.page = page.meta.path
    return _save(outline), page


def discard(site_id: str, outline_id: str) -> Outline:
    outline = _editable(site_id, outline_id)
    outline.status = "discarded"
    return _save(outline)

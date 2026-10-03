from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

#: A site is a directory under the Scrive root; its id is the directory name.
SITE_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{0,63}$"

PageKind = Literal["post", "page", "notebook"]


class SiteConfig(BaseModel):
    """`scrive.yml` — Scrive's own settings for a site, beside MyST's `myst.yml`.

    A separate file rather than a key inside `myst.yml`, so a real `jupyter book
    build` never trips over configuration it does not know. `version` exists from day
    one (GitBook's lesson): the first migration is much cheaper with it than without.
    """

    version: int = 1
    title: str = ""
    theme: str = "minimal"
    #: Publish targets by id (`pages: {repo, mode}`), filled in by the publish phases.
    targets: dict[str, Any] = Field(default_factory=dict)
    defaults: dict[str, Any] = Field(default_factory=dict)


class SiteMeta(BaseModel):
    id: str
    title: str
    theme: str
    #: Absolute folder, for "reveal in explorer". The folder is the user's own data.
    root: str
    pages: int = 0


class CreateSite(BaseModel):
    id: str = Field(pattern=SITE_ID_PATTERN)
    title: str = ""


class PageMeta(BaseModel):
    """What the posts database view needs, read from frontmatter. Nothing here is
    stored anywhere but the file itself."""

    path: str  # relative to the site root, forward slashes
    kind: PageKind
    title: str
    status: str = ""
    date: str = ""
    tags: list[str] = Field(default_factory=list)
    description: str = ""
    updated_at: float
    revision: str


class Page(BaseModel):
    meta: PageMeta
    #: The file's text, byte-for-byte (line endings included). The editor's
    #: source-preserving writer depends on getting back exactly what is on disk.
    content: str


class CreatePage(BaseModel):
    kind: Literal["post", "page"] = "post"
    title: str = "Untitled"
    #: Defaults to a slug of the title. Posts get a `YYYY-MM-DD-` prefix.
    slug: str = ""
    #: A post template to start from (see `templates.py`), and its inputs.
    template: str = ""
    inputs: dict[str, str] = Field(default_factory=dict)


class SavePage(BaseModel):
    content: str
    #: The revision the editor loaded. A save against anything else is a 409 carrying
    #: the current page, the same contract as notes.
    base_revision: str


class PageChanged(BaseModel):
    """`scrive` channel, event `page.changed`. Sent for the editor's own saves too;
    a client compares `revision` with what it holds and ignores its own echo."""

    site: str
    path: str
    change: Literal["added", "modified", "deleted"]
    revision: str = ""
    #: `agent` when a Scrive agent tool wrote this revision: an open editor shows it
    #: as a reviewable agent edit (changed blocks marked, Keep / Undo).
    origin: Literal["", "agent"] = ""

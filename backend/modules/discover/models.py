"""The one shape every Discover source answers in.

Eleven catalogs (the Hub, arXiv, GitHub, Kaggle, the MCP registry, …) each describe a
"thing you might want" differently. The pane renders one list and one detail view, so
each adapter's job is to translate into this shape **without inventing anything**:

- A metric the upstream didn't return is `value=None` and renders as "—". It is never
  `0`, because "0 downloads" and "the API didn't say" are different facts, and the
  difference is exactly the kind of thing a browse surface must not blur.
- A metric's `label` states what the number *is* — `downloads · 30d`, not "downloads"
  — because the Hub's `downloads` field is a 30-day window and reading it as a total
  is wrong by orders of magnitude for an old model.
- `feed_label` is a sentence saying what an unsearched list is ("Most-starred ML repos
  pushed in the last 7 days"), so a default feed never pretends to be something it
  isn't — GitHub has no "trending" API, and the label says so by not claiming one.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Status = Literal["ok", "needs_connect", "rate_limited", "degraded", "error"]
Tone = Literal["ok", "warn", "fail", "info", "idle"]


class Metric(BaseModel):
    key: str
    label: str
    value: float | None = None
    #: How the frontend formats `value`: a count, bytes, a 0–1 fraction, or a
    #: unix-epoch date. One field rather than per-source formatting code.
    unit: Literal["count", "bytes", "percent", "score"] = "count"


class Badge(BaseModel):
    label: str
    tone: Tone = "idle"
    title: str | None = None


class Fact(BaseModel):
    label: str
    value: str


class Link(BaseModel):
    label: str
    url: str


class DiscoverItem(BaseModel):
    source: str
    kind: str
    id: str
    title: str
    subtitle: str = ""
    description: str = ""
    url: str | None = None
    author: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    thumbnail: str | None = None
    tags: list[str] = Field(default_factory=list)
    badges: list[Badge] = Field(default_factory=list)
    metrics: list[Metric] = Field(default_factory=list)
    #: Facts known at list time that the detail view shows (a Kaggle deadline, an
    #: arXiv category list). Carried here so a source without a cheap detail call
    #: can still render one from the row it already has.
    facts: list[Fact] = Field(default_factory=list)


class DiscoverPage(BaseModel):
    source: str
    kind: str
    items: list[DiscoverItem] = Field(default_factory=list)
    cursor_next: str | None = None
    #: Only when upstream reports one. Most don't, and a made-up total is worse
    #: than none.
    total: int | None = None
    feed_label: str = ""
    fetched_at: float = 0.0
    #: Served from the last good answer because upstream just failed.
    stale: bool = False
    status: Status = "ok"
    message: str | None = None
    #: Seconds until a rate limit resets, when upstream said.
    retry_after: float | None = None


class DiscoverDetail(BaseModel):
    item: DiscoverItem
    #: README / model card / abstract / SKILL.md. Markdown unless `body_format`
    #: says it's plain text (an arXiv abstract is not markdown and must not be
    #: rendered as if it were).
    body: str | None = None
    body_format: Literal["markdown", "text"] = "markdown"
    facts: list[Fact] = Field(default_factory=list)
    links: list[Link] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)
    status: Status = "ok"
    message: str | None = None


class Option(BaseModel):
    value: str
    label: str


class FilterSpec(BaseModel):
    id: str
    label: str
    options: list[Option]
    default: str = ""


class KindSpec(BaseModel):
    id: str
    label: str
    sorts: list[Option] = Field(default_factory=list)
    default_sort: str = ""
    filters: list[FilterSpec] = Field(default_factory=list)
    searchable: bool = True
    search_placeholder: str = "Search…"


class SourceSpec(BaseModel):
    id: str
    label: str
    kinds: list[KindSpec]
    requires_auth: bool = False
    #: Whether this node holds credentials for the source. For a source that
    #: works anonymously this only means "you'll also see private/gated things".
    connected: bool = False
    auth_hint: str | None = None


class SourcesResponse(BaseModel):
    sources: list[SourceSpec]

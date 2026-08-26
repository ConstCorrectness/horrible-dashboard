"""Pydantic models for the documentation viewer (`/api/docviewer/*`).

A **doc set** is one documentation site captured as a browsable unit: a seed URL, a
URL prefix that bounds it, and the pages the crawl found under that prefix. Each page
is a stored HTML archive (scripts intact — see `backend/modules/artifacts/store.py`
for how those are served) plus a `page` source in a library, which is what makes the
set semantically searchable.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SetStatus = Literal["queued", "crawling", "ready", "failed"]
PageStatus = Literal["pending", "captured", "failed"]

# Bounds on one crawl. `max_pages` is the knob users reach for; the byte cap is the
# one that stops a single set eating the disk, since a captured page inlines its own
# CSS, fonts and images.
DEFAULT_MAX_PAGES = 200
MAX_MAX_PAGES = 2000
DEFAULT_MAX_DEPTH = 6
TOTAL_SET_BYTES_CAP = 1_500_000_000


class DocPage(BaseModel):
    """One captured page in a set."""

    id: str
    set_id: str
    url: str
    title: str
    status: PageStatus
    error: str | None = None
    artifact_id: str | None = None
    source_id: str | None = None
    parent_id: str | None = None
    depth: int = 0
    ordinal: int = 0
    bytes: int = 0


class DocSet(BaseModel):
    id: str
    title: str
    seed_url: str
    prefix: str
    library: str
    status: SetStatus
    error: str | None = None
    page_count: int = 0
    max_pages: int = DEFAULT_MAX_PAGES
    created_at: str
    last_crawled_at: str | None = None


class CreateSetRequest(BaseModel):
    """Start a doc set from a seed URL.

    `prefix` bounds the crawl and defaults to the seed's own directory — the seed
    `https://x.dev/docs/start` yields `https://x.dev/docs/`, which is almost always
    what "these docs" means. Pass it explicitly when the default is wrong.
    """

    seed_url: str
    title: str | None = None
    prefix: str | None = None
    library: str | None = None
    max_pages: int = Field(default=DEFAULT_MAX_PAGES, ge=1, le=MAX_MAX_PAGES)
    max_depth: int = Field(default=DEFAULT_MAX_DEPTH, ge=0, le=20)


class SetsResponse(BaseModel):
    sets: list[DocSet]


class PagesResponse(BaseModel):
    pages: list[DocPage]


class CrawlProgress(BaseModel):
    """What the `docviewer` `/ws` channel reports while a crawl runs."""

    set_id: str
    status: SetStatus
    captured: int = 0
    failed: int = 0
    queued: int = 0
    current_url: str | None = None
    error: str | None = None


class SearchRequest(BaseModel):
    query: str
    limit: int = Field(default=10, ge=1, le=50)


class SearchHit(BaseModel):
    page_id: str | None = None
    url: str | None = None
    title: str
    snippet: str
    score: float | None = None


class SearchResponse(BaseModel):
    hits: list[SearchHit]


class OkResponse(BaseModel):
    ok: bool = True

"""API-boundary models for the knowledge base (`/api/kb`). See docs/modules/kb.mdx."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Kind = Literal[
    "bookmark",
    "article",
    "primer",
    "course",
    "chapter",
    "lesson",
    "paper",
    "video",
    "site",
    "note",
]
Status = Literal["to_read", "reading", "done", "archived"]
IndexMode = Literal["link", "text", "archive", "site"]
# Derived from the linked library source on every read, never stored — see
# `kb/store.py`. `none` means the item has no indexed content (a plain link).
IndexStatus = Literal["none", "queued", "indexing", "ready", "failed", "skipped"]


class Progress(BaseModel):
    """Leaf completion under a course/chapter: `done` of `total` lessons."""

    done: int = 0
    total: int = 0


class Item(BaseModel):
    id: str
    kind: Kind
    url: str | None = None
    canonical_url: str | None = None
    # The canonical URL the page itself declared, when that differs from `url`.
    alias_url: str | None = None
    title: str
    description: str = ""
    author: str | None = None
    tags: list[str] = Field(default_factory=list)
    status: Status = "to_read"
    rating: int | None = None
    notes: str = ""
    parent_id: str | None = None
    ordinal: int = 0
    index_mode: IndexMode = "link"
    index_status: IndexStatus = "none"
    index_error: str | None = None
    library: str | None = None
    source_id: str | None = None
    chunk_count: int = 0
    docset_id: str | None = None
    docpage_id: str | None = None
    artifact_id: str | None = None
    content_signal: str | None = None
    starred: bool = False
    profile_id: str | None = None
    collections: list[str] = Field(default_factory=list)
    added_at: str = ""
    updated_at: str | None = None
    opened_at: str | None = None
    completed_at: str | None = None
    # Present for items with children (a course, a chapter).
    progress: Progress | None = None
    child_count: int = 0


class ItemsResponse(BaseModel):
    items: list[Item]
    total: int


class TreeNode(BaseModel):
    item: Item
    children: list["TreeNode"] = Field(default_factory=list)


class CreateItemRequest(BaseModel):
    """Save something. `url` is required except for a `note`.

    Saving a URL that is already in the catalog **updates** the existing item with
    the fields you sent instead of failing — "save this page" is idempotent.
    """

    url: str | None = None
    kind: Kind | None = None
    title: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    collections: list[str] | None = None
    status: Status | None = None
    notes: str | None = None
    rating: int | None = Field(default=None, ge=0, le=5)
    starred: bool | None = None
    index_mode: IndexMode = "link"
    parent_id: str | None = None


class UpdateItemRequest(BaseModel):
    title: str | None = None
    kind: Kind | None = None
    description: str | None = None
    tags: list[str] | None = None
    collections: list[str] | None = None
    status: Status | None = None
    notes: str | None = None
    rating: int | None = Field(default=None, ge=0, le=5)
    starred: bool | None = None


class IndexRequest(BaseModel):
    mode: IndexMode


class Collection(BaseModel):
    id: str
    name: str
    description: str = ""
    item_count: int = 0


class CollectionsResponse(BaseModel):
    collections: list[Collection]


class CreateCollectionRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = ""


class LookupResponse(BaseModel):
    """`item` is null when the URL is not in the catalog."""

    item: Item | None = None


class Summary(BaseModel):
    items: int
    indexed: int
    by_kind: dict[str, int]
    collections: list[str]


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    collections: list[str] | None = None
    item_id: str | None = None
    limit: int = Field(default=8, ge=1, le=30)


class Passage(BaseModel):
    """One retrieved chunk, with everything needed to cite and open it."""

    item_id: str | None = None
    source_id: str
    title: str
    section: str | None = None
    url: str | None = None
    # `url#anchor` when the chunk's heading has an id; the deep link to cite.
    link: str | None = None
    # The offline copy, when one exists: a doc-set page or a stored artifact.
    docpage_id: str | None = None
    artifact_id: str | None = None
    library: str
    score: float
    text: str


class SearchResponse(BaseModel):
    query: str
    passages: list[Passage]
    # Libraries searched — so an empty result can say *where* it looked.
    libraries: list[str]
    # Set when the vectors of the searched libraries came from different models and
    # the lists had to be fused by rank instead of merged by score.
    fused_by_rank: bool = False


class OkResponse(BaseModel):
    ok: bool = True
    deleted: int = 0


class ImportRequest(BaseModel):
    """Import a site. Name a `profile_id`, or just pass a `url` — a matching
    profile is picked (an apxml course URL resolves to that one course), and a URL
    no profile knows gets a generic crawl of its own directory."""

    profile_id: str | None = None
    url: str | None = None
    params: dict[str, str] = Field(default_factory=dict)
    max_pages: int | None = Field(default=None, ge=1, le=2000)
    collections: list[str] | None = None
    # Re-capture pages already archived (otherwise a re-import resumes).
    force: bool = False


class ImportResponse(BaseModel):
    root_item_id: str
    set_id: str
    resumed: bool
    plan: dict[str, Any]


class ProfilesResponse(BaseModel):
    profiles: list[dict[str, Any]]


class ProfileMatch(BaseModel):
    """Which profile applies to a URL — what the browser asks to offer an import."""

    profile: dict[str, Any] | None = None
    params: dict[str, str] = Field(default_factory=dict)

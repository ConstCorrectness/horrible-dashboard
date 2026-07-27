"""Pydantic models for the `/api/search` boundary."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Freshness = Literal["day", "week", "month", "year"]


class SearchRequest(BaseModel):
    query: str
    limit: int = Field(default=8, ge=1, le=25)
    # `deep` runs the full pipeline (rewrite, fetch, rerank) and takes seconds;
    # `quick` is the fan-out only and returns in well under one.
    depth: Literal["quick", "deep"] = "quick"
    providers: list[str] | None = None
    site: str | None = None
    freshness: Freshness | None = None
    use_cache: bool = True


class SearchHitModel(BaseModel):
    url: str
    title: str
    snippet: str = ""
    text: str | None = None
    score: float = 0.0
    providers: list[str] = Field(default_factory=list)
    published: str | None = None
    host: str = ""


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHitModel]
    rewrites: list[str] = Field(default_factory=list)
    providers_used: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    cached: int = 0
    elapsed_ms: int = 0


class ProviderModel(BaseModel):
    id: str
    label: str
    needs_key: bool
    configured: bool
    # Why it can't run, when it can't. Empty when it can.
    reason: str = ""


class ProvidersResponse(BaseModel):
    providers: list[ProviderModel]
    # The provider *name* is public — only the key is secret — so it is safe to hand
    # the whole selection to the browser.
    selected: str = "auto"
    active: list[str] = Field(default_factory=list)


class ReadRequest(BaseModel):
    url: str
    use_cache: bool = True


class ReadResponse(BaseModel):
    url: str
    title: str = ""
    author: str = ""
    text: str = ""
    truncated: bool = False
    cached: bool = False


class SeedModel(BaseModel):
    id: str
    label: str
    config: dict[str, Any]
    enabled: bool
    builtin: bool
    last_crawled_at: str | None = None
    last_status: str | None = None
    last_error: str | None = None
    pages: int = 0


class SeedsResponse(BaseModel):
    seeds: list[SeedModel]


class SeedUpsertRequest(BaseModel):
    id: str
    label: str = ""
    start_urls: list[str] = Field(default_factory=list)
    allow_domains: list[str] = Field(default_factory=list)
    allow_patterns: list[str] = Field(default_factory=list)
    deny_patterns: list[str] = Field(default_factory=list)
    max_depth: int = Field(default=2, ge=0, le=4)
    max_pages: int = Field(default=200, ge=1, le=2000)
    recrawl_days: int = Field(default=14, ge=0, le=365)
    tags: list[str] = Field(default_factory=list)


class CrawlRequest(BaseModel):
    # Omit to enqueue every seed that is due.
    seed_id: str | None = None
    force: bool = False


class CrawlQueued(BaseModel):
    queued: list[str]


class IndexStatus(BaseModel):
    collection: str
    docs: int
    embed_model: str | None = None
    dim: int | None = None
    reindex_needed: bool = False


class CrawlStatusResponse(BaseModel):
    seeds: list[SeedModel]
    index: IndexStatus

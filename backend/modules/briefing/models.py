"""Wire models for `/api/briefing/*`."""

from __future__ import annotations

from pydantic import BaseModel


class PaperModel(BaseModel):
    arxiv_id: str
    title: str
    summary: str
    authors: list[str]
    organization: str | None = None
    upvotes: int
    comments: int
    github_url: str | None = None
    github_stars: int | None = None
    thumbnail: str | None = None
    submitted_at: str


class StoryModel(BaseModel):
    id: str
    title: str
    url: str
    domain: str
    points: int
    comments: int
    created_at: int
    discussion_url: str


class PapersResponse(BaseModel):
    papers: list[PaperModel]
    #: Epoch seconds of the upstream fetch this answer came from.
    fetched_at: float
    #: True when the refresh failed and this is the last good answer.
    stale: bool = False


class NewsResponse(BaseModel):
    stories: list[StoryModel]
    fetched_at: float
    stale: bool = False

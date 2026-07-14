"""Pydantic models for the browser module's HTTP surface (`/api/browser`)."""

from __future__ import annotations

from pydantic import BaseModel


class ReaderResponse(BaseModel):
    """Reader-mode result: server-side extracted, readable page content."""

    url: str
    title: str
    author: str | None = None
    text: str


class HistoryEntry(BaseModel):
    id: str
    url: str
    title: str
    visited_at: str


class HistoryListResponse(BaseModel):
    entries: list[HistoryEntry]


class RecordHistoryRequest(BaseModel):
    url: str
    title: str = ""


class Bookmark(BaseModel):
    id: str
    url: str
    title: str
    tags: list[str] = []
    added_at: str


class BookmarksResponse(BaseModel):
    bookmarks: list[Bookmark]


class AddBookmarkRequest(BaseModel):
    url: str
    title: str = ""
    tags: list[str] = []


class OkResponse(BaseModel):
    ok: bool = True


class EngineStatus(BaseModel):
    """Availability of the real headless-Chromium engine (`full` browser mode)."""

    enabled: bool  # HORRIBLE_ENABLE_SERVER_BROWSER=1
    installed: bool  # the `browser-engine` extra (playwright) is importable

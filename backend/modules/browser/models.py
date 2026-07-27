"""Pydantic models for the browser module's HTTP surface (`/api/browser`)."""

from __future__ import annotations

from pydantic import BaseModel, Field


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


# --- network probes ---------------------------------------------------------


class NetProbeRequest(BaseModel):
    """A host or URL to probe. POST, not GET: the target is user input and a query
    string would put it in logs and referrers."""

    target: str
    record_type: str = "A"


class DnsHopModel(BaseModel):
    """One rung of the delegation ladder: who answered, and what they said."""

    level: str
    zone: str
    server: str
    server_name: str = ""
    query: str
    referral: list[str] = Field(default_factory=list)
    referral_zone: str = ""
    # Addresses shipped with a referral. Without glue, resolving an in-zone
    # nameserver would be circular — which is why it exists at all.
    glue: dict[str, list[str]] = Field(default_factory=dict)
    answers: list[str] = Field(default_factory=list)
    # An alias answer: resolution continues at this name, usually in another zone.
    cname: str = ""
    # Whether the parent published a DS record for the child — one link of the
    # DNSSEC chain of trust.
    signed: bool = False
    rtt_ms: float | None = None
    error: str | None = None


class DnsChainResponse(BaseModel):
    name: str
    record_type: str
    hops: list[DnsHopModel]
    addresses: list[str]
    dnssec: bool
    elapsed_ms: int
    notes: list[str] = Field(default_factory=list)


class GeoPoint(BaseModel):
    ip: str
    lat: float
    lon: float
    city: str | None = None
    country: str | None = None


class TraceHopModel(BaseModel):
    ttl: int
    host: str = ""
    ip: str = ""
    rtt_ms: list[float] = Field(default_factory=list)
    # A router that doesn't answer ICMP is still a hop the packets crossed; it is
    # reported rather than dropped so the path length stays honest.
    timeout: bool = False
    geo: GeoPoint | None = None


class TraceResponse(BaseModel):
    host: str
    hops: list[TraceHopModel]
    elapsed_ms: int = 0
    error: str | None = None


class GeoStatus(BaseModel):
    available: bool
    path: str
    attribution: str
    hint: str

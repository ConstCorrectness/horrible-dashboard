"""HTTP surface for the embedded browser (`/api/browser/*`).

- `GET /read` — reader mode: SSRF-safe server-side fetch + main-content extraction,
  so a page that refuses iframing can still be read inline.
- `GET/POST/DELETE /history` and `/bookmarks` — the server-side catalog backing the
  panel's history dropdown and bookmarks strip.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, Query

from backend.modules.browser import store
from backend.modules.browser.fetch import UnsafeUrlError, fetch_readable
from backend.modules.browser.models import (
    AddBookmarkRequest,
    Bookmark,
    BookmarksResponse,
    DnsChainResponse,
    EngineStatus,
    GeoStatus,
    HistoryEntry,
    HistoryListResponse,
    NetProbeRequest,
    OkResponse,
    ReaderResponse,
    RecordHistoryRequest,
    TraceHopModel,
    TraceResponse,
)
from backend.modules.browser.session import server_browser_enabled

router = APIRouter(prefix="/browser", tags=["browser"])


@router.get("/engine", response_model=EngineStatus)
def engine_status() -> EngineStatus:
    """Whether the real backend browser engine is available (gated + installed).

    The panel calls this to decide `auto` mode: full engine when on, iframe otherwise.
    Reports installed=False if Playwright isn't importable so the UI can hint the extra.
    """
    installed = True
    try:
        import playwright  # noqa: F401
    except Exception:  # noqa: BLE001
        installed = False
    return EngineStatus(enabled=server_browser_enabled(), installed=installed)


@router.get("/read", response_model=ReaderResponse)
async def read(url: str = Query(..., description="page URL to read")) -> ReaderResponse:
    """Fetch and extract the readable article for `url` (SSRF-guarded)."""
    try:
        article = await fetch_readable(url)
    except UnsafeUrlError as exc:
        raise HTTPException(status_code=400, detail=f"unsafe URL: {exc}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"fetch failed: {exc}") from exc
    return ReaderResponse(
        url=article.url,
        title=article.title,
        author=article.author,
        text=article.text,
    )


@router.get("/history", response_model=HistoryListResponse)
def get_history(limit: int = 100) -> HistoryListResponse:
    return HistoryListResponse(
        entries=[HistoryEntry(**e) for e in store.list_history(limit)]
    )


@router.post("/history", response_model=HistoryEntry)
def add_history(req: RecordHistoryRequest) -> HistoryEntry:
    if not req.url.strip():
        raise HTTPException(status_code=400, detail="url is required")
    return HistoryEntry(**store.record_visit(req.url, req.title or req.url))


@router.delete("/history", response_model=OkResponse)
def delete_history() -> OkResponse:
    store.clear_history()
    return OkResponse()


@router.get("/bookmarks", response_model=BookmarksResponse)
def get_bookmarks() -> BookmarksResponse:
    return BookmarksResponse(bookmarks=[Bookmark(**b) for b in store.list_bookmarks()])


@router.post("/bookmarks", response_model=Bookmark)
def create_bookmark(req: AddBookmarkRequest) -> Bookmark:
    if not req.url.strip():
        raise HTTPException(status_code=400, detail="url is required")
    return Bookmark(**store.add_bookmark(req.url, req.title or req.url, req.tags))


@router.delete("/bookmarks/{bookmark_id}", response_model=OkResponse)
def delete_bookmark(bookmark_id: str) -> OkResponse:
    if not store.delete_bookmark(bookmark_id):
        raise HTTPException(status_code=404, detail="bookmark not found")
    return OkResponse()


# --- network probes ---------------------------------------------------------
#
# The educational half of the network view. Everything else in this module reports
# what the browser *did*; these answer how the web underneath it works — where a
# name comes from, and what path the packets take.
#
# All three validate the target against the SSRF guard's public-IP check first.
# Probing internal hosts is a reconnaissance primitive, and "the user clicked it"
# is not a defence when the target could have been suggested by a page.


def _probe_target(value: str) -> str:
    """The hostname to probe, from a bare host or a URL. Rejects private targets."""
    from urllib.parse import urlsplit

    from backend.modules.browser.fetch import _check_host_public

    raw = (value or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="a host or URL is required")
    host = urlsplit(raw).hostname if "//" in raw else raw.split("/")[0]
    if not host:
        raise HTTPException(
            status_code=400, detail=f"couldn't read a host from {raw!r}"
        )
    try:
        _check_host_public(host)
    except UnsafeUrlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return host


@router.post("/net/dns", response_model=DnsChainResponse)
async def probe_dns(req: NetProbeRequest) -> DnsChainResponse:
    """Walk the DNS delegation from a root server down to the authoritative answer.

    Plain UDP/53, so it needs no elevated privileges — which is what makes this the
    centrepiece of the network view rather than a footnote.
    """
    from backend.modules.browser import netprobe

    host = _probe_target(req.target)
    chain = await netprobe.resolve_chain(host, req.record_type or "A")
    return DnsChainResponse(**chain.to_dict())


@router.post("/net/trace", response_model=TraceResponse)
async def probe_trace(req: NetProbeRequest) -> TraceResponse:
    """Trace the network path to a host, annotating hops with location when possible."""
    from backend.modules.browser import netprobe

    host = _probe_target(req.target)
    result = await netprobe.traceroute(host)
    hops = []
    for hop in result["hops"]:
        located = netprobe.locate(hop["ip"]) if hop.get("ip") else None
        hops.append(TraceHopModel(**hop, geo=located))
    return TraceResponse(
        host=result["host"],
        hops=hops,
        elapsed_ms=result.get("elapsed_ms", 0),
        error=result.get("error"),
    )


@router.get("/net/geo", response_model=GeoStatus)
def geo_status() -> GeoStatus:
    """Whether route locations can be plotted, and how to enable it if not."""
    from backend.modules.browser import netprobe

    return GeoStatus(**netprobe.geo_status())

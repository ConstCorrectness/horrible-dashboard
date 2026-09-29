"""Sources for the AI briefing: the week's top papers and today's AI headlines.

Two keyless public APIs, each read on demand and held in a small TTL cache:

- **Papers** — Hugging Face's Daily Papers (`/api/daily_papers?week=YYYY-Www`).
  arXiv itself has no notion of "top": its API sorts by relevance or date and
  nothing else. HF's daily list is arXiv papers that people submitted and
  upvoted, which is the closest public signal to "what the field read this
  week". Every entry carries its arXiv id, so the library's arXiv download works
  on it unchanged.
- **Headlines** — Hacker News via Algolia's search API, one query per term
  (Algolia ANDs the words of a single query) restricted to titles, because "AI"
  otherwise matches inside every URL and self-post body.

"This week" is a rolling seven days, not the ISO week: on a Monday the current
ISO week holds a handful of papers and the briefing would be nearly empty. Two
ISO weeks are fetched and filtered to the window.

A failed refresh falls back to the last good answer, marked `stale` — a
briefing from an hour ago beats an error card over breakfast.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx

logger = logging.getLogger(__name__)

HF_PAPERS_URL = "https://huggingface.co/api/daily_papers"
HN_SEARCH_URL = "https://hn.algolia.com/api/v1/search"
_UA = "horrible-dashboard/0.1 (briefing module)"
_TIMEOUT = 15.0

PAPERS_TTL_S = 30 * 60.0
NEWS_TTL_S = 15 * 60.0

PAPER_WINDOW = timedelta(days=7)
PAPER_LIMIT = 12
SUMMARY_CHARS = 420

NEWS_TERMS = ("AI", "LLM", "OpenAI", "Anthropic", "GPT", "Gemini")
NEWS_WINDOW = timedelta(hours=48)
NEWS_MIN_POINTS = 20
NEWS_LIMIT = 12


class BriefingSourceError(RuntimeError):
    """A source could not be reached and there is no earlier answer to fall back to."""


@dataclass(frozen=True)
class Paper:
    arxiv_id: str
    title: str
    summary: str
    authors: list[str]
    organization: str | None
    upvotes: int
    comments: int
    github_url: str | None
    github_stars: int | None
    thumbnail: str | None
    submitted_at: str


@dataclass(frozen=True)
class Story:
    id: str
    title: str
    url: str
    domain: str
    points: int
    comments: int
    created_at: int
    discussion_url: str


@dataclass
class _Cached:
    fetched_at: float
    value: Any


@dataclass
class _Slot:
    """One cached source, and a lock so concurrent opens share one fetch."""

    ttl: float
    entry: _Cached | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_papers = _Slot(PAPERS_TTL_S)
_news = _Slot(NEWS_TTL_S)


def reset_cache() -> None:
    """Tests only: forget every cached answer."""
    _papers.entry = None
    _news.entry = None


async def _get_json(url: str) -> Any:
    async with httpx.AsyncClient(timeout=_TIMEOUT, headers={"User-Agent": _UA}) as http:
        res = await http.get(url)
        res.raise_for_status()
        return res.json()


# --- papers ------------------------------------------------------------------


def iso_week(day: datetime) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0] + "…"


def _parse_time(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_papers(
    rows: list[dict[str, Any]], *, now: datetime, limit: int = PAPER_LIMIT
) -> list[Paper]:
    """Daily-papers rows → the window's most-upvoted papers, deduplicated by arXiv id."""
    since = now - PAPER_WINDOW
    seen: dict[str, Paper] = {}
    for row in rows:
        paper = row.get("paper") or {}
        arxiv_id = paper.get("id")
        title = row.get("title") or paper.get("title")
        if not arxiv_id or not title or arxiv_id in seen:
            continue
        submitted = _parse_time(
            paper.get("submittedOnDailyAt") or row.get("publishedAt")
        )
        if submitted is None or submitted < since:
            continue
        org = paper.get("organization") or row.get("organization") or {}
        repo = paper.get("githubRepo")
        seen[arxiv_id] = Paper(
            arxiv_id=arxiv_id,
            title=" ".join(title.split()),
            summary=_clip(
                paper.get("summary") or row.get("summary") or "", SUMMARY_CHARS
            ),
            authors=[
                a["name"]
                for a in paper.get("authors", [])
                if a.get("name") and not a.get("hidden")
            ][:4],
            organization=(org.get("fullname") or org.get("name"))
            if isinstance(org, dict)
            else None,
            upvotes=int(paper.get("upvotes") or 0),
            comments=int(row.get("numComments") or 0),
            github_url=repo
            if isinstance(repo, str) and repo.startswith("https://")
            else None,
            github_stars=paper.get("githubStars")
            if isinstance(paper.get("githubStars"), int)
            else None,
            thumbnail=row.get("thumbnail")
            if isinstance(row.get("thumbnail"), str)
            else None,
            submitted_at=submitted.isoformat(),
        )
    return sorted(seen.values(), key=lambda p: p.upvotes, reverse=True)[:limit]


async def _fetch_papers() -> list[Paper]:
    now = datetime.now(timezone.utc)
    weeks = sorted({iso_week(now), iso_week(now - PAPER_WINDOW)})
    results = await asyncio.gather(
        *(
            _get_json(f"{HF_PAPERS_URL}?{urlencode({'week': w, 'limit': 100})}")
            for w in weeks
        ),
        return_exceptions=True,
    )
    rows: list[dict[str, Any]] = []
    errors: list[BaseException] = []
    for r in results:
        if isinstance(r, BaseException):
            errors.append(r)
        elif isinstance(r, list):
            rows.extend(r)
    if not rows and errors:
        raise BriefingSourceError(f"Hugging Face papers unreachable: {errors[0]}")
    return parse_papers(rows, now=now)


# --- headlines -----------------------------------------------------------------


def parse_stories(
    hits: list[dict[str, Any]], *, limit: int = NEWS_LIMIT
) -> list[Story]:
    seen: dict[str, Story] = {}
    for hit in hits:
        oid = hit.get("objectID")
        title = hit.get("title")
        if not oid or not title or oid in seen:
            continue
        discussion = f"https://news.ycombinator.com/item?id={oid}"
        url = hit.get("url") or discussion
        domain = (urlparse(url).hostname or "news.ycombinator.com").removeprefix("www.")
        seen[oid] = Story(
            id=oid,
            title=title,
            url=url,
            domain=domain,
            points=int(hit.get("points") or 0),
            comments=int(hit.get("num_comments") or 0),
            created_at=int(hit.get("created_at_i") or 0),
            discussion_url=discussion,
        )
    return sorted(seen.values(), key=lambda s: s.points, reverse=True)[:limit]


async def _fetch_news() -> list[Story]:
    since = int((datetime.now(timezone.utc) - NEWS_WINDOW).timestamp())

    def url(term: str) -> str:
        return f"{HN_SEARCH_URL}?" + urlencode(
            {
                "query": term,
                "tags": "story",
                "restrictSearchableAttributes": "title",
                "numericFilters": f"created_at_i>{since},points>{NEWS_MIN_POINTS}",
                "hitsPerPage": 20,
            }
        )

    results = await asyncio.gather(
        *(_get_json(url(t)) for t in NEWS_TERMS), return_exceptions=True
    )
    hits: list[dict[str, Any]] = []
    errors: list[BaseException] = []
    for r in results:
        if isinstance(r, BaseException):
            errors.append(r)
        elif isinstance(r, dict):
            hits.extend(r.get("hits") or [])
    if not hits and errors:
        raise BriefingSourceError(f"Hacker News unreachable: {errors[0]}")
    return parse_stories(hits)


# --- cache ---------------------------------------------------------------------


async def _cached(slot: _Slot, fetch, *, refresh: bool) -> tuple[Any, float, bool]:
    """(value, fetched_at, stale). Stale means "the refresh failed; this is older"."""
    async with slot.lock:
        entry = slot.entry
        if entry and not refresh and time.time() - entry.fetched_at < slot.ttl:
            return entry.value, entry.fetched_at, False
        try:
            value = await fetch()
        except (httpx.HTTPError, BriefingSourceError, ValueError) as exc:
            if entry is None:
                raise BriefingSourceError(str(exc)) from exc
            logger.warning("briefing refresh failed, serving cached copy: %s", exc)
            return entry.value, entry.fetched_at, True
        slot.entry = _Cached(time.time(), value)
        return value, slot.entry.fetched_at, False


async def top_papers(*, refresh: bool = False) -> tuple[list[Paper], float, bool]:
    return await _cached(_papers, _fetch_papers, refresh=refresh)


async def ai_news(*, refresh: bool = False) -> tuple[list[Story], float, bool]:
    return await _cached(_news, _fetch_news, refresh=refresh)

"""Briefing module: parsing (fixtures, no network), the rolling paper window, the
stale-copy fallback, and the HTTP responses end to end with a faked upstream."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.briefing import client

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


def _paper_row(
    arxiv_id: str, upvotes: int, days_ago: float, **extra: Any
) -> dict[str, Any]:
    submitted = (NOW - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")
    return {
        "title": f"Paper {arxiv_id}",
        "numComments": 3,
        "thumbnail": f"https://cdn-thumbnails.huggingface.co/social-thumbnails/papers/{arxiv_id}.png",
        "paper": {
            "id": arxiv_id,
            "title": f"Paper {arxiv_id}",
            "summary": "  A   summary\nwith   odd whitespace.  ",
            "upvotes": upvotes,
            "submittedOnDailyAt": submitted,
            "authors": [
                {"name": "Ada"},
                {"name": "Hidden Person", "hidden": True},
                {"name": "Grace"},
            ],
            **extra,
        },
    }


def _hit(
    oid: str, points: int, url: str | None = "https://www.example.com/a"
) -> dict[str, Any]:
    return {
        "objectID": oid,
        "title": f"Story {oid}",
        "url": url,
        "points": points,
        "num_comments": 7,
        "created_at_i": int(NOW.timestamp()),
    }


@pytest.fixture(autouse=True)
def fresh_cache():
    client.reset_cache()
    yield
    client.reset_cache()


@pytest.fixture
def api() -> TestClient:
    return TestClient(app)


# --- parsing -------------------------------------------------------------------


def test_papers_rank_by_upvotes_within_the_rolling_week() -> None:
    rows = [
        _paper_row("2609.00001", 10, days_ago=1),
        _paper_row("2609.00002", 99, days_ago=2),
        _paper_row("2609.00003", 500, days_ago=9),  # outside the 7-day window
        _paper_row("2609.00002", 99, days_ago=2),  # the same paper in both ISO weeks
    ]
    papers = client.parse_papers(rows, now=NOW)
    assert [p.arxiv_id for p in papers] == ["2609.00002", "2609.00001"]


def test_paper_fields_are_cleaned() -> None:
    [p] = client.parse_papers(
        [
            _paper_row(
                "2609.00004",
                5,
                days_ago=0.5,
                githubRepo="https://github.com/org/repo",
                githubStars=42,
                organization={"name": "org", "fullname": "Org Labs"},
            )
        ],
        now=NOW,
    )
    assert p.summary == "A summary with odd whitespace."
    assert p.authors == ["Ada", "Grace"]  # hidden authors dropped
    assert p.organization == "Org Labs"
    assert p.github_url == "https://github.com/org/repo"
    assert p.github_stars == 42


def test_non_https_repo_is_not_linked() -> None:
    [p] = client.parse_papers(
        [_paper_row("2609.00005", 1, days_ago=1, githubRepo="javascript:alert(1)")],
        now=NOW,
    )
    assert p.github_url is None


def test_long_summary_is_clipped_on_a_word() -> None:
    row = _paper_row("2609.00006", 1, days_ago=1)
    row["paper"]["summary"] = "word " * 400
    [p] = client.parse_papers([row], now=NOW)
    assert len(p.summary) <= client.SUMMARY_CHARS
    assert p.summary.endswith("word…")


def test_stories_dedupe_and_sort_and_fall_back_to_the_discussion() -> None:
    stories = client.parse_stories(
        [_hit("1", 30), _hit("2", 90, url=None), _hit("1", 30)]
    )
    assert [s.id for s in stories] == ["2", "1"]
    assert stories[0].url == "https://news.ycombinator.com/item?id=2"
    assert stories[0].domain == "news.ycombinator.com"
    assert stories[1].domain == "example.com"


def test_iso_week_label() -> None:
    assert client.iso_week(NOW) == "2026-W39"


# --- fetching, caching, fallback ------------------------------------------------


class FakeUpstream:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail = False

    async def __call__(self, url: str) -> Any:
        self.calls.append(url)
        if self.fail:
            raise httpx.ConnectError("down")
        if url.startswith(client.HF_PAPERS_URL):
            now = datetime.now(timezone.utc)
            submitted = (now - timedelta(days=1)).isoformat()
            row = _paper_row("2609.11111", 77, days_ago=0)
            row["paper"]["submittedOnDailyAt"] = submitted
            return [row]
        return {"hits": [_hit("h1", 55)]}


@pytest.fixture
def upstream(monkeypatch: pytest.MonkeyPatch) -> FakeUpstream:
    fake = FakeUpstream()
    monkeypatch.setattr(client, "_get_json", fake)
    return fake


def test_papers_route_returns_every_field(
    api: TestClient, upstream: FakeUpstream
) -> None:
    res = api.get("/api/briefing/papers")
    assert res.status_code == 200
    body = res.json()
    assert body["stale"] is False
    [paper] = body["papers"]
    assert paper["arxiv_id"] == "2609.11111"
    assert paper["upvotes"] == 77
    assert paper["authors"] == ["Ada", "Grace"]
    assert paper["thumbnail"].endswith("2609.11111.png")
    # Two ISO weeks are asked for, so a Monday briefing is not empty.
    assert all("week=" in u for u in upstream.calls)


def test_news_route_asks_titles_only(api: TestClient, upstream: FakeUpstream) -> None:
    res = api.get("/api/briefing/news")
    assert res.status_code == 200
    [story] = res.json()["stories"]
    assert story["id"] == "h1"
    assert story["discussion_url"] == "https://news.ycombinator.com/item?id=h1"
    assert len(upstream.calls) == len(client.NEWS_TERMS)
    assert all("restrictSearchableAttributes=title" in u for u in upstream.calls)


def test_second_read_is_served_from_cache(
    api: TestClient, upstream: FakeUpstream
) -> None:
    api.get("/api/briefing/news")
    first = len(upstream.calls)
    api.get("/api/briefing/news")
    assert len(upstream.calls) == first
    api.get("/api/briefing/news?refresh=true")
    assert len(upstream.calls) == 2 * first


def test_failed_refresh_serves_the_last_good_copy(
    api: TestClient, upstream: FakeUpstream
) -> None:
    assert api.get("/api/briefing/papers").json()["stale"] is False
    upstream.fail = True
    res = api.get("/api/briefing/papers?refresh=true")
    assert res.status_code == 200
    body = res.json()
    assert body["stale"] is True
    assert body["papers"][0]["arxiv_id"] == "2609.11111"


def test_failure_with_nothing_cached_is_a_502(
    api: TestClient, upstream: FakeUpstream
) -> None:
    upstream.fail = True
    res = api.get("/api/briefing/news")
    assert res.status_code == 502
    assert "Hacker News unreachable" in res.json()["detail"]

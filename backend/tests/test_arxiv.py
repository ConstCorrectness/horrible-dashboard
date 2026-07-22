"""ArXiv module: Atom parsing (fixture, no network), etiquette throttle, id
validation, and the download flow with a mocked guarded fetch."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.arxiv import client

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <title>ArXiv Query: search_query=all:attention</title>
  <opensearch:totalResults>4242</opensearch:totalResults>
  <opensearch:startIndex>0</opensearch:startIndex>
  <entry>
    <id>http://arxiv.org/abs/1706.03762v7</id>
    <updated>2023-08-02T00:41:18Z</updated>
    <published>2017-06-12T17:57:34Z</published>
    <title>Attention Is All You Need</title>
    <summary>  The dominant sequence transduction models are based on complex
  recurrent or convolutional neural networks.</summary>
    <author><name>Ashish Vaswani</name></author>
    <author><name>Noam Shazeer</name></author>
    <arxiv:comment>15 pages, 5 figures</arxiv:comment>
    <link href="http://arxiv.org/abs/1706.03762v7" rel="alternate" type="text/html"/>
    <link title="pdf" href="http://arxiv.org/pdf/1706.03762v7" rel="related" type="application/pdf"/>
    <category term="cs.CL" scheme="http://arxiv.org/schemas/atom"/>
    <category term="cs.LG" scheme="http://arxiv.org/schemas/atom"/>
  </entry>
</feed>
"""


@pytest.fixture
def api() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_cache():
    client._cache.clear()
    yield
    client._cache.clear()


def test_parse_feed_extracts_everything() -> None:
    total, entries = client.parse_feed(FEED)
    assert total == 4242
    assert len(entries) == 1
    e = entries[0]
    assert e.id == "1706.03762v7"
    assert e.title == "Attention Is All You Need"
    assert e.authors == ["Ashish Vaswani", "Noam Shazeer"]
    assert e.categories == ["cs.CL", "cs.LG"]
    assert e.pdf_url == "http://arxiv.org/pdf/1706.03762v7"
    assert e.abs_url == "http://arxiv.org/abs/1706.03762v7"
    assert e.comment == "15 pages, 5 figures"
    # Whitespace in the summary collapses.
    assert "  " not in e.summary


def test_parse_feed_garbage_raises() -> None:
    with pytest.raises(client.ArxivError):
        client.parse_feed("this is not xml")


@pytest.mark.parametrize(
    ("candidate", "ok"),
    [
        ("1706.03762", True),
        ("1706.03762v7", True),
        ("2401.12345", True),
        ("cs/0112017", True),
        ("cs.AI/0112017", True),  # old-style archive.SubjectClass/YYMMNNN
        ("math-ph/0112017", True),
        ("../etc/passwd", False),
        ("1706.03762v7; rm -rf", False),
        ("", False),
    ],
)
def test_id_validation(candidate: str, ok: bool) -> None:
    assert client.valid_id(candidate) is ok


def test_search_caches_and_throttles(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fake_get(url: str) -> str:
        calls.append(url)
        return FEED

    monkeypatch.setattr(client, "_throttled_get", fake_get)

    async def run() -> None:
        r1 = await client.search("attention")
        r2 = await client.search("attention")  # cache hit — no second request
        assert r1[0] == r2[0] == 4242
        await client.search("attention", category="cs.LG")  # different key

    asyncio.run(run())
    assert len(calls) == 2


def test_throttle_spacing(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    class FakeResponse:
        text = FEED

        def raise_for_status(self) -> None: ...

    class FakeClient:
        def __init__(self, **kwargs) -> None: ...
        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *args) -> None: ...
        async def get(self, url: str) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr(client.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(client.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(client, "_last_request", 0.0)

    async def run() -> None:
        await client._throttled_get("https://export.arxiv.org/api/query?a=1")
        await client._throttled_get("https://export.arxiv.org/api/query?a=2")

    asyncio.run(run())
    # The second request had to wait ~the full interval.
    assert sleeps and sleeps[-1] > 2.0


def test_get_paper_rejects_bad_id() -> None:
    with pytest.raises(client.ArxivError, match="not an arXiv id"):
        asyncio.run(client.get_paper("../../etc"))


def test_search_route(api: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get(url: str) -> str:
        return FEED

    monkeypatch.setattr(client, "_throttled_get", fake_get)
    res = api.get("/api/arxiv/search", params={"query": "attention"})
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 4242
    assert body["entries"][0]["id"] == "1706.03762v7"

    res = api.get("/api/arxiv/search")
    assert res.status_code == 400  # empty query


def test_download_route_files_pdf(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.modules.research import service
    from backend.tests.test_drive_pdf import make_pdf

    async def fake_get(url: str) -> str:
        return FEED

    async def fake_fetch_bytes(url: str, **kwargs) -> tuple[str, bytes]:
        assert url == "http://arxiv.org/pdf/1706.03762v7"
        return url, make_pdf("Attention body")

    monkeypatch.setattr(client, "_throttled_get", fake_get)
    monkeypatch.setattr(service, "safe_fetch_bytes", fake_fetch_bytes)

    res = api.post("/api/arxiv/download", json={"arxiv_id": "1706.03762v7"})
    assert res.status_code == 200
    body = res.json()
    assert body["source"]["title"] == "Attention Is All You Need"
    assert body["source"]["type"] == "pdf"
    assert body["source"]["url"] == "http://arxiv.org/abs/1706.03762v7"  # citable page
    assert "arxiv" in body["source"]["tags"]
    assert body["artifact"]["kind"] == "pdf"
    assert body["entry"]["authors"] == ["Ashish Vaswani", "Noam Shazeer"]

    res = api.post("/api/arxiv/download", json={"arxiv_id": "not-an-id"})
    assert res.status_code == 404

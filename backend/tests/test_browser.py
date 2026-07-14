"""Tests for the embedded browser module: the SSRF guard, reader-mode route, and
the history/bookmarks store. No real network — DNS resolution is monkeypatched and
reader-mode fetch is stubbed. The autouse `isolate_data_dir` fixture points the
store at a temp DB per test.
"""

from __future__ import annotations

import socket

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.browser import fetch, store
from backend.modules.browser.fetch import UnsafeUrlError, _validate
from backend.modules.library.extract import Article

client = TestClient(app)


# ---- SSRF guard ------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/",
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://[::1]/",
        "file:///etc/passwd",
        "ftp://example.com/",
        "https:///nohost",
    ],
)
def test_ssrf_blocks_internal_and_bad_schemes(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        _validate(url)


def test_ssrf_blocks_dns_name_resolving_to_private(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A public-looking name that resolves to a private IP must still be rejected.
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("10.1.2.3", 0))]
    )
    with pytest.raises(UnsafeUrlError):
        _validate("https://sneaky.example.com/")


def test_ssrf_allows_public_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))]
    )
    _validate("https://example.com/")  # no raise


# ---- reader-mode route -----------------------------------------------------


def test_read_returns_extracted_article(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(url: str) -> Article:
        return Article(title="Hello", author="Ada", text="Body text.", url=url)

    monkeypatch.setattr("backend.modules.browser.routes.fetch_readable", fake)
    r = client.get("/api/browser/read", params={"url": "https://example.com/post"})
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "Hello" and body["author"] == "Ada"
    assert body["text"] == "Body text."


def test_read_rejects_unsafe_url(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(url: str) -> Article:
        raise UnsafeUrlError("non-public")

    monkeypatch.setattr("backend.modules.browser.routes.fetch_readable", boom)
    r = client.get("/api/browser/read", params={"url": "http://127.0.0.1/"})
    assert r.status_code == 400


def test_safe_fetch_html_reuses_library_extract() -> None:
    # extract_article is the shared, network-free extractor the fetch composes.
    art = fetch.extract_article("<title>T</title><p>Words here.</p>", "https://x.test/")
    assert art.title == "T"
    assert "Words here" in art.text


# ---- history + bookmarks store (via HTTP) -----------------------------------


def test_history_crud_and_dedup() -> None:
    assert (
        client.post(
            "/api/browser/history", json={"url": "https://a.test", "title": "A"}
        ).status_code
        == 200
    )
    # Same URL again upserts (one row, latest title).
    client.post("/api/browser/history", json={"url": "https://a.test", "title": "A2"})
    entries = client.get("/api/browser/history").json()["entries"]
    assert len(entries) == 1 and entries[0]["title"] == "A2"
    assert client.delete("/api/browser/history").json()["ok"] is True
    assert client.get("/api/browser/history").json()["entries"] == []


def test_bookmarks_crud_and_dedup() -> None:
    made = client.post(
        "/api/browser/bookmarks",
        json={"url": "https://b.test", "title": "B", "tags": ["x"]},
    ).json()
    assert made["tags"] == ["x"]
    # Same URL is idempotent — returns the existing bookmark id.
    dup = client.post(
        "/api/browser/bookmarks", json={"url": "https://b.test", "title": "dup"}
    ).json()
    assert dup["id"] == made["id"]
    assert len(client.get("/api/browser/bookmarks").json()["bookmarks"]) == 1
    assert client.delete(f"/api/browser/bookmarks/{made['id']}").json()["ok"] is True
    assert client.get("/api/browser/bookmarks").json()["bookmarks"] == []
    assert client.delete("/api/browser/bookmarks/nope").status_code == 404


def test_record_visit_requires_url() -> None:
    assert client.post("/api/browser/history", json={"url": "  "}).status_code == 400


def test_store_direct_roundtrip() -> None:
    store.record_visit("https://direct.test", "Direct")
    assert any(e["url"] == "https://direct.test" for e in store.list_history())

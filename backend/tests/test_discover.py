"""Tests for the Discover module.

Assertions are on the **HTTP response** wherever a route is involved: the routes
declare `response_model`s, and a model silently drops fields it doesn't declare. The
upstream clients are stubbed at their public seams (`hub_get`, `public_get`,
`arxiv.client._feed`, `kaggle_provider.client`), so nothing here touches the network.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.arxiv import client as arxiv_client
from backend.modules.connectors.providers import (
    github,
    github_api,
    huggingface,
    huggingface_routes,
    huggingface_tools,
)
from backend.modules.discover import routes
from backend.modules.discover.cache import Cache
from backend.modules.discover.sources import docs, github as gh_source, hf, kaggle
from backend.modules.discover.sources import papers, skills
from backend.modules.discover.sources.base import Query, SourceUnavailable


@pytest.fixture(autouse=True)
def _clean():
    routes.clear_state()
    skills._repo_cache.clear()
    yield
    routes.clear_state()
    skills._repo_cache.clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _no_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    async def none() -> None:
        return None

    monkeypatch.setattr(huggingface, "token", none)
    monkeypatch.setattr(github, "token", none)


HF_ROW = {
    "id": "acme/tiny-7b",
    "likes": 12,
    "trendingScore": 40,
    "downloads": 900,
    "downloadsAllTime": 5000,
    "pipeline_tag": "text-generation",
    "library_name": "transformers",
    "gated": "manual",
    "private": False,
    "tags": ["transformers", "license:apache-2.0", "llama"],
    "createdAt": "2026-09-01T00:00:00.000Z",
    "lastModified": "2026-09-20T00:00:00.000Z",
}
NEXT = '<https://huggingface.co/api/models?sort=trendingScore&cursor=abc%3D%3D>; rel="next"'


# ── Hugging Face ─────────────────────────────────────────────────────────────


def test_hf_link_cursor_and_rate_limit_parsing() -> None:
    assert hf.next_cursor({"link": NEXT}) == "abc=="
    assert hf.next_cursor({}) is None
    assert hf.rate_limit_reset({"RateLimit": '"api";r=0;t=175'}) == 175.0


def test_hf_item_labels_the_30_day_window_and_keeps_missing_metrics_null() -> None:
    row = dict(HF_ROW)
    del row["downloadsAllTime"]
    item = hf.to_item(row, "model")
    metrics = {m.key: m for m in item.metrics}
    assert metrics["downloads"].label == "downloads · 30d"
    assert metrics["downloads"].value == 900
    # Absent upstream → None, never 0.
    assert metrics["downloadsAllTime"].value is None
    labels = [b.label for b in item.badges]
    assert "gated · manual" in labels and "apache-2.0" in labels
    assert item.subtitle == "text-generation · transformers"


def test_hf_feed_over_http_serves_every_field(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    async def fake(path: str, *, params: Any = None) -> tuple[Any, dict[str, str]]:
        seen["path"], seen["params"] = path, params
        return [HF_ROW], {"link": NEXT}

    monkeypatch.setattr(huggingface_tools, "hub_get", fake)
    res = client.get(
        "/api/discover/hf/list", params={"kind": "model", "f.task": "text-generation"}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["feed_label"] == "Trending models on the Hub"
    assert body["cursor_next"] == "abc=="
    item = body["items"][0]
    assert item["metrics"][0] == {
        "key": "downloads",
        "label": "downloads · 30d",
        "value": 900.0,
        "unit": "count",
    }
    assert item["badges"][0]["tone"] == "warn"
    assert seen["path"] == "/models"
    assert ("sort", "trendingScore") in seen["params"]
    assert ("pipeline_tag", "text-generation") in seen["params"]
    assert ("expand[]", "downloadsAllTime") in seen["params"]


def test_hf_rate_limit_maps_to_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    async def limited(path: str, *, params: Any = None):
        return {"error": "Hugging Face returned 429: slow down"}, {
            "ratelimit": '"api";r=0;t=30'
        }

    monkeypatch.setattr(huggingface_tools, "hub_get", limited)
    with pytest.raises(SourceUnavailable) as err:
        asyncio.run(hf.HubSource().list(Query(kind="model")))
    assert err.value.status == "rate_limited"
    assert err.value.retry_after == 30.0


def test_hf_mine_reads_the_repos_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: the route read `results` while `_list_repos` answers `repos`, so
    "Mine" was always empty."""
    huggingface_routes.clear_cache()

    async def mine(args: dict[str, Any]) -> Any:
        return {"author": "me", "repos": [{"id": "me/a", "type": "model"}]}

    monkeypatch.setattr(huggingface_tools, "_list_repos", mine)
    res = client.get("/api/connectors/huggingface/mine")
    assert res.status_code == 200
    assert [r["id"] for r in res.json()["results"]] == ["me/a"]
    huggingface_routes.clear_cache()


def test_hf_public_reads_need_no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reads go out anonymously rather than refusing with "not connected"."""
    sent: dict[str, Any] = {}

    class FakeClient:
        def __init__(self, **_: Any) -> None: ...

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_: Any) -> None: ...

        async def get(self, url: str, params: Any = None, headers: Any = None) -> Any:
            import httpx

            sent["headers"] = headers
            return httpx.Response(
                200, json=[{"id": "a/b"}], request=httpx.Request("GET", url)
            )

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    data, _ = asyncio.run(huggingface_tools.hub_get("/models"))
    assert data == [{"id": "a/b"}]
    assert sent["headers"] == {}


# ── papers / arXiv ───────────────────────────────────────────────────────────


def test_paper_without_code_has_no_stars_metric() -> None:
    item = papers.to_item(
        {
            "title": "A  paper",
            "numComments": 3,
            "paper": {"id": "2609.00001", "upvotes": 9, "authors": [{"name": "X"}]},
        }
    )
    assert item is not None
    assert item.title == "A paper"
    assert [m.key for m in item.metrics] == ["upvotes", "comments"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("large language models", "all:large AND all:language AND all:models"),
        ('"chain of thought" agents', 'all:"chain of thought" AND all:agents'),
        ("au:hinton AND cat:cs.LG", "au:hinton AND cat:cs.LG"),
        ("rlhf OR", "all:rlhf"),
        ("", ""),
    ],
)
def test_arxiv_query_scopes_every_term(raw: str, expected: str) -> None:
    assert arxiv_client.build_query(raw) == expected


def test_arxiv_default_feed_ors_categories(monkeypatch: pytest.MonkeyPatch) -> None:
    urls: list[str] = []

    async def feed(url: str):
        urls.append(url)
        return 0, []

    monkeypatch.setattr(arxiv_client, "_feed", feed)
    asyncio.run(
        arxiv_client.search("", categories=["cs.LG", "cs.AI"], sort="submittedDate")
    )
    asyncio.run(arxiv_client.search("moe", categories=["cs.LG", "cs.AI"]))
    from urllib.parse import parse_qs, urlparse

    queries = [parse_qs(urlparse(u).query)["search_query"][0] for u in urls]
    assert queries == [
        "cat:cs.LG OR cat:cs.AI",
        "(cat:cs.LG OR cat:cs.AI) AND (all:moe)",
    ]


# ── GitHub ───────────────────────────────────────────────────────────────────


def test_github_default_feed_and_explicit_any() -> None:
    from datetime import datetime, timezone

    today = datetime(2026, 10, 1, tzinfo=timezone.utc)
    q, said = gh_source.build_q(Query(kind="repo"), today)
    assert q == "topic:machine-learning pushed:>2026-09-24 stars:>100"
    assert said == "machine-learning repos · pushed in the last 7 days"
    # An explicit empty filter means "any" — it must not snap back to the default.
    q, _ = gh_source.build_q(
        Query(kind="repo", filters={"topic": "", "window": ""}), today
    )
    assert q == "stars:>100"
    q, _ = gh_source.build_q(Query(kind="repo", q="vllm"), today)
    assert q == "vllm"


def test_github_rate_limit_sets_a_cooldown(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}

    async def limited(path: str, *, params: Any = None):
        calls["n"] += 1
        return {"error": "GitHub rate limit reached", "status": 429}, {
            "x-ratelimit-reset": str(int(__import__("time").time()) + 40)
        }

    monkeypatch.setattr(github_api, "public_get", limited)
    first = client.get("/api/discover/github/list", params={"kind": "repo"}).json()
    assert first["status"] == "rate_limited"
    assert 30 < first["retry_after"] <= 40
    assert "connecting GitHub" in first["message"]
    second = client.get(
        "/api/discover/github/list", params={"kind": "repo", "q": "other"}
    ).json()
    assert second["status"] == "rate_limited"
    assert calls["n"] == 1  # the cooldown answered; upstream wasn't asked again


# ── stale-on-error, coalescing ───────────────────────────────────────────────


def test_failure_serves_the_last_good_page_marked_stale(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = {"fail": False}

    async def flaky(path: str, *, params: Any = None):
        if state["fail"]:
            return {"error": "couldn't reach Hugging Face: boom"}, {}
        return [HF_ROW], {}

    monkeypatch.setattr(huggingface_tools, "hub_get", flaky)
    ok = client.get("/api/discover/hf/list", params={"kind": "model"}).json()
    assert ok["stale"] is False and len(ok["items"]) == 1
    state["fail"] = True
    stale = client.get(
        "/api/discover/hf/list", params={"kind": "model", "fresh": "true"}
    ).json()
    assert stale["stale"] is True
    assert stale["status"] == "error"
    assert "boom" in stale["message"]
    assert stale["items"][0]["id"] == "acme/tiny-7b"


def test_concurrent_identical_requests_share_one_load() -> None:
    cache = Cache()
    calls = {"n": 0}

    async def loader() -> str:
        calls["n"] += 1
        await asyncio.sleep(0.01)
        return "v"

    async def both():
        return await asyncio.gather(
            cache.fetch("k", 60, loader), cache.fetch("k", 60, loader)
        )

    results = asyncio.run(both())
    assert [v for _, v in results] == ["v", "v"]
    assert calls["n"] == 1


def test_a_failure_is_not_cached() -> None:
    cache = Cache()

    async def boom() -> str:
        raise SourceUnavailable("x")

    async def ok() -> str:
        return "v"

    async def run():
        with pytest.raises(SourceUnavailable):
            await cache.fetch("k", 60, boom)
        return await cache.fetch("k", 60, ok)

    assert asyncio.run(run())[1] == "v"


# ── Kaggle ───────────────────────────────────────────────────────────────────


def test_kaggle_without_credentials_needs_connect(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.modules.training.providers import kaggle_provider
    from backend.modules.training.providers.base import ProviderError

    def refuse() -> Any:
        raise ProviderError("Kaggle authentication failed")

    monkeypatch.setattr(kaggle_provider, "client", refuse)
    body = client.get(
        "/api/discover/kaggle/list", params={"kind": "competition"}
    ).json()
    assert body["status"] == "needs_connect"
    assert "training.kaggle.username" in body["message"]
    assert body["items"] == []


def test_kaggle_benchmarks_say_why_they_are_empty(client: TestClient) -> None:
    body = client.get("/api/discover/kaggle/list", params={"kind": "benchmark"}).json()
    assert body["status"] == "degraded"
    assert "kaggle.com/benchmarks" in body["message"]


def test_kaggle_competition_item_omits_the_unpopulated_notebook_count() -> None:
    from datetime import datetime
    from types import SimpleNamespace

    comp = SimpleNamespace(
        ref="https://www.kaggle.com/competitions/titanic",
        title="Titanic",
        reward="Knowledge",
        deadline=datetime(2030, 1, 1),
        category="Getting Started",
        team_count=10595,
        kernel_count=0,
        evaluation_metric="Accuracy",
        tags=[SimpleNamespace(name="tabular")],
    )
    item = kaggle.competition_item(comp)
    assert item.id == "titanic"
    assert [m.key for m in item.metrics] == ["teams"]
    # The deadline is not a last-modified date.
    assert item.updated_at is None
    assert item.tags == ["tabular"]


# ── docs ─────────────────────────────────────────────────────────────────────


def test_docs_newest_prefers_bare_slug_then_highest_release() -> None:
    rows = [
        {"slug": "python~3.9", "release": "3.9.14", "mtime": 1},
        {"slug": "python~3.14", "release": "3.14.7", "mtime": 1},
        {"slug": "react~18", "release": "18.3.1", "mtime": 1},
        {"slug": "react", "release": "19.2", "mtime": 1},
    ]
    assert docs.newest(rows, "python")["slug"] == "python~3.14"
    assert docs.newest(rows, "react")["slug"] == "react"
    assert docs.newest(rows, "rust") is None


def test_docs_rank_orders_by_match_quality() -> None:
    entries = [
        {"name": "collections.Counter", "path": "a", "type": "Data Types"},
        {"name": "Counter", "path": "b", "type": "Data Types"},
        {"name": "itertools.count", "path": "c", "type": "Functional"},
        {"name": "Countermeasures", "path": "d", "type": "x"},
    ]
    assert [e["path"] for e in docs.rank(entries, "counter")] == ["b", "d", "a"]


# ── skills ───────────────────────────────────────────────────────────────────


def _fake_skill_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    async def public_get(path: str, *, params: Any = None):
        if path.endswith("/git/trees/main"):
            return {
                "tree": [
                    {"type": "blob", "path": "skills/pdf/SKILL.md", "size": 10},
                    {"type": "blob", "path": "skills/pdf/scripts/fill.py", "size": 5},
                    {"type": "blob", "path": "skills/docx/SKILL.md", "size": 10},
                    {"type": "blob", "path": "README.md", "size": 3},
                ]
            }, {}
        return {"default_branch": "main", "stargazers_count": 7}, {}

    texts = {
        "skills/pdf/SKILL.md": "---\nname: pdf\ndescription: Fill PDFs\n---\nUse scripts/fill.py",
        "skills/pdf/scripts/fill.py": "print('hi')",
        "skills/docx/SKILL.md": "---\nname: docx\ndescription: Word files\n---\nBody",
    }

    class FakeResponse:
        def __init__(self, text: str | None) -> None:
            self.status_code = 200 if text is not None else 404
            self.text = text or ""
            self.content = self.text.encode()

    class FakeClient:
        def __init__(self, **_: Any) -> None: ...

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_: Any) -> None: ...

        async def get(self, url: str) -> FakeResponse:
            rel = url.split("/main/", 1)[1]
            return FakeResponse(texts.get(rel))

    monkeypatch.setattr(github_api, "public_get", public_get)
    monkeypatch.setattr(skills.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(
        skills, "repos", lambda: [{"repo": "acme/skills", "label": "Acme"}]
    )


def test_skills_list_reads_front_matter_and_sibling_files(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_skill_repo(monkeypatch)
    monkeypatch.setattr(skills, "installed_names", lambda: {"docx"})
    body = client.get("/api/discover/skills/list", params={"kind": "skill"}).json()
    by_name = {i["title"]: i for i in body["items"]}
    assert set(by_name) == {"pdf", "docx"}
    assert by_name["pdf"]["description"] == "Fill PDFs"
    assert "+1 files" in [b["label"] for b in by_name["pdf"]["badges"]]
    assert by_name["docx"]["badges"][0]["label"] == "installed"
    assert by_name["pdf"]["metrics"][0]["value"] == 7


def test_skill_install_copies_the_whole_directory(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _fake_skill_repo(monkeypatch)
    from backend.modules.skills import store

    monkeypatch.setattr(store, "user_dir", lambda: tmp_path)
    res = client.post(
        "/api/discover/skills/install", json={"id": "acme/skills:skills/pdf"}
    )
    assert res.status_code == 200, res.text
    assert res.json() == {"name": "pdf", "files": 2}
    assert (tmp_path / "pdf" / "SKILL.md").read_text().startswith("---\nname: pdf")
    assert (tmp_path / "pdf" / "scripts" / "fill.py").read_text() == "print('hi')"
    # A second install would clobber an existing (possibly edited) skill: refused.
    again = client.post(
        "/api/discover/skills/install", json={"id": "acme/skills:skills/pdf"}
    )
    assert again.status_code >= 400
    assert "already exists" in again.json()["detail"]


def test_skill_install_refuses_repos_not_listed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_skill_repo(monkeypatch)
    res = client.post("/api/discover/skills/install", json={"id": "evil/repo:x"})
    assert res.status_code >= 400
    assert "not one of the listed" in res.json()["detail"]


# ── routing ──────────────────────────────────────────────────────────────────


def test_sources_lists_every_source(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_catalog() -> list[dict[str, Any]]:
        raise SourceUnavailable("offline")

    monkeypatch.setattr(docs, "catalog", no_catalog)
    ids = [s["id"] for s in client.get("/api/discover/sources").json()["sources"]]
    assert ids == [
        "hf",
        "papers",
        "arxiv",
        "github",
        "kaggle",
        "mcp",
        "plugins",
        "skills",
        "docs",
    ]


def test_unknown_source_is_404(client: TestClient) -> None:
    assert (
        client.get("/api/discover/nope/list", params={"kind": "x"}).status_code == 404
    )


def test_latest_daily_papers_are_one_day(monkeypatch: pytest.MonkeyPatch) -> None:
    """Undated, the endpoint mixes the newest two days; "latest day" must be one."""

    def row(arxiv_id: str, day: str, upvotes: int) -> dict[str, Any]:
        return {
            "title": arxiv_id,
            "paper": {"id": arxiv_id, "upvotes": upvotes, "submittedOnDailyAt": f"{day}T00:00:00.000Z"},
        }

    async def fake(path: str, *, params: Any = None):
        return [row("a", "2026-10-01", 3), row("b", "2026-09-30", 99), row("c", "2026-10-01", 7)], {}

    monkeypatch.setattr(huggingface_tools, "hub_get", fake)
    result = asyncio.run(papers.PapersSource().list(Query(kind="paper")))
    assert [i.id for i in result.items] == ["c", "a"]
    assert "2026-10-01" in result.feed_label


def test_front_matter_reads_folded_descriptions() -> None:
    from backend.modules.discover.sources.base import front_matter

    text = "---\nname: guide\ndescription: >\n  Teaches the course\n  step by step.\nlicense: MIT\n---\nBody"
    assert front_matter(text) == {
        "name": "guide",
        "description": "Teaches the course step by step.",
        "license": "MIT",
    }

"""The versioned doc index: llms.txt parsing, version resolution, installed-version
awareness, and the store columns that carry a version from a crawl to a search hit.

Pure functions wherever possible, following `test_search.py`. The two impure pieces —
the sqlite columns and the registry parsers — are covered against a temp `app.db` and
against captured response shapes, so nothing here touches the network.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.modules.search.base import SearchResult
from backend.modules.search.crawl import llmstxt
from backend.modules.search.crawl.crawler import _version_changed, in_scope
from backend.modules.search.crawl.versions import (
    PackageRef,
    _canonical_dist,
    _read_version,
    installed_mismatch,
    installed_versions,
    is_prerelease,
    normalize_version,
    parse_package,
    version_series,
)
from backend.modules.search.providers.crawl import demote_mismatches

# --- llms.txt: URL candidates -------------------------------------------------


def test_llms_txt_candidates_try_the_path_before_the_origin():
    """Hugging Face publishes one file per product and nothing at the origin, so a
    root-only probe finds no docs at all for the most important seed there is."""
    full, index = llmstxt.llms_txt_urls(
        "https://huggingface.co/docs/transformers/index"
    )
    assert index == [
        "https://huggingface.co/docs/transformers/llms.txt",
        "https://huggingface.co/llms.txt",
    ]
    assert full[0] == "https://huggingface.co/docs/transformers/llms-full.txt"


def test_llms_txt_candidates_collapse_when_the_start_url_is_the_origin():
    _full, index = llmstxt.llms_txt_urls("https://docs.ollama.com/")
    assert index == ["https://docs.ollama.com/llms.txt"]


# --- llms.txt: the index ------------------------------------------------------

INDEX = """# Transformers

> State-of-the-art machine learning.

## Docs

- [Quicktour](https://example.com/docs/quicktour.md): Get started fast.
- [Installation](/docs/install.md)
- [Quicktour](https://example.com/docs/quicktour.md): duplicate

## Optional

- [Blog](https://example.com/blog/index.md): Not really docs.
- not a link at all
"""


def test_parse_llms_txt_reads_title_summary_sections_and_dedupes():
    index = llmstxt.parse_llms_txt(INDEX, "https://example.com/docs/llms.txt")
    assert index.title == "Transformers"
    assert index.summary == "State-of-the-art machine learning."
    assert [e.url for e in index.entries] == [
        "https://example.com/docs/quicktour.md",
        "https://example.com/docs/install.md",
        "https://example.com/blog/index.md",
    ]
    assert index.entries[0].description == "Get started fast."
    assert index.entries[0].section == "Docs"
    assert index.entries[2].section == "Optional"


def test_parse_llms_txt_resolves_relative_links_against_the_file():
    index = llmstxt.parse_llms_txt(
        "- [x](../other/page.md)", "https://example.com/docs/llms.txt"
    )
    assert index.entries[0].url == "https://example.com/other/page.md"


def test_looks_like_markdown_rejects_the_spa_shell_a_404_returns():
    """Doc sites answer a missing path with a 200 and their own HTML far more often
    than with a 404, so the status code alone cannot decide this."""
    assert llmstxt.looks_like_markdown("# Title\n\nbody")
    assert not llmstxt.looks_like_markdown("<!doctype html><html><head>")
    assert not llmstxt.looks_like_markdown('  <html lang="en">')
    assert not llmstxt.looks_like_markdown("")


# --- llms.txt: the full corpus ------------------------------------------------

CORPUS = """# Get version

Source: https://docs.example.com/api/get-version

Returns the running version. {padding}

# Orphan section

No source line here, so it cannot be attributed. {padding}

# Chat

Source: https://docs.example.com/api/chat

Generate the next message. {padding}
""".replace("{padding}", "x " * 150)


def test_parse_llms_full_keeps_only_attributable_documents():
    docs, total = llmstxt.parse_llms_full(
        CORPUS, "https://docs.example.com/llms-full.txt"
    )
    assert total == 3
    assert [d.url for d in docs] == [
        "https://docs.example.com/api/get-version",
        "https://docs.example.com/api/chat",
    ]
    assert docs[0].title == "Get version"


def test_parse_llms_full_strips_the_source_line_from_the_body():
    """The URL is metadata and is about to become metadata; leaving it inline puts a
    bare link in the middle of an embedded chunk and in the search snippet."""
    docs, _total = llmstxt.parse_llms_full(CORPUS, "https://docs.example.com/x.txt")
    assert "Source:" not in docs[0].text
    assert docs[0].text.startswith("Returns the running version.")


def test_parse_llms_full_ignores_headings_inside_code_fences():
    """A shell session full of `# comment` lines would otherwise shred one page into
    a dozen bodiless documents."""
    body = "x " * 150
    text = (
        "# Real\n\nSource: https://e.com/a\n\n"
        f"{body}\n```bash\n# not a heading\n# also not\n```\n{body}\n"
    )
    docs, total = llmstxt.parse_llms_full(text, "https://e.com/llms-full.txt")
    assert total == 1
    assert "# not a heading" in docs[0].text


def test_parse_llms_full_accepts_a_linked_heading_as_the_source():
    body = "x " * 150
    text = f"# [Chat](https://e.com/chat)\n\n{body}\n"
    docs, _total = llmstxt.parse_llms_full(text, "https://e.com/llms-full.txt")
    assert docs[0].url == "https://e.com/chat"
    assert docs[0].title == "Chat"


def test_usable_as_corpus_rejects_a_corpus_that_names_no_pages():
    """Google's ADK corpus is 229 sections with no source URLs. Indexing it against
    the corpus URL would collapse every hit into one unopenable result, so the
    fallback to the llms.txt index has to fire."""
    assert not llmstxt.usable_as_corpus([], 229)
    doc = llmstxt.LlmsDoc(url="https://e.com/a", title="a", text="t")
    assert not llmstxt.usable_as_corpus([doc], 10)
    assert llmstxt.usable_as_corpus([doc], 2)
    # A corpus of one document is fine; a corpus of one *attributable out of ten* is
    # not, and the difference is the ratio, not the count.
    assert llmstxt.usable_as_corpus([doc], 1)


def test_markdown_article_takes_the_first_heading_as_the_title():
    title, text = llmstxt.markdown_article("# Chat completions\n\nBody here.\n")
    assert title == "Chat completions"
    # Headings stay in the body: a chunk from the middle of a page needs to say
    # which section it came from.
    assert text.startswith("# Chat completions")


# --- publisher-curated links bypass deny patterns -----------------------------


def test_curated_links_ignore_deny_patterns_but_not_the_domain():
    """Hugging Face's own index links pinned `/v5.14.0/` URLs, which the seed's
    `/v[0-9]` deny pattern would reject — discarding all 726 of them."""
    spec = {
        "allow_domains": ["huggingface.co"],
        "allow_patterns": ["^/docs/transformers/"],
        "deny_patterns": ["/v[0-9]"],
    }
    pinned = "https://huggingface.co/docs/transformers/v5.14.0/quicktour.md"
    assert not in_scope(pinned, spec)
    assert in_scope(pinned, spec, apply_deny=False)
    # The domain and allow patterns still hold, or an index could drag the crawl
    # anywhere it liked.
    assert not in_scope("https://evil.com/docs/transformers/x", spec, apply_deny=False)
    assert not in_scope("https://huggingface.co/blog/x", spec, apply_deny=False)


# --- version normalization ----------------------------------------------------


def test_normalize_version_strips_a_release_tag_v():
    assert normalize_version("v0.20.1") == "0.20.1"
    assert normalize_version(" 4.44.2 ") == "4.44.2"
    # Not a version prefix — a name that merely starts with v.
    assert normalize_version("vision-1.0") == "vision-1.0"
    assert normalize_version(None) == ""


def test_version_series_is_major_minor_because_docs_are_written_per_series():
    assert version_series("4.44.0") == version_series("4.44.2") == "4.44"
    assert version_series("5") == "5"
    assert version_series("not-a-version") == ""


def test_is_prerelease():
    assert is_prerelease("1.0.0rc1")
    assert is_prerelease("2.0.0-beta")
    assert not is_prerelease("1.0.0")


# --- registry parsers ---------------------------------------------------------


def test_read_version_pypi():
    assert _read_version("pypi", {"info": {"version": "4.44.2"}}) == "4.44.2"
    assert _read_version("pypi", {"info": None}) is None


def test_read_version_npm():
    assert _read_version("npm", {"version": "1.2.3"}) == "1.2.3"


def test_read_version_github_strips_the_tag_prefix():
    assert _read_version("github", {"tag_name": "v0.6.1"}) == "0.6.1"


def test_read_version_github_refuses_a_draft():
    """A draft release names a tag nobody can install yet, so stamping the index with
    it would claim a version that does not exist."""
    assert _read_version("github", {"tag_name": "v9.0.0", "draft": True}) is None


def test_read_version_survives_a_shape_it_has_never_seen():
    assert _read_version("pypi", ["not", "a", "dict"]) is None
    assert _read_version("npm", {}) is None


def test_parse_package_rejects_what_it_cannot_use():
    assert parse_package({"registry": "pypi", "name": "trl"}) == PackageRef(
        "pypi", "trl"
    )
    assert parse_package({"registry": "cargo", "name": "serde"}) is None
    assert parse_package({"registry": "pypi", "name": ""}) is None
    # A github ref without an owner names no repository.
    assert parse_package({"registry": "github", "name": "ollama"}) is None
    assert parse_package(None) is None
    assert parse_package("transformers") is None


def test_package_dist_name_defaults_to_the_repo_half_of_a_github_ref():
    assert PackageRef("github", "ollama/ollama").dist_name == "ollama"
    assert PackageRef("pypi", "huggingface-hub", dist="huggingface-hub").dist_name == (
        "huggingface-hub"
    )


def test_canonical_dist_follows_pep503():
    assert _canonical_dist("huggingface_hub") == _canonical_dist("Huggingface-Hub")
    assert _canonical_dist("scikit.learn") == "scikit-learn"


# --- installed versions -------------------------------------------------------


def test_installed_versions_finds_a_package_in_the_backend_env():
    """`fastapi` is a hard dependency of this backend, so it is always importable —
    if this ever fails, the dist-info reader is broken, not the fixture."""
    found = installed_versions("fastapi")
    assert "backend" in found
    assert version_series(found["backend"])


def test_installed_versions_is_empty_for_something_not_installed():
    assert installed_versions("definitely-not-a-real-distribution-xyz") == {}


def test_installed_mismatch_is_three_state():
    """Nothing installed is *no signal*, not a mismatch — penalizing docs for a
    package the user hasn't installed yet buries exactly the docs read before
    installing it."""
    assert (
        installed_mismatch("4.44.0", "definitely-not-a-real-distribution-xyz") is None
    )
    assert installed_mismatch(None, "fastapi") is None
    assert installed_mismatch("not-a-version", "fastapi") is None


# --- ranking on the installed version ----------------------------------------


def _hit(url: str, seed: str, version: str | None, score: float) -> SearchResult:
    return SearchResult(
        url=url,
        title=url,
        score=score,
        provider="crawl",
        raw={"seed_id": seed, "version": version},
    )


def test_demote_mismatches_puts_matching_docs_first():
    results = [
        _hit("https://e.com/old", "trl", "0.9.0", 0.80),
        _hit("https://e.com/new", "trl", "1.2.0", 0.75),
    ]
    ranked = demote_mismatches(results, {"trl": "1.2.4"})
    assert [r.url for r in ranked] == ["https://e.com/new", "https://e.com/old"]
    assert ranked[0].raw["installed"] == "1.2.4"
    assert ranked[0].raw["version_mismatch"] is False
    assert ranked[1].raw["version_mismatch"] is True


def test_demote_mismatches_is_a_demotion_not_an_exclusion():
    """Docs for an adjacent release are usually still right; the failure being
    guarded against is a confident wrong answer, not an older page existing."""
    results = [
        _hit("https://e.com/old", "trl", "0.9.0", 0.90),
        _hit("https://e.com/new", "trl", "1.2.0", 0.20),
    ]
    ranked = demote_mismatches(results, {"trl": "1.2.4"})
    assert [r.url for r in ranked] == ["https://e.com/old", "https://e.com/new"]


def test_demote_mismatches_leaves_unversioned_hits_alone():
    results = [
        _hit("https://e.com/a", "python-docs", None, 0.5),
        _hit("https://e.com/b", "", None, 0.4),
    ]
    ranked = demote_mismatches(results, {"trl": "1.2.4"})
    assert [r.url for r in ranked] == ["https://e.com/a", "https://e.com/b"]
    assert all(r.raw["version_mismatch"] is False for r in ranked)


def test_demote_mismatches_breaks_ties_by_the_incoming_order():
    results = [_hit(f"https://e.com/{i}", "s", None, 0.5) for i in range(4)]
    ranked = demote_mismatches(results, {})
    assert [r.url for r in ranked] == [r.url for r in results]


# --- the reindex rule ---------------------------------------------------------


def test_version_change_forces_a_reindex_of_an_unchanged_page():
    """Both skip levels have to lose. Level 2 compares hashes, but a page whose text
    never changed would otherwise keep chunk metadata naming the previous release and
    vanish from a version-filtered search."""
    assert _version_changed({"version": "4.43.0"}, "4.44.1")
    assert _version_changed({"version": None}, "4.44.1")


def test_a_patch_release_does_not_reindex_two_hundred_pages():
    assert not _version_changed({"version": "4.44.0"}, "4.44.2")


def test_an_unversioned_seed_never_restales():
    """A seed with no package resolves no version, and every page it holds must keep
    hitting the cheap skip paths exactly as before."""
    assert not _version_changed({"version": None}, None)
    assert not _version_changed({"version": "4.44.0"}, None)
    assert not _version_changed(None, "4.44.1")


# --- the store columns --------------------------------------------------------


@pytest.fixture()
def crawl_db(tmp_path, monkeypatch):
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    from backend.modules.search.crawl import store

    store.init_crawl_db()
    return store


def test_version_survives_a_round_trip_through_crawl_pages(crawl_db):
    crawl_db.record_page(
        "https://e.com/a", "trl", content_hash="h1", indexed=True, version="1.2.0"
    )
    assert crawl_db.get_page("https://e.com/a", "trl")["version"] == "1.2.0"


def test_an_error_row_cannot_overwrite_the_version_of_indexed_chunks(crawl_db):
    """`version` describes the release the chunks in the vector store were written
    for. A later failure to fetch the page doesn't change what was indexed."""
    crawl_db.record_page(
        "https://e.com/a", "trl", content_hash="h1", indexed=True, version="1.2.0"
    )
    crawl_db.record_page(
        "https://e.com/a", "trl", content_hash="", status="error", error="timed out"
    )
    row = crawl_db.get_page("https://e.com/a", "trl")
    assert row["version"] == "1.2.0"
    assert row["status"] == "error"


def test_seed_version_is_recorded_and_read_back(crawl_db):
    crawl_db.upsert_seed({"id": "trl", "label": "TRL", "start_urls": []})
    crawl_db.set_seed_version("trl", "1.2.4")
    assert crawl_db.get_seed("trl")["version"] == "1.2.4"


def test_the_corpus_row_is_never_a_page(crawl_db):
    """The llms-full.txt row exists to hold an etag. If it entered `known_urls` the
    next run would crawl the corpus file as if it were documentation, and it would
    inflate the seed's page count."""
    crawl_db.record_page(
        "https://e.com/llms-full.txt",
        "ollama",
        content_hash="h",
        status="corpus",
        indexed=True,
    )
    crawl_db.record_page("https://e.com/a", "ollama", content_hash="h1", indexed=True)
    assert crawl_db.known_urls("ollama") == ["https://e.com/a"]
    assert crawl_db.seed_stats("ollama")["pages"] == 1


def test_touching_the_corpus_row_does_not_promote_it_to_a_page(crawl_db):
    """A whole-seed 304 touches this row on every re-crawl — the common path."""
    crawl_db.record_page(
        "https://e.com/llms-full.txt", "ollama", content_hash="h", status="corpus"
    )
    crawl_db.touch_page("https://e.com/llms-full.txt", "ollama", status="corpus")
    assert crawl_db.get_page("https://e.com/llms-full.txt", "ollama")["status"] == (
        "corpus"
    )
    assert crawl_db.known_urls("ollama") == []


def test_the_version_columns_are_added_to_an_existing_install(tmp_path, monkeypatch):
    """`init_crawl_db` is CREATE TABLE IF NOT EXISTS, so a database made before this
    change never sees a new column unless `_ensure_column` adds it."""
    import sqlite3

    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    from backend.modules.database.app_db import ensure_app_db_dir
    from backend.modules.search.crawl import store

    conn = sqlite3.connect(str(ensure_app_db_dir()))
    conn.execute(
        "CREATE TABLE crawl_pages (url TEXT NOT NULL, seed_id TEXT NOT NULL, "
        "title TEXT, etag TEXT, last_modified TEXT, content_hash TEXT NOT NULL, "
        "chunk_count INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'ok', "
        "error TEXT, fetched_at TIMESTAMP, indexed_at TIMESTAMP, "
        "PRIMARY KEY (url, seed_id))"
    )
    conn.commit()
    conn.close()

    store.init_crawl_db()
    store.record_page(
        "https://e.com/a", "trl", content_hash="h", indexed=True, version="1"
    )
    assert store.get_page("https://e.com/a", "trl")["version"] == "1"


# --- the corpus path, end to end ---------------------------------------------


async def _async_true(self, *args, **kwargs):
    return True


async def _async_none(self, *args, **kwargs):
    return None


def _returning(value):
    async def _resolve(_spec):
        return value

    return _resolve


CORPUS_URL = "https://docs.example.com/llms-full.txt"


@pytest.fixture()
def corpus_crawl(crawl_db, monkeypatch):
    """`crawl_seed` with the network, the embedder and the vector store removed.

    Everything stubbed here is I/O the crawl path decides nothing about; what is left
    under test is which ingest path runs, what gets written, and what gets skipped.
    """
    from backend.modules.database import vectorstore
    from backend.modules.search.crawl import crawler
    from backend.modules.search.crawl import index as webindex
    from backend.modules.search.crawl.robots import HostLimiter, RobotsCache

    responses: dict[str, dict] = {}
    written: list[tuple[str, str, dict]] = []

    monkeypatch.setattr(vectorstore, "init_db", lambda *a, **k: None)
    monkeypatch.setattr(webindex, "forget_page", lambda *a, **k: None)
    monkeypatch.setattr(crawler, "_chunk", lambda text: [text])
    monkeypatch.setattr(RobotsCache, "allowed", _async_true)
    monkeypatch.setattr(RobotsCache, "crawl_delay", _async_none)
    monkeypatch.setattr(HostLimiter, "acquire", _async_none)
    monkeypatch.setattr(HostLimiter, "release", lambda self, host: None)

    async def fake_flush(pending):
        written.extend(pending)
        pending.clear()
        return None

    async def fake_fetch(url, conditional, *, max_bytes=0):
        outcome = responses.get(url)
        if outcome is None:
            return {"error": "HTTP 404"}
        if outcome.get("etag") and conditional.get("If-None-Match") == outcome["etag"]:
            return {"not_modified": True, "final_url": url}
        return {"html": outcome["body"], "final_url": url, "etag": outcome.get("etag")}

    monkeypatch.setattr(crawler, "_flush", fake_flush)
    monkeypatch.setattr(crawler, "_fetch", fake_fetch)
    return crawl_db, crawler, responses, written


def _seed(store, **extra):
    store.upsert_seed(
        {
            "id": "ex",
            "label": "Example",
            "start_urls": ["https://docs.example.com/"],
            "allow_domains": ["docs.example.com"],
            **extra,
        }
    )


def test_a_corpus_becomes_one_page_row_per_document(corpus_crawl):
    store, crawler, responses, written = corpus_crawl
    _seed(store)
    responses[CORPUS_URL] = {"body": CORPUS, "etag": '"v1"'}

    stats = asyncio.run(crawler.crawl_seed("ex"))

    assert stats.source == "corpus"
    assert stats.indexed == 2
    assert {row[2]["url"] for row in written} == {
        "https://docs.example.com/api/get-version",
        "https://docs.example.com/api/chat",
    }
    # One fetch, two documents — the whole point of the corpus path.
    assert stats.fetched == 1
    assert store.seed_stats("ex")["pages"] == 2


def test_an_unchanged_corpus_costs_one_conditional_request(corpus_crawl):
    store, crawler, responses, written = corpus_crawl
    _seed(store)
    responses[CORPUS_URL] = {"body": CORPUS, "etag": '"v1"'}
    asyncio.run(crawler.crawl_seed("ex"))
    written.clear()

    stats = asyncio.run(crawler.crawl_seed("ex"))

    assert stats.not_modified == 1
    assert stats.indexed == 0
    assert written == []


def test_a_document_dropped_from_the_corpus_is_forgotten(corpus_crawl):
    """A corpus is the complete list of what the publisher documents, so a page that
    left it is gone rather than merely undiscovered this run."""
    store, crawler, responses, _written = corpus_crawl
    _seed(store)
    responses[CORPUS_URL] = {"body": CORPUS}
    asyncio.run(crawler.crawl_seed("ex"))

    responses[CORPUS_URL] = {"body": CORPUS.split("# Chat")[0]}
    stats = asyncio.run(crawler.crawl_seed("ex"))

    assert stats.skipped == 1
    assert store.known_urls("ex") == ["https://docs.example.com/api/get-version"]


def test_the_index_path_runs_when_the_corpus_names_no_pages(corpus_crawl):
    """The ADK shape: a big llms-full.txt with no source URLs, beside a usable
    llms.txt. Falling through to the index is what makes that site indexable."""
    store, crawler, responses, written = corpus_crawl
    _seed(store)
    body = "x " * 200
    responses[CORPUS_URL] = {"body": f"# A\n\n{body}\n# B\n\n{body}\n"}
    responses["https://docs.example.com/llms.txt"] = {
        "body": "# Example\n\n- [Chat](https://docs.example.com/chat.md): how to chat.\n"
    }
    responses["https://docs.example.com/chat.md"] = {"body": f"# Chat page\n\n{body}"}

    stats = asyncio.run(crawler.crawl_seed("ex"))

    assert stats.source == "index"
    assert [row[2]["url"] for row in written] == ["https://docs.example.com/chat.md"]
    # The publisher's title for the page, not the markdown's own heading.
    assert written[0][2]["title"] == "Chat"


def test_a_seed_can_opt_out_of_llms_txt_entirely(corpus_crawl):
    store, crawler, responses, _written = corpus_crawl
    _seed(store, prefer_llms_txt=False)
    responses[CORPUS_URL] = {"body": CORPUS}

    stats = asyncio.run(crawler.crawl_seed("ex"))

    assert stats.source == "crawl"
    assert stats.indexed == 0


def test_a_version_change_reindexes_a_byte_identical_corpus(corpus_crawl, monkeypatch):
    """The 304 shortcut has to lose to a version change, or every page keeps chunk
    metadata naming the previous release."""
    store, crawler, responses, written = corpus_crawl
    _seed(store, package={"registry": "pypi", "name": "example"})
    responses[CORPUS_URL] = {"body": CORPUS, "etag": '"v1"'}

    monkeypatch.setattr(crawler, "_resolve_version", _returning("1.2.0"))
    asyncio.run(crawler.crawl_seed("ex"))
    written.clear()

    monkeypatch.setattr(crawler, "_resolve_version", _returning("1.3.0"))
    stats = asyncio.run(crawler.crawl_seed("ex"))

    assert stats.not_modified == 0
    assert stats.indexed == 2
    assert {row[2]["version"] for row in written} == {"1.3.0"}
    assert store.get_seed("ex")["version"] == "1.3.0"

"""Tests for the search module's pure surface.

Everything here runs without a network, an API key, or a vector store — the module
is deliberately arranged so the parts most likely to be wrong (URL identity, rank
fusion, each provider's response shape, the crawler's scope rule) are pure functions
against fixtures rather than integration paths.
"""

from __future__ import annotations

from backend.modules.search.canonical import (
    canonical_url,
    host_of,
    registrable_domain,
    same_source,
)
from backend.modules.search.crawl.crawler import content_hash, extract_links, in_scope
from backend.modules.search.fusion import fuse, rrf
from backend.modules.search.pipeline import parse_rewrites
from backend.modules.search.providers import brave, ddg, exa, searxng, serper, tavily
from backend.modules.search.providers.crawl import _group_by_page


# --- canonical --------------------------------------------------------------


def test_canonical_url_normalizes_the_obvious_forks():
    same = {
        canonical_url("https://www.example.com/a/b/"),
        canonical_url("http://example.com/a/b"),
        canonical_url("https://example.com/a/b#section"),
        canonical_url("https://example.com:443/a/b?utm_source=twitter"),
        canonical_url("https://www.example.com/a/b/index.html"),
    }
    assert same == {"https://example.com/a/b"}


def test_canonical_url_keeps_meaningful_query_params_sorted():
    assert (
        canonical_url("https://x.com/s?b=2&a=1&fbclid=zz") == "https://x.com/s?a=1&b=2"
    )


def test_canonical_url_survives_garbage():
    # An unparseable string is still a usable dedupe key; raising here would take
    # down a whole search over one malformed provider result.
    assert canonical_url("not a url") == "not a url"
    assert canonical_url("") == ""


def test_canonical_url_strips_amp_suffixes():
    assert canonical_url("https://news.example.com/story/amp") == (
        "https://news.example.com/story"
    )


def test_registrable_domain_collapses_subdomains():
    assert registrable_domain("https://platform.openai.com/docs") == "openai.com"
    assert registrable_domain("https://openai.com/blog") == "openai.com"
    assert same_source("https://a.openai.com/x", "https://openai.com/y")


def test_registrable_domain_handles_multi_part_suffixes():
    # Without the suffix list both of these would reduce to "co.uk" and count as one
    # source — the one failure mode of the shortcut that isn't conservative.
    assert registrable_domain("https://www.bbc.co.uk/news") == "bbc.co.uk"
    assert registrable_domain("https://www.theguardian.co.uk/x") == "theguardian.co.uk"
    assert not same_source("https://bbc.co.uk/a", "https://theguardian.co.uk/b")


def test_registrable_domain_treats_blog_hosts_as_distinct_publishers():
    assert registrable_domain("https://alice.substack.com/p/x") == "alice.substack.com"
    assert not same_source(
        "https://alice.substack.com/p/x", "https://bob.substack.com/p/y"
    )


def test_host_of_strips_www():
    assert host_of("https://www.Example.COM/path") == "example.com"
    assert host_of("garbage") == ""


# --- fusion -----------------------------------------------------------------


def test_rrf_counts_only_a_key_s_best_rank():
    scores = rrf(["a", "b", "a"])
    assert set(scores) == {"a", "b"}
    assert scores["a"] > scores["b"]


def test_fuse_rewards_agreement_across_lists():
    # `b` is second on both lists; `a` is first on one and absent from the other.
    fused = fuse([["a", "b"], ["c", "b"]])
    assert fused["b"] > fused["a"]
    assert fused["b"] > fused["c"]


def test_fuse_of_nothing_is_empty():
    assert fuse([]) == {}
    assert fuse([[], []]) == {}


# --- provider parsers -------------------------------------------------------


def test_tavily_parser():
    results = tavily.parse_response(
        {
            "results": [
                {
                    "url": "https://a.com/1",
                    "title": "First",
                    "content": "body  text\nhere",
                    "score": 0.83,
                    "published_date": "2026-01-02",
                },
                {"title": "no url", "content": "skipped"},
            ]
        }
    )
    assert len(results) == 1
    assert results[0].url == "https://a.com/1"
    assert results[0].snippet == "body text here"
    assert results[0].score == 0.83
    assert results[0].provider == "tavily"


def test_brave_parser_reads_the_nested_web_results():
    results = brave.parse_response(
        {"web": {"results": [{"url": "https://b.com", "description": "d"}]}}
    )
    assert [r.url for r in results] == ["https://b.com"]
    assert results[0].title == "https://b.com"  # falls back to the URL


def test_brave_parser_ignores_other_verticals():
    assert brave.parse_response({"news": {"results": [{"url": "https://n.com"}]}}) == []


def test_exa_parser_falls_back_through_summary_and_highlights():
    results = exa.parse_response(
        {
            "results": [
                {"url": "https://e.com/1", "title": "T", "highlights": ["hl"]},
                {"url": "https://e.com/2", "title": "T2", "summary": "sum"},
            ]
        }
    )
    assert [r.snippet for r in results] == ["hl", "sum"]


def test_serper_promotes_the_answer_box():
    results = serper.parse_response(
        {
            "answerBox": {"link": "https://ans.com", "title": "A", "snippet": "s"},
            "organic": [{"link": "https://o.com", "title": "O", "snippet": "t"}],
        }
    )
    assert [r.url for r in results] == ["https://ans.com", "https://o.com"]


def test_serper_without_an_answer_box():
    results = serper.parse_response({"organic": [{"link": "https://o.com"}]})
    assert len(results) == 1


def test_searxng_parser_respects_the_limit():
    payload = {"results": [{"url": f"https://s.com/{i}"} for i in range(10)]}
    assert len(searxng.parse_response(payload, limit=3)) == 3


def test_searxng_missing_results_key_explains_the_json_format_gotcha():
    # A JSON-disabled instance answers 200 with HTML, so "no results" would read as
    # "the web has nothing" rather than "fix your settings.yml".
    import pytest

    from backend.modules.search.base import SearchProviderError

    with pytest.raises(SearchProviderError, match="search.formats"):
        searxng.parse_response({"query": "x"})


def test_ddg_scrape_unwraps_the_redirect():
    html = (
        '<a class="result__a" href="/l/?uddg=https%3A%2F%2Freal.com%2Fpage">'
        "Title <b>hi</b></a>"
        '<a class="result__snippet" href="#">snip</a>'
    )
    rows = ddg.parse_ddg_results(html)
    assert rows == [
        {"title": "Title hi", "url": "https://real.com/page", "snippet": "snip"}
    ]


def test_ddg_scrape_returns_nothing_on_drift():
    assert ddg.parse_ddg_results("<html>totally different markup</html>") == []


# --- pipeline ---------------------------------------------------------------


def test_parse_rewrites_always_keeps_the_original_first():
    out = parse_rewrites('["alt one", "alt two"]', query="original", limit=3)
    assert out == ["original", "alt one", "alt two"]


def test_parse_rewrites_degrades_to_the_original_alone():
    assert parse_rewrites("sorry, I can't", query="q", limit=3) == ["q"]
    assert parse_rewrites('{"not": "a list"}', query="q", limit=3) == ["q"]


def test_parse_rewrites_dedupes_and_clamps():
    out = parse_rewrites('["q", "a", "a", "b", "c"]', query="q", limit=3)
    assert out == ["q", "a", "b"]


# --- crawl scope ------------------------------------------------------------


_SPEC = {
    "allow_domains": ["example.com"],
    "allow_patterns": ["^/docs/"],
    "deny_patterns": ["/_modules/"],
}


def test_in_scope_allows_subdomains_but_not_suffix_collisions():
    assert in_scope("https://docs.example.com/docs/a", _SPEC)
    assert not in_scope("https://notexample.com/docs/a", _SPEC)


def test_in_scope_applies_allow_and_deny_patterns():
    assert not in_scope("https://example.com/blog/a", _SPEC)
    assert not in_scope("https://example.com/docs/_modules/x", _SPEC)
    assert in_scope("https://example.com/docs/ok", _SPEC)


def test_in_scope_with_no_rules_accepts_any_host():
    assert in_scope("https://anything.com/x", {})


def test_extract_links_absolutizes_and_drops_non_http():
    html = (
        '<a href="/a">1</a><a href="https://x.com/b">2</a>'
        '<a href="#frag">3</a><a href="mailto:a@b.c">4</a>'
        '<a href="/a#section">5</a>'
    )
    links = extract_links(html, "https://base.com/dir/page")
    assert links == ["https://base.com/a", "https://x.com/b"]


def test_content_hash_ignores_whitespace_churn():
    # Hashing extracted text rather than HTML is what makes the skip path work:
    # markup carries build ids and rotating banners that change every crawl.
    assert content_hash("a  b\nc") == content_hash("a b c")
    assert content_hash("a b") != content_hash("a c")


# --- crawl result grouping --------------------------------------------------


def _row(url: str, score: float, text: str = "chunk"):
    return {"metadata": {"url": url, "title": "T"}, "text": text, "score": score}


def test_group_by_page_keeps_only_each_page_s_best_chunk():
    rows = [
        _row("https://a.com/x", 0.9),
        _row("https://a.com/x", 0.5),
        _row("https://b.com", 0.7),
    ]
    out = _group_by_page(rows, limit=10, site=None)
    assert [r.url for r in out] == ["https://a.com/x", "https://b.com"]
    assert out[0].score == 0.9


def test_group_by_page_filters_by_site():
    rows = [_row("https://a.com/x", 0.9), _row("https://b.com/y", 0.8)]
    assert [r.url for r in _group_by_page(rows, limit=10, site="b.com")] == [
        "https://b.com/y"
    ]


def test_group_by_page_skips_rows_with_no_url():
    assert (
        _group_by_page(
            [{"metadata": {}, "text": "t", "score": 1.0}], limit=5, site=None
        )
        == []
    )

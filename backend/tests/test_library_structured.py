"""Structured ingest: markdown extraction with math kept as TeX, heading-aware
chunking, and the batched, fallback-refusing write path.

The fixtures are trimmed copies of the two real shapes this exists for: a Jekyll
primer with MathJax-2 sources and heading ids (aman.ai), and a Next.js lesson whose
body sits in `div.course-content` with server-rendered KaTeX (apxml.com).
"""

from __future__ import annotations

import asyncio

import pytest

from backend.modules.database.embeddings import get_local_fallback_embedding
from backend.modules.library import store
from backend.modules.library.chunking import SECTION_SEP, chunk_markdown
from backend.modules.library.extract import css_to_xpath, extract_structured
from backend.modules.library.ingest import ingest_source
from backend.modules.library.models import IngestRequest

PRIMER = """<!DOCTYPE html><html><head><title>Primers • RAG</title>
<link rel="canonical" href="https://aman.ai/primers/ai/RAG/"></head><body>
<nav>Home Primers Papers</nav>
<article class="post"><h1 id="rag">Primers • RAG</h1>
<h2 id="overview">Overview</h2>
<p>Retrieval augmented generation grounds a model in documents it retrieves.</p>
<h2 id="fusion">Fusion</h2>
<h3 id="rrf">Reciprocal Rank Fusion (RRF)</h3>
<p>RRF scores a document by
<span class="MathJax_Preview">glyphs</span><span class="MathJax">r(d)</span>
<script type="math/tex">\\sum_i \\frac{1}{k + r_i(d)}</script> across lists.</p>
<script type="math/tex; mode=display">\\text{RRF}(d) = \\sum_{i} \\frac{1}{60 + r_i(d)}</script>
<pre><code>def rrf(ranks):

    return sum(1 / (60 + r) for r in ranks)
</code></pre>
</article></body></html>"""

LESSON = """<html><head><title>Measuring Similarity</title></head><body>
<div class="sidebar"><a href="/x">Chapter 1</a><a href="/y">Chapter 2</a></div>
<div class="m_7485 course-content extra"><h1>Measuring Similarity in Vector Space</h1>
<p>Closeness is measured with similarity metrics.</p>
<h3>Cosine Similarity</h3>
<p>For vectors <span class="katex"><span class="katex-mathml"><math><semantics><mrow>
<mi>a</mi></mrow><annotation encoding="application/x-tex">\\vec{a}</annotation>
</semantics></math></span><span class="katex-html">a⃗</span></span> and b:</p>
<span class="katex-display"><span class="katex"><span class="katex-mathml"><math>
<semantics><annotation encoding="application/x-tex">\\cos\\theta = \\frac{a \\cdot b}{|a||b|}
</annotation></semantics></math></span></span></span>
<ul><li>Range is <code>[-1, 1]</code><ul><li>1 means identical direction</li></ul></li>
<li>Ignores magnitude</li></ul>
</div></body></html>"""


# --- extraction ----------------------------------------------------------------


def test_mathjax2_source_survives_as_tex_and_rendered_spans_are_dropped():
    article = extract_structured(PRIMER, "https://aman.ai/primers/RAG/")
    md = article.markdown
    assert "$\\sum_i \\frac{1}{k + r_i(d)}$" in md
    assert "$$\\text{RRF}(d) = \\sum_{i} \\frac{1}{60 + r_i(d)}$$" in md
    # The rendered glyph spans would otherwise be extracted as noise.
    assert "glyphs" not in md
    assert "## Fusion" in md and "### Reciprocal Rank Fusion (RRF)" in md


MATHJAX3_PAGE = """<html><head><title>Attention</title></head><body>
<article class="post-content">
<h2 id="scaling">Scaling</h2>
<p>Their dot product <mjx-container class="MathJax CtxtMenu_Attached_0" jax="CHTML"
 data-tex="q \\cdot k"><mjx-math><mjx-mi>q</mjx-mi></mjx-math></mjx-container> has
variance <mjx-container class="MathJax CtxtMenu_Attached_0" jax="CHTML"
 data-tex="d_k"><mjx-math>d</mjx-math></mjx-container>, so divide by its root:</p>
<mjx-container class="MathJax CtxtMenu_Attached_0" jax="CHTML" display="true"
 data-tex="\\frac{QK^\\top}{\\sqrt{d_k}}"><mjx-math>QK</mjx-math></mjx-container>
<p>That keeps the softmax out of saturation, as the primer explains in detail.</p>
</article></body></html>"""


def test_mathjax3_containers_keep_their_stamped_tex():
    # Captured exactly like this from aman.ai: MathJax 3 gives every container
    # class="MathJax …", which the MathJax 2 sweep used to delete before the
    # data-tex pass ran — every formula on the site came out as "".
    for selector in (None, "article.post-content"):
        md = extract_structured(
            MATHJAX3_PAGE, "https://aman.ai/primers/ai/attention/", selector
        ).markdown
        assert "$q \\cdot k$" in md, selector
        assert "$d_k$" in md, selector
        assert "$$\\frac{QK^\\top}{\\sqrt{d_k}}$$" in md, selector


def test_canonical_link_and_heading_anchors_are_harvested():
    article = extract_structured(PRIMER, "https://aman.ai/primers/RAG/")
    assert article.canonical == "https://aman.ai/primers/ai/RAG/"
    assert article.anchors["Reciprocal Rank Fusion (RRF)"] == "rrf"
    assert article.anchors["Overview"] == "overview"


def test_content_selector_extracts_katex_lesson_with_nested_lists():
    article = extract_structured(LESSON, "https://apxml.com/l", "div.course-content")
    md = article.markdown
    assert "Chapter 2" not in md  # the sidebar is outside the selected element
    assert "### Cosine Similarity" in md
    assert "$\\vec{a}$" in md
    assert "$$\\cos\\theta = \\frac{a \\cdot b}{|a||b|}$$" in md
    assert "a⃗" not in md  # KaTeX's rendered HTML copy is gone
    assert "- Range is `[-1, 1]`" in md
    assert "  - 1 means identical direction" in md


def test_css_to_xpath_subset_and_loud_failure():
    assert css_to_xpath("#main") == '//*[@id="main"]'
    xp = css_to_xpath("div.course-content p")
    assert xp.startswith("//div[contains(") and xp.endswith("//p")
    with pytest.raises(ValueError):
        css_to_xpath("div > p")
    with pytest.raises(ValueError):
        css_to_xpath("a[href]")


# --- chunking ------------------------------------------------------------------


def test_chunk_markdown_carries_section_path_and_anchor():
    article = extract_structured(PRIMER, "https://aman.ai/primers/RAG/")
    chunks = chunk_markdown(
        article.markdown, title=article.title, size=400, anchors=article.anchors
    )
    rrf = [c for c in chunks if "Reciprocal Rank Fusion" in c.section]
    assert rrf, [c.section for c in chunks]
    assert rrf[0].section == f"Fusion{SECTION_SEP}Reciprocal Rank Fusion (RRF)"
    assert rrf[0].anchor == "rrf"
    # The embedded text leads with the breadcrumb, minus the H1 restating the title.
    assert rrf[0].text.startswith(
        f"Primers • RAG{SECTION_SEP}Fusion{SECTION_SEP}Reciprocal"
    )


def test_chunk_markdown_never_splits_a_code_fence_with_blank_lines():
    md = "# T\n\n## Code\n\n```\nline one\n\nline two\n```\n\nAfter the fence."
    chunks = chunk_markdown(md, title="T", size=30, overlap=0)
    fenced = [c for c in chunks if "```" in c.text]
    assert len(fenced) == 1
    assert "line one\n\nline two" in fenced[0].text


def test_chunk_markdown_merges_small_sections_but_respects_size():
    md = "\n\n".join(f"## S{i}\n\nshort body {i}." for i in range(10))
    chunks = chunk_markdown(md, title="Doc", size=120, overlap=0)
    assert 1 < len(chunks) < 10  # merged, but not into one oversized chunk
    for c in chunks:
        body = c.text.split("\n\n", 1)[1]
        assert len(body) <= 120


def test_chunk_markdown_splits_long_sections_on_sentences_with_word_overlap():
    sentence = "Attention weights sum to one across the keys. "
    md = "## Long\n\n" + sentence * 60
    chunks = chunk_markdown(md, title="Doc", size=300, overlap=60)
    assert len(chunks) > 3
    assert all(c.section == "Long" for c in chunks)
    for c in chunks[1:]:
        body = c.text.split("\n\n", 1)[1]
        assert body[0].isalpha(), body[:20]  # overlap starts on a word


# --- ingest --------------------------------------------------------------------


@pytest.fixture
def batch_embed(monkeypatch):
    calls: list[int] = []

    async def _embed_many(texts: list[str]):
        calls.append(len(texts))
        return [get_local_fallback_embedding(t) for t in texts], "test/embed"

    monkeypatch.setattr("backend.modules.library.ingest.get_embeddings", _embed_many)
    return calls


@pytest.fixture
def count_writes(monkeypatch):
    from backend.modules.library import ingest

    writes: list[int] = []
    real = ingest.replace_documents

    def counting(collection, prefixes, rows):
        writes.append(len(rows))
        return real(collection, prefixes, rows)

    monkeypatch.setattr(ingest, "replace_documents", counting)

    def _single(*_a, **_k):
        raise AssertionError("structured ingest must not write row-by-row")

    monkeypatch.setattr(ingest, "upsert_document", _single)
    return writes


def _note(library: str, text: str, **extra) -> dict:
    source = store.create_source(
        library=library, type="note", title="Doc", url=None, author=None, tags=[]
    )
    req = IngestRequest(type="note", library=library, title="Doc", text=text, **extra)
    asyncio.run(ingest_source(source["id"], req))
    row = store.get_source(source["id"])
    assert row is not None
    return row


def test_ingest_embeds_once_and_writes_once(batch_embed, count_writes):
    md = "\n\n".join(f"## Part {i}\n\n" + "Lorem ipsum dolor. " * 40 for i in range(6))
    row = _note("kb-test-batch", md, structured=True, chunk_size=400)
    assert row["status"] == "ready", row.get("error")
    assert row["chunk_count"] > 6
    assert batch_embed == [row["chunk_count"]]
    assert count_writes == [row["chunk_count"]]


def test_structured_chunks_store_section_metadata(batch_embed):
    from backend.modules.database.vectorstore import list_documents

    row = _note(
        "kb-test-meta",
        "## Alpha\n\nFirst part.\n\n## Beta\n\n" + "Second part. " * 200,
        structured=True,
        chunk_size=500,
    )
    assert row["status"] == "ready"
    docs, _total = list_documents("kb-test-meta", limit=100, offset=0)
    sections = {d["metadata"]["section"] for d in docs}
    assert "Beta" in sections
    assert all(d["metadata"]["embed_model"] == "test/embed" for d in docs)


def test_chunk_size_override_beats_global_setting(batch_embed, monkeypatch):
    monkeypatch.setattr(
        "backend.modules.library.ingest.get_value", lambda key, default=None: 200
    )
    text = "Word " * 600
    small = _note("kb-test-global", text)
    big = _note("kb-test-override", text, chunk_size=2000)
    assert small["chunk_count"] > big["chunk_count"] >= 1


def test_strict_ingest_refuses_hash_fallback(monkeypatch):
    async def _fallback(texts: list[str]):
        return [get_local_fallback_embedding(t) for t in texts], "local-fallback"

    monkeypatch.setattr("backend.modules.library.ingest.get_embeddings", _fallback)
    strict = _note("kb-test-strict", "Some text worth keeping.", strict_embeddings=True)
    assert strict["status"] == "failed"
    assert "hash-fallback" in (strict["error"] or "")

    from backend.modules.database.vectorstore import _get_db

    assert "kb-test-strict" not in _get_db().table_names()

    # Non-strict keeps the old behaviour: a note still saves with no embedder.
    lax = _note("kb-test-lax", "Some text worth keeping.")
    assert lax["status"] == "ready"


# --- ANN index -----------------------------------------------------------------


def test_ensure_ann_index_builds_once_then_reports_current():
    import random

    from backend.modules.database.vectorstore import (
        ensure_ann_index,
        search_documents,
        upsert_documents,
    )

    rng = random.Random(7)
    rows = []
    for i in range(600):
        vec = [rng.uniform(-1, 1) for _ in range(32)]
        rows.append((f"s#{i}", f"text {i}", {"source_id": "s", "chunk_index": i}, vec))
    upsert_documents("kb-ann", rows)

    assert ensure_ann_index("kb-ann", min_rows=10_000) == "skipped"
    assert ensure_ann_index("kb-ann", min_rows=500) == "built"
    assert ensure_ann_index("kb-ann", min_rows=500) == "current"
    # Still answers, and still with cosine scores (the exact row scores ~1).
    hits = search_documents("kb-ann", rows[42][3], 3)
    assert hits and hits[0]["id"] == "s#42"
    assert hits[0]["score"] > 0.95


def test_in_page_table_of_contents_is_dropped():
    toc_items = "".join(f'<li><a href="#s{i}">Section {i}</a></li>' for i in range(8))
    html = (
        f"<html><body><article><h1>Doc</h1><ul>{toc_items}</ul>"
        '<h2 id="s0">Section 0</h2><p>Real body text.</p>'
        '<ul><li><a href="https://x.test/a">A link</a> with prose around it</li>'
        "<li>plain item</li></ul></article></body></html>"
    )
    md = extract_structured(html, "https://x.test/", "article").markdown
    assert "Section 7" not in md  # the TOC list is gone…
    assert "Real body text." in md
    assert "A link with prose around it" in md  # …but an ordinary list survives


def test_commit_batches_several_sources_into_one_write(batch_embed, count_writes):
    import asyncio as _asyncio

    from backend.modules.library.ingest import commit_prepared, prepare_source

    prepared = []
    for i in range(3):
        src = store.create_source(
            library="kb-batch",
            type="note",
            title=f"N{i}",
            url=None,
            author=None,
            tags=[],
        )
        req = IngestRequest(
            type="note", library="kb-batch", title=f"N{i}", text=f"body {i}"
        )
        p = _asyncio.run(prepare_source(src["id"], req))
        assert p is not None
        assert store.get_source(src["id"])["status"] == "embedding"  # not yet written
        prepared.append(p)
    commit_prepared(prepared)
    assert count_writes == [3]
    assert all(store.get_source(p.source_id)["status"] == "ready" for p in prepared)


def test_reingest_replaces_rather_than_duplicates(batch_embed):
    from backend.modules.database.vectorstore import list_documents

    src = store.create_source(
        library="kb-re", type="note", title="R", url=None, author=None, tags=[]
    )
    req = IngestRequest(type="note", library="kb-re", title="R", text="one two three")
    asyncio.run(ingest_source(src["id"], req))
    asyncio.run(ingest_source(src["id"], req))
    _docs, total = list_documents("kb-re", limit=100, offset=0)
    assert total == 1

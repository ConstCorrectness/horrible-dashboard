"""A knowledge library, as a dataset.

The direction the graph was missing. Research files material into a library and the
library embeds it; the dataset sources were hub / local / exports, so a corpus you
had spent an afternoon assembling could be *searched* by the agent and never
*trained on*. The two halves met only through a human copying text between them.

The vector store and the library catalog are both stubbed here: this is a test of
the adapter — what it lists, what it flattens, and when it rewrites the file — not
of LanceDB.
"""

from __future__ import annotations

import json

import pytest

from backend.modules.datasets import sources


@pytest.fixture
def library(tmp_path, monkeypatch):
    """One library, `notes`, with three chunks across two sources."""
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))

    docs = [
        {
            "text": "the first chunk",
            "metadata": {"source_id": "s1", "chunk_index": 0},
        },
        {
            "text": "the second chunk",
            "metadata": {"source_id": "s1", "chunk_index": 1},
        },
        {
            "text": "from another paper",
            "metadata": {"source_id": "s2", "chunk_index": 0},
        },
    ]

    # Attributes on the REAL modules, not `sys.modules` stubs. Replacing
    # `backend.modules.database.vectorstore` wholesale also replaces it for
    # `library.routes`, which imports `CLIP_SUFFIX` from it at import time — the
    # stub then breaks an unrelated module and the failure reads as an import bug.
    # `LibrarySource` does its imports inside the methods, so patching attributes
    # is enough.
    from backend.modules.database import vectorstore
    from backend.modules.library import store as library_store

    def list_documents(collection, limit, offset):
        assert collection == "notes"
        return docs[offset : offset + limit], len(docs)

    def list_libraries():
        return [{"name": "notes", "source_count": 2, "chunk_count": 3}]

    def list_sources(library=None, **_):
        assert library == "notes"
        return [
            {"id": "s1", "title": "A paper", "url": "https://example.com/a"},
            {"id": "s2", "title": "Another", "url": "https://example.com/b"},
        ]

    monkeypatch.setattr(vectorstore, "list_documents", list_documents)
    monkeypatch.setattr(library_store, "list_libraries", list_libraries)
    monkeypatch.setattr(library_store, "list_sources", list_sources)

    return sources.LibrarySource(), docs


def test_search_lists_libraries_with_what_is_in_them(library) -> None:
    source, _ = library
    hits = source.search("", 10)
    assert [h.id for h in hits] == ["notes"]
    # The count is the description. "notes" alone does not tell you whether it is
    # worth training on, and four sources vs four hundred is the whole question.
    assert hits[0].description == "3 chunks from 2 sources"
    # `rows` is how a picker shows size without reading the description.
    assert hits[0].rows == 3


def test_search_filters_by_name(library) -> None:
    source, _ = library
    assert [h.id for h in source.search("not", 10)] == ["notes"]
    assert source.search("nothing-like-this", 10) == []


def test_peek_carries_provenance(library) -> None:
    source, _ = library
    columns, rows = source.peek("notes", "default", "train", 10)
    assert "text" in columns
    # A chunk with no provenance is not reviewable, and being able to go and read
    # where a row came from is the argument for training on your own corpus.
    assert rows[0]["title"] == "A paper"
    assert rows[0]["url"] == "https://example.com/a"
    assert rows[2]["source_id"] == "s2"


def test_locate_materializes_one_row_per_chunk(library, tmp_path) -> None:
    source, docs = library
    path = tmp_path / "datasets" / "library-notes.jsonl"
    assert source.locate("notes") == str(path)

    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == len(docs)
    assert lines[0]["text"] == "the first chunk"


def test_locate_rewrites_only_when_the_counts_disagree(library, tmp_path) -> None:
    source, docs = library
    path = tmp_path / "datasets" / "library-notes.jsonl"
    source.locate("notes")
    before = path.stat().st_mtime_ns

    # Unchanged: `locate` runs on every recipe render, and re-serialising a large
    # library each time would make opening a form feel broken.
    source.locate("notes")
    assert path.stat().st_mtime_ns == before

    # A source was added since. The file is stale and must be rebuilt, or a re-run
    # silently trains on yesterday's corpus.
    docs.append(
        {"text": "newly ingested", "metadata": {"source_id": "s3", "chunk_index": 0}}
    )
    source.locate("notes")
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 4


def test_locate_refuses_an_empty_library(library) -> None:
    source, docs = library
    docs.clear()
    # Writing a zero-row jsonl would hand `load_dataset` a file it reads happily and
    # a training run that fails much later, for a reason nothing here reported.
    with pytest.raises(sources.SourceError, match="no chunks"):
        source.locate("notes")


def test_the_source_is_registered() -> None:
    # A source nothing can reach is the bug this whole change exists to fix.
    assert "library" in sources.all_sources()
    assert sources.get_source("library").local is True


def test_safe_name_survives_a_user_supplied_library_name() -> None:
    assert sources._safe_name("My Notes / 2026") == "My-Notes---2026"
    assert sources._safe_name("../../etc/passwd") == "etc-passwd"
    assert sources._safe_name("///") == "library"

"""symdex: package/docs/schema extraction, the embed-model drift guard, and the
HTTP surface."""

import asyncio
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.modules.symdex import extract_packages as ep
from backend.modules.symdex.extract_docs import extract_docs
from backend.modules.symdex.index import SymdexIndex
from backend.modules.symdex import index as index_mod


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path / "data"))
    yield


def _write_mini_package(root: Path) -> None:
    pkg = root / "mypkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(
        textwrap.dedent(
            '''
            """Top-level doc."""

            def do_thing(a: int, b: str = "x") -> bool:
                """Do the thing.

                Second paragraph that must not be included.
                """
                return True

            def _private() -> None:
                pass

            class Client:
                """An HTTP-ish client."""

                def get(self, url: str) -> str:
                    """Fetch a URL."""
                    return url

                def _hidden(self) -> None:
                    pass
            '''
        )
    )
    tests = pkg / "tests"
    tests.mkdir()
    (tests / "__init__.py").write_text("def test_only(): pass\n")
    private_mod = pkg / "_internal.py"
    private_mod.write_text("def secret(): pass\n")


def test_extract_packages_harvests_signatures_docs_and_skips(tmp_path, monkeypatch):
    site = tmp_path / "site-packages"
    _write_mini_package(site)
    monkeypatch.setattr(ep, "site_packages_for", lambda interp: [site])
    monkeypatch.setattr(ep, "FRAMEWORK_PACKAGES", {"mypkg": "mypkg-dist"})

    harvests = ep.extract_packages("ignored-interpreter")
    assert len(harvests) == 1
    docs = {d.id: d for d in harvests[0].docs}

    fn = docs["pkg:mypkg-dist:mypkg.do_thing"]
    assert "a: int" in fn.text and "-> bool" in fn.text  # unparsed signature
    assert "Do the thing." in fn.doc
    assert "Second paragraph" not in fn.doc  # first paragraph only

    cls = docs["pkg:mypkg-dist:mypkg.Client"]
    assert cls.kind == "class"
    method = docs["pkg:mypkg-dist:mypkg.Client.get"]
    assert method.symbol == "get" and method.module == "Client"

    # Private functions, private modules, and tests/ never surface.
    assert not any(
        "_private" in i or "_internal" in i or "test_only" in i for i in docs
    )

    # The code_symbols projection rows carry the doc snippet.
    rows = {r["symbol"]: r for r in (d.store_row() for d in harvests[0].docs)}
    assert rows["do_thing"]["doc"].startswith("Do the thing.")


def test_extract_docs_chunks_mdx(tmp_path):
    docs_dir = tmp_path / "docs"
    (docs_dir / "modules").mkdir(parents=True)
    (docs_dir / "modules" / "thing.mdx").write_text("# The Thing\n\n" + "word " * 600)
    chunks = extract_docs(docs_dir)
    assert len(chunks) >= 2  # long page split into overlapping chunks
    assert chunks[0].id == "doc:modules/thing.mdx#0"
    assert chunks[0].metadata["title"] == "The Thing"


def _fake_embedder(model: str, dim: int):
    async def fake(text: str):
        return [0.1] * dim, model

    return fake


def test_reindex_and_search_roundtrip(monkeypatch, tmp_path):
    idx = SymdexIndex()
    monkeypatch.setattr(index_mod, "get_embedding", _fake_embedder("ollama/embedA", 4))
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "a.mdx").write_text("# Alpha\nsome text")
    monkeypatch.setattr(index_mod, "extract_docs", lambda: extract_docs(docs_dir))

    result = asyncio.run(idx.reindex(["docs"]))
    assert result["counts"] == {"docs": 1}
    assert result["embed_model"] == "ollama/embedA"

    found = asyncio.run(idx.search("alpha"))
    assert found["status"] == "ok"
    assert found["results"][0]["kind"] == "docs"

    status = idx.status()
    assert status["counts"] == {"docs": 1} and status["embed_model"] == "ollama/embedA"


def test_model_change_forces_full_rebuild(monkeypatch, tmp_path):
    idx = SymdexIndex()
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "a.mdx").write_text("# Alpha\nsome text")
    monkeypatch.setattr(index_mod, "extract_docs", lambda: extract_docs(docs_dir))
    monkeypatch.setattr(index_mod, "get_embedding", _fake_embedder("ollama/embedA", 4))
    asyncio.run(idx.reindex(["docs"]))

    # New model with a NEW dimension: without the guard this would crash with
    # DimensionMismatch; with it, the collection is rebuilt under the new model.
    monkeypatch.setattr(index_mod, "get_embedding", _fake_embedder("ollama/embedB", 8))
    result = asyncio.run(idx.reindex(["docs"]))
    assert result["embed_model"] == "ollama/embedB"
    found = asyncio.run(idx.search("alpha"))
    assert found["status"] == "ok"


def test_fallback_embedder_refused_and_search_degrades(monkeypatch, tmp_path):
    idx = SymdexIndex()
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "a.mdx").write_text("# Alpha\nsome text")
    monkeypatch.setattr(index_mod, "extract_docs", lambda: extract_docs(docs_dir))
    monkeypatch.setattr(index_mod, "get_embedding", _fake_embedder("ollama/embedA", 4))
    asyncio.run(idx.reindex(["docs"]))

    # Provider goes offline: the hash fallback must neither write nor crash.
    monkeypatch.setattr(
        index_mod, "get_embedding", _fake_embedder("local-fallback", 384)
    )
    refused = asyncio.run(idx.reindex(["docs"]))
    assert refused["started"] is False and "offline" in refused["reason"]

    degraded = asyncio.run(idx.search("alpha"))
    assert degraded["status"] == "reindex_needed"
    assert degraded["results"] == []


def test_routes_status_shape(monkeypatch):
    from backend.app import app

    client = TestClient(app)
    status = client.get("/api/symdex/status")
    assert status.status_code == 200
    body = status.json()
    assert {"building", "total", "counts", "embed_model", "reindex_needed"} <= set(body)


def test_reexports_from_dunder_all_are_harvested(tmp_path, monkeypatch):
    """A parse-only harvest can't see C-implemented or re-exported symbols
    (`collections.defaultdict`, `pandas.DataFrame`) — but they're listed in `__all__`,
    and they're exactly the names people import."""
    site = tmp_path / "site-packages"
    pkg = site / "mypkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(
        '"""Doc."""\n'
        "__all__ = ['DataFrame', 'do_thing', '_hidden']\n"
        "def do_thing(a):\n    return a\n"
    )
    monkeypatch.setattr(ep, "site_packages_for", lambda interp: [site])
    monkeypatch.setattr(ep, "FRAMEWORK_PACKAGES", {"mypkg": "mypkg-dist"})

    docs = {d.symbol: d for d in ep.extract_packages("ignored")[0].docs}
    # Re-exported name the AST walk never saw, carrying the right import module.
    assert docs["DataFrame"].imp == "mypkg"
    # A real def is not duplicated by the __all__ pass, and keeps its signature.
    assert docs["do_thing"].kind == "function" and "a" in docs["do_thing"].detail
    assert "_hidden" not in docs


def test_extract_stdlib_harvests_real_modules(tmp_path):
    """The stdlib corpus is the whole point of the editor's offline intellisense —
    harvest it from the running interpreter and check the shape of what lands."""
    import sys

    from backend.modules.symdex import extract_stdlib as es

    harvests = {h.dist: h for h in es.extract_stdlib(sys.executable)}
    assert "pathlib" in harvests and "json" in harvests

    by_symbol = {d.symbol: d for d in harvests["pathlib"].docs}
    assert by_symbol["Path"].imp == "pathlib"
    assert by_symbol["Path"].id.startswith("std:")
    assert by_symbol["Path"].doc  # a real docstring, not an empty snippet

    # Methods carry no import module — you can't `from X import a_method`.
    methods = [d for d in harvests["pathlib"].docs if d.kind == "method"]
    assert methods and all(d.imp == "" for d in methods)

    # The embed subset is a bounded slice of the (much larger) relational corpus.
    assert es.STDLIB_EMBED < set(harvests) | es.STDLIB_EMBED
    assert "pathlib" in es.STDLIB_EMBED

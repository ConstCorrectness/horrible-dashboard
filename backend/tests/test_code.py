"""Tests for the code-intelligence module: tree-sitter symbol extraction, the index
service, and the /api/code routes (which reuse the files module's root boundary)."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.code.index import CodeIndex
from backend.modules.code.semantic import SemanticIndex
from backend.modules.code.ts import extract_symbols

PY = "def foo(x):\n    return x\n\nclass Bar:\n    def method(self):\n        pass\n"

TS = (
    "export function add(a: number, b: number) { return a + b }\n"
    "class Widget { render() {} }\n"
    "export const helper = (n: number) => n * 2\n"
    "interface Shape { area(): number }\n"
    "type Id = string\n"
    "enum Color { Red, Green }\n"
)


def test_extract_python_defs() -> None:
    syms = {s.name: s for s in extract_symbols("python", PY)}
    assert syms["foo"].kind == "function"
    assert syms["foo"].range.start.line == 1  # 1-based lines
    assert syms["Bar"].kind == "class"
    assert syms["method"].container == "Bar"


def test_extract_ts_defs() -> None:
    kinds = {s.name: s.kind for s in extract_symbols("tsx", TS)}
    assert kinds["add"] == "function"
    assert kinds["Widget"] == "class"
    assert kinds["helper"] == "function"  # const arrow function
    assert kinds["render"] == "method"
    assert kinds["Shape"] == "interface"
    assert kinds["Id"] == "type"
    assert kinds["Color"] == "enum"


def test_document_symbols_mtime_cache(tmp_path) -> None:
    f = tmp_path / "m.py"
    f.write_text(PY, encoding="utf-8")
    idx = CodeIndex()
    first = idx.document_symbols(f)
    second = idx.document_symbols(f)
    assert first is second  # unchanged mtime returns the cached list
    assert [s.name for s in first] == ["foo", "Bar", "method"]


def test_find_ranks_and_subsequence(tmp_path) -> None:
    (tmp_path / "m.py").write_text(PY, encoding="utf-8")
    idx = CodeIndex()
    hits = idx.find_symbols("foo", [tmp_path])
    assert hits and hits[0].name == "foo"
    assert any(h.name == "method" for h in idx.find_symbols("mthd", [tmp_path]))


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "sample.py").write_text(PY, encoding="utf-8")
    monkeypatch.setenv("HORRIBLE_WORKSPACE_ROOTS", str(ws))
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path / "data"))
    return TestClient(app)


def test_symbols_route(client: TestClient) -> None:
    res = client.get("/api/code/symbols", params={"path": "sample.py"})
    assert res.status_code == 200
    body = res.json()
    assert body["language"] == "python"
    assert [s["name"] for s in body["symbols"]] == ["foo", "Bar", "method"]


def test_symbols_route_rejects_outside_roots(client: TestClient, tmp_path) -> None:
    outside = tmp_path / "outside.py"
    outside.write_text(PY, encoding="utf-8")
    res = client.get("/api/code/symbols", params={"path": str(outside)})
    assert res.status_code == 403


def test_find_route(client: TestClient) -> None:
    res = client.get("/api/code/find", params={"q": "Bar"})
    assert res.status_code == 200
    hits = res.json()["hits"]
    assert any(h["name"] == "Bar" and h["kind"] == "class" for h in hits)


# --- semantic search (Slice 2) ----------------------------------------------

SEMANTIC_SRC = (
    "def parse_source_file(path):\n"
    "    'Read and tokenize a source file into an AST.'\n"
    "    return path\n"
    "def send_email(to, body):\n"
    "    'Deliver a message over SMTP to a recipient.'\n"
    "    return True\n"
)


def test_semantic_reindex_and_search(tmp_path, monkeypatch) -> None:
    # Offline: get_embedding falls back to a deterministic hash, so ranking is stable.
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path / "data"))
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text(SEMANTIC_SRC, encoding="utf-8")

    idx = SemanticIndex()
    built = asyncio.run(idx.reindex([src]))
    assert built["indexed"] == 2
    assert idx.count() == 2

    res = asyncio.run(
        idx.search("read and tokenize a file into a syntax tree", [src], 5)
    )
    assert res["building"] is False
    names = [h["name"] for h in res["results"]]
    assert names[0] == "parse_source_file"  # ranks above send_email
    assert res["results"][0]["range"]["start"]["line"] == 1  # usable locus


def test_reindex_route(client: TestClient) -> None:
    res = client.post("/api/code/reindex")
    assert res.status_code == 200
    assert res.json()["started"] is True


def test_search_route_reports_building_when_empty(client: TestClient) -> None:
    # A fresh per-test data dir means the `code` collection starts empty → the route
    # auto-kicks a background build and reports it.
    res = client.get("/api/code/search", params={"q": "read a file", "limit": 5})
    assert res.status_code == 200
    body = res.json()
    assert body["building"] is True
    assert body["results"] == []

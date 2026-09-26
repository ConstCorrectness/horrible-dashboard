"""Listing Chroma collections without a `get_collection` round-trip per collection.

Chroma 0.6 changed `list_collections()` from returning Collection objects to
returning bare names; both shapes are in the wild. When the objects come back the
driver uses them directly; when names come back it has to fetch each one. A fake
client stands in for chromadb, which is an optional extra.
"""

from __future__ import annotations

import pytest

from backend.modules.database.drivers import chroma_driver


class _Collection:
    def __init__(self, name: str, n: int, meta: dict | None = None) -> None:
        self.name = name
        self.n = n
        self.meta = meta or {}

    def count(self) -> int:
        return self.n

    def peek(self, limit: int = 1) -> dict:
        return {"metadatas": [self.meta]}


class _Client:
    def __init__(self, *, objects: bool, broken: str | None = None) -> None:
        self.colls = {
            "a": _Collection("a", 3, {"source": "x"}),
            "b": _Collection("b", 5, {"lang": "en", "page": 1}),
        }
        self.objects = objects
        self.broken = broken
        self.fetched: list[str] = []

    def list_collections(self):
        if self.objects:
            return list(self.colls.values())
        return list(self.colls)

    def get_collection(self, name: str):
        self.fetched.append(name)
        if name == self.broken:
            raise RuntimeError("unreadable")
        return self.colls[name]


@pytest.fixture
def client(monkeypatch):
    holder: dict = {}

    def install(**kwargs) -> _Client:
        holder["client"] = _Client(**kwargs)
        monkeypatch.setattr(chroma_driver, "_client", lambda _config: holder["client"])
        return holder["client"]

    return install


def _counts(result) -> dict:
    names = [c.name for c in result.columns]
    rows = [dict(zip(names, row)) for row in result.rows]
    return {r["collection"]: r["count"] for r in rows}


def test_collection_objects_are_counted_without_fetching_each(client):
    fake = client(objects=True)
    result = chroma_driver.run_query({}, '{"op": "collections"}')
    assert _counts(result) == {"a": 3, "b": 5}
    assert fake.fetched == []


def test_bare_names_are_fetched_then_counted(client):
    fake = client(objects=False)
    result = chroma_driver.run_query({}, '{"op": "collections"}')
    assert _counts(result) == {"a": 3, "b": 5}
    assert sorted(fake.fetched) == ["a", "b"]


def test_an_unreadable_collection_still_lists_with_no_count(client):
    client(objects=False, broken="b")
    result = chroma_driver.run_query({}, '{"op": "collections"}')
    assert _counts(result) == {"a": 3, "b": None}


@pytest.mark.parametrize("objects", [True, False])
def test_introspect_reads_metadata_keys_in_both_shapes(client, objects):
    fake = client(objects=objects)
    schema = chroma_driver.introspect({})
    cols = {t.name: [c.name for c in t.columns] for t in schema.tables}
    assert cols == {"a": ["id", "text", "source"], "b": ["id", "text", "lang", "page"]}
    # Introspection runs in threads, so fetch order is not fixed.
    assert sorted(fake.fetched) == ([] if objects else ["a", "b"])

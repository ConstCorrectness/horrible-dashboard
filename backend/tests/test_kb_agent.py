"""The knowledge base from the agent's side: the `kb` tool group, the per-agent
knowledge scope, the system-prompt nudge, and the ML Expert persona."""

from __future__ import annotations

import asyncio

import pytest

from backend.modules.agent.current import agent_context, current_agent_id
from backend.modules.agent.orchestrator import _kb_nudge
from backend.modules.agent.roster import get_agent
from backend.modules.database.embeddings import get_local_fallback_embedding
from backend.modules.database.vectorstore import upsert_documents
from backend.modules.kb import agent_tools, store
from backend.modules.library import store as library_store


def _vec(text: str) -> list[float]:
    return get_local_fallback_embedding(text)


@pytest.fixture(autouse=True)
def fake_embed(monkeypatch):
    async def _embed(text: str):
        return _vec(text), "test/embed"

    monkeypatch.setattr("backend.modules.database.embeddings.get_embedding", _embed)


def _indexed(url: str, section: str, text: str, collections: list[str]) -> dict:
    item = store.create_item(url=url, title=url, collections=collections)
    source = library_store.create_source(
        library="kb-pages", type="blog", title=url, url=url, author=None, tags=[]
    )
    library_store.set_status(source["id"], "ready", chunk_count=1)
    upsert_documents(
        "kb-pages",
        [
            (
                f"{source['id']}#0",
                text,
                {
                    "source_id": source["id"],
                    "chunk_index": 0,
                    "section": section,
                    "anchor": "a",
                    "embed_model": "test/embed",
                    "title": url.rsplit("/", 1)[-1],
                },
                _vec(text),
            )
        ],
    )
    return store.update_item(item["id"], source_id=source["id"], library="kb-pages")


def _tool(name: str):
    return next(t for t in agent_tools._TOOLS if t.name == name)


def test_every_kb_tool_is_grouped_and_writes_are_gated():
    for tool in agent_tools._TOOLS:
        assert tool.group == "kb" and tool.name.startswith("kb.")
    gated = {t.name for t in agent_tools._TOOLS if t.side_effect}
    assert gated == {"kb.save", "kb.setStatus", "kb.import", "kb.saveProfile"}


def test_search_tool_numbers_and_cites_passages():
    _indexed("https://a.test/rag", "Fusion", "reciprocal rank fusion", ["ml"])
    out = asyncio.run(_tool("kb.search").handler({"query": "reciprocal rank fusion"}))
    first = out["passages"][0]
    assert first["n"] == 1
    assert first["cite"] == "[1] rag › Fusion"
    assert first["link"] == "https://a.test/rag#a"


def test_search_tool_reports_an_empty_scope_honestly():
    _indexed("https://a.test/x", "S", "attention", ["web"])
    out = asyncio.run(
        _tool("kb.search").handler({"query": "attention", "collection": "ml"})
    )
    assert out["passages"] == []
    assert "within ml" in out["note"] and "not in the user's material" in out["note"]


def test_agent_scope_defaults_the_collection():
    _indexed("https://ml.test/a", "S", "gradient clipping", ["ml"])
    _indexed("https://web.test/b", "S", "gradient clipping", ["web"])
    search = _tool("kb.search").handler
    everything = asyncio.run(search({"query": "gradient clipping"}))
    assert len(everything["passages"]) == 2
    with agent_context("ml_expert"):
        assert current_agent_id() == "ml_expert"
        scoped = asyncio.run(search({"query": "gradient clipping"}))
    assert scoped["scope"] == ["ml"]
    assert [p["link"] for p in scoped["passages"]] == ["https://ml.test/a#a"]
    assert current_agent_id() == "main"


def test_save_and_status_tools(monkeypatch):
    monkeypatch.setattr(
        "backend.modules.library.routes.enqueue_task", lambda **_k: None
    )
    saved = asyncio.run(
        _tool("kb.save").handler(
            {"url": "https://s.test/post", "collections": ["ml"], "index": "link"}
        )
    )
    assert saved["created"] and saved["saved"]["collections"] == ["ml"]
    item_id = saved["saved"]["id"]
    done = asyncio.run(
        _tool("kb.setStatus").handler({"itemId": item_id, "status": "done"})
    )
    assert done["item"]["status"] == "done"
    bad = asyncio.run(_tool("kb.setStatus").handler({"itemId": item_id, "status": "x"}))
    assert "error" in bad
    rejected = asyncio.run(_tool("kb.save").handler({"url": "javascript:alert(1)"}))
    assert "error" in rejected


def test_nudge_only_when_there_is_something_to_find():
    main = get_agent("main")
    assert _kb_nudge(main, remote=False) == ""  # empty knowledge base
    _indexed("https://n.test/a", "S", "text", ["ml"])
    line = _kb_nudge(main, remote=False)
    assert "knowledge base" in line and "collections: ml" in line
    assert _kb_nudge(main, remote=True) == ""
    coder = get_agent("coder")
    assert _kb_nudge(coder, remote=False) == ""  # can't load kb anyway


def test_ml_expert_is_scoped_to_its_collections():
    spec = get_agent("ml_expert")
    assert spec is not None
    assert spec.preload_groups == ["kb"] and "kb" in spec.tool_groups
    assert spec.knowledge_scope == ["ml"]
    assert "cite" in spec.system_prompt.lower()
    assert "kb" in get_agent("researcher").tool_groups

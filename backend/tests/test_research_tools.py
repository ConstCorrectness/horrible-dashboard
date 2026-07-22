"""Agent awareness: research/arxiv tools registered, grouped by prefix, and the
researcher persona scoped to them."""

from __future__ import annotations

import asyncio

import backend.app  # noqa: F401 — registers every built-in tool
from backend.modules.agent.orchestrator import (
    _GROUP_KEYWORDS,
    _GROUP_DESCRIPTIONS,
    _group_of,
)
from backend.modules.agent.roster import get_agent
from backend.modules.research import agent_tools, runstore
from backend.sdk.registry import registry


def test_backend_tools_registered_and_grouped() -> None:
    # Other suites reset the global registry (test_backend_sdk), so re-register
    # here rather than depend on import-time state; registration is idempotent.
    from backend.modules.arxiv import register_arxiv_tools
    from backend.modules.research import register_research_tools

    register_research_tools()
    register_arxiv_tools()
    expected = {
        "research.start": True,
        "research.status": False,
        "research.report": False,
        "research.capture": True,
        "research.savePdf": True,
        "arxiv.search": False,
        "arxiv.get": False,
        "arxiv.download": True,
    }
    for name, side_effect in expected.items():
        tool = registry.agent_tools.get(name)
        assert tool is not None, f"{name} not registered"
        assert tool.side_effect is side_effect, name
        assert _group_of(name) == name.split(".", 1)[0]


def test_groups_have_blurbs_and_keywords() -> None:
    assert "research" in _GROUP_DESCRIPTIONS
    assert "arxiv" in _GROUP_DESCRIPTIONS
    assert "deep research" in _GROUP_KEYWORDS["research"]
    assert "paper" in _GROUP_KEYWORDS["arxiv"]


def test_researcher_persona_scoped_to_research() -> None:
    spec = get_agent("researcher")
    assert spec is not None
    assert "research" in (spec.tool_groups or [])
    assert "arxiv" in (spec.tool_groups or [])
    assert "research" in spec.preload_groups
    assert "research.start" in spec.system_prompt


def test_start_tool_creates_and_enqueues_run(monkeypatch) -> None:
    enqueued: list[str] = []
    from backend.modules.research.runner import research_runner

    monkeypatch.setattr(research_runner, "enqueue", enqueued.append)
    result = asyncio.run(
        agent_tools._start_run({"query": "what is x", "effort": "quick"})
    )
    assert result["run_id"]
    assert enqueued == [result["run_id"]]
    run = runstore.get_run(result["run_id"])
    assert run is not None and run["effort"] == "quick"

    bad = asyncio.run(agent_tools._start_run({"query": "", "effort": "quick"}))
    assert "error" in bad
    bad = asyncio.run(agent_tools._start_run({"query": "x", "effort": "extreme"}))
    assert "error" in bad


def test_status_and_report_tools(monkeypatch) -> None:
    from backend.modules.artifacts.store import store_bytes

    run = runstore.create_run(query="q", effort="quick")
    artifact = store_bytes(
        b"# Report", kind="report", mime="text/markdown", filename="r.md"
    )
    runstore.update_run(
        run["id"],
        status="done",
        report_artifact_id=artifact["id"],
        report_source_id="src1",
    )

    status = asyncio.run(agent_tools._run_status({"run_id": run["id"]}))
    assert status["runs"][0]["status"] == "done"

    report = asyncio.run(agent_tools._run_report({"run_id": run["id"]}))
    assert report["report"] == "# Report"
    assert report["report_source_id"] == "src1"

    missing = asyncio.run(agent_tools._run_report({"run_id": "0" * 32}))
    assert "error" in missing

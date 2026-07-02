"""Training agent integration: grouped tool disclosure, keyword preloading,
dispatch routing, permission metadata, and the backend tool handlers."""

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest

from backend.modules.agent import orchestrator
from backend.modules.training import agent_tools, projects
from backend.sdk.registry import registry


@pytest.fixture(autouse=True)
def register_training_tools():
    agent_tools.register_agent_tools()
    yield
    # Leave the registry clean between tests (other suites assert on it).
    for tool in agent_tools._TOOLS:
        registry.agent_tools.pop(tool.name, None)


class FakeConn:
    def __init__(self) -> None:
        self.agent_tools: list[dict[str, Any]] = []  # no browser manifest here


# --- grouping & disclosure ----------------------------------------------------


def test_grouped_tools_absent_from_core() -> None:
    core_names = {t["function"]["name"] for t in orchestrator._core_tools()}
    assert not any(n.startswith("training.") for n in core_names)


def test_grouped_tools_in_dynamic_pool() -> None:
    conn = FakeConn()
    dyn = {t["function"]["name"] for t in orchestrator._all_dynamic_tools(conn)}
    assert "training.create_project" in dyn
    assert "training.search_environments" in dyn


def test_training_group_in_catalog() -> None:
    conn = FakeConn()
    catalog = {g["name"]: g for g in orchestrator._group_catalog(conn)}
    assert "training" in catalog
    assert catalog["training"]["tools"] == len(agent_tools._TOOLS)


def test_keyword_preload_on_kaggle_prompt() -> None:
    conn = FakeConn()
    active = orchestrator._preload_groups(
        conn, "i want to work on kaggle's pokemon tcg competition"
    )
    assert "training" in active


def test_no_preload_on_unrelated_prompt() -> None:
    conn = FakeConn()
    active = orchestrator._preload_groups(conn, "what's the weather like")
    assert "training" not in active


def test_tools_for_includes_training_when_preloaded() -> None:
    conn = FakeConn()
    names = {
        t["function"]["name"]
        for t in orchestrator._tools_for(conn, "create a pytorch training project")
    }
    assert "training.create_project" in names


# --- dispatch routing & permission metadata -----------------------------------


def test_dispatch_routes_training_tool_to_registry(monkeypatch) -> None:
    called: dict[str, Any] = {}

    async def fake_invoke(name: str, args: dict[str, Any]) -> Any:
        called["name"] = name
        called["args"] = args
        return {"ok": True}

    monkeypatch.setattr(registry, "invoke_agent_tool", fake_invoke)
    # Bypass the gate for this routing test.
    monkeypatch.setattr(orchestrator, "_gate", _always_allow)

    call = _Call("training.list_projects", {})
    result = asyncio.run(
        orchestrator._dispatch_call(FakeConn(), "turn", call, {"training"})
    )
    assert result == {"ok": True}
    assert called["name"] == "training.list_projects"


def test_side_effect_metadata_surfaces() -> None:
    create = next(t for t in agent_tools._TOOLS if t.name == "training.create_project")
    meta = create.meta()
    assert meta["sideEffect"] is True
    assert meta["specifierTemplate"] == "{provider}:{ref}"
    ro = next(t for t in agent_tools._TOOLS if t.name == "training.list_projects")
    assert ro.meta()["sideEffect"] is False


# --- handlers -----------------------------------------------------------------


@pytest.fixture
def project_env(tmp_path, monkeypatch):
    settings = Path(os.environ["HORRIBLE_DATA_DIR"]) / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(
        json.dumps({"training.projectsRoot": str(tmp_path / "projects")})
    )
    # Never shell out to uv in the create handler.
    from backend.modules.training import routes

    monkeypatch.setattr(routes, "_start_bootstrap", lambda project, reqs: None)
    return tmp_path


def test_create_project_handler(project_env) -> None:
    result = asyncio.run(
        agent_tools._create_project(
            {"provider": "gymnasium", "ref": "CartPole-v1", "kind": "env"}
        )
    )
    assert "projectId" in result
    project = projects.get_project(result["projectId"])
    assert project is not None
    assert (Path(project.root) / "main.ipynb").is_file()


def test_search_environments_handler_offline() -> None:
    # Gymnasium provider needs no network — exercise the real search path.
    result = asyncio.run(
        agent_tools._search_environments({"provider": "gymnasium", "query": "cartpole"})
    )
    ids = [r.get("id") for r in result["results"]]
    assert "CartPole-v1" in ids


def test_project_status_unknown() -> None:
    result = asyncio.run(agent_tools._project_status({"projectId": "nope"}))
    assert result == {"error": "unknown project"}


# --- helpers ------------------------------------------------------------------


class _Call:
    def __init__(self, name: str, arguments: dict[str, Any]) -> None:
        self.name = name
        self.arguments = arguments


async def _always_allow(conn, turn_id, call) -> bool:
    return True

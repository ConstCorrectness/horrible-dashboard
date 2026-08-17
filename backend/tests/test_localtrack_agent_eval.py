"""Evaluation suite for LocalTrack Agent Tool Integration & Multi-Turn Orchestration.

Tests that the agent orchestrator accurately discovers, preloads, and dispatches
multi-turn tool calls against the LocalTrack experiment tracking tools.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
import pytest

from backend.modules.agent import orchestrator
from backend.modules.localtrack import agent_tools, store
from backend.modules.localtrack.models import MetricLogItem
from backend.sdk.registry import registry
from backend.sdk.types import AgentTool


@dataclass
class MockToolCall:
    name: str
    arguments: dict[str, Any]
    call_id: str = "mock-call-id"


class FakeWsConn:
    def __init__(self, approval: dict[str, Any] | None = None) -> None:
        self.sent: list[dict[str, Any]] = []
        self.agent_tools: list[dict[str, Any]] = []
        self.pending_approvals: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self.approval = approval or {"decision": "allow_once"}

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)
        if data.get("event") == "approval_request" and self.approval is not None:
            d = data["data"]
            fut = self.pending_approvals.get(d["approvalId"])
            if fut is not None and not fut.done():
                fut.set_result(self.approval)


@pytest.fixture(autouse=True)
def setup_localtrack_tools(tmp_path, monkeypatch):
    """Set up temporary database and register LocalTrack tools for evaluation."""
    db_path = tmp_path / "localtrack_eval.db"
    monkeypatch.setattr("backend.paths.data_dir", lambda: tmp_path)
    store.init_db()
    agent_tools.register_agent_tools()

    # Seed evaluation dataset (project + runs + metrics)
    proj = store.create_project(project_id="eval-project", name="Evaluation Benchmark Project")
    run1 = store.create_run(
        run_id="run-baseline",
        project_id=proj.id,
        name="baseline-model",
        config={"learning_rate": 1e-4, "batch_size": 32, "epochs": 3},
        tags=["baseline", "eval"],
    )
    run2 = store.create_run(
        run_id="run-finetuned",
        project_id=proj.id,
        name="finetuned-lora",
        config={"learning_rate": 3e-4, "batch_size": 16, "epochs": 3, "lora_rank": 16},
        tags=["lora", "eval"],
    )

    batch: list[MetricLogItem] = []
    for step in range(1, 51):
        loss1 = 3.0 / (1.0 + step * 0.05)
        loss2 = 2.8 / (1.0 + step * 0.08)
        batch.append(MetricLogItem(
            run_id=run1.id,
            step=step,
            metrics={"train/loss": round(loss1, 4), "eval/accuracy": round(0.4 + step * 0.01, 3)},
        ))
        batch.append(MetricLogItem(
            run_id=run2.id,
            step=step,
            metrics={"train/loss": round(loss2, 4), "eval/accuracy": round(0.5 + step * 0.009, 3)},
        ))
    store.ingest_metrics(batch)
    store.save_artifact(run2.id, "eval_config.json", b'{"metric": "accuracy", "top_1": 0.95}')

    yield

    # Clean up registry
    for tool in agent_tools._TOOLS:
        registry.agent_tools.pop(tool.name, None)


# --- 1. Keyword Preload & Catalog Discovery Evaluations ---


def test_eval_preload_localtrack_on_natural_language_prompts() -> None:
    conn = FakeWsConn()
    prompts = [
        "Can you compare the loss curves for my last experiment?",
        "Check my training runs in localtrack",
        "What was the learning rate and hyperparameter config for run-baseline?",
        "Show me the metrics from weights and biases or local experiment tracking",
    ]
    for prompt in prompts:
        active = orchestrator._preload_groups(conn, prompt)
        assert "localtrack" in active, f"Expected 'localtrack' group to preload for prompt: {prompt}"


def test_eval_localtrack_catalog_disclosure() -> None:
    conn = FakeWsConn()
    catalog = {g["name"]: g for g in orchestrator._group_catalog(conn)}
    assert "localtrack" in catalog
    assert catalog["localtrack"]["tools"] == len(agent_tools._TOOLS)
    assert "experiment" in catalog["localtrack"]["description"].lower()


# --- 2. Multi-Turn Orchestration Tool Call Evaluations ---


@pytest.mark.anyio
async def test_eval_multiturn_experiment_inspection_workflow() -> None:
    """Evaluate multi-turn agent interaction inspecting projects, listing runs, and querying metrics."""
    conn = FakeWsConn()
    active_groups: set[str] = set()

    # Turn 1: Discover & load tool group
    load_call = MockToolCall(name="load_tools", arguments={"groups": ["localtrack"]})
    load_res = await orchestrator._dispatch_call(conn, "turn-1", load_call, active_groups)
    assert "localtrack" in active_groups
    assert "localtrack" in load_res["loaded"]

    # Turn 2: Agent calls localtrack.list_projects
    list_proj_call = MockToolCall(name="localtrack.list_projects", arguments={})
    proj_res = await orchestrator._dispatch_call(conn, "turn-2", list_proj_call, active_groups)
    assert "projects" in proj_res
    project_ids = [p["id"] for p in proj_res["projects"]]
    assert "eval-project" in project_ids

    # Turn 3: Agent calls localtrack.list_runs for "eval-project"
    list_runs_call = MockToolCall(name="localtrack.list_runs", arguments={"project_id": "eval-project"})
    runs_res = await orchestrator._dispatch_call(conn, "turn-3", list_runs_call, active_groups)
    assert "runs" in runs_res
    assert len(runs_res["runs"]) == 2
    run_ids = [r["id"] for r in runs_res["runs"]]
    assert "run-baseline" in run_ids
    assert "run-finetuned" in run_ids

    # Turn 4: Agent compares metrics across runs with downsampling
    query_call = MockToolCall(
        name="localtrack.query_metrics",
        arguments={
            "run_ids": ["run-baseline", "run-finetuned"],
            "keys": ["train/loss", "eval/accuracy"],
            "max_points": 20,
            "smoothing": 0.2,
        },
    )
    query_res = await orchestrator._dispatch_call(conn, "turn-4", query_call, active_groups)
    assert "series" in query_res
    assert len(query_res["series"]) == 4  # 2 runs * 2 keys
    for s in query_res["series"]:
        assert len(s["values"]) <= 20
        assert len(s["steps"]) == len(s["values"])
        assert s["key"] in ("train/loss", "eval/accuracy")


@pytest.mark.anyio
async def test_eval_multiturn_project_creation_and_artifact_inspection() -> None:
    """Evaluate multi-turn agent interaction creating projects and retrieving run details."""
    conn = FakeWsConn()
    active_groups: set[str] = {"localtrack"}

    # Turn 1: Create a new project
    create_proj_call = MockToolCall(
        name="localtrack.create_project",
        arguments={"name": "Reinforcement Learning Lab", "description": "PPO experiments"},
    )
    create_res = await orchestrator._dispatch_call(conn, "turn-1", create_proj_call, active_groups)
    assert create_res["status"] == "created"
    assert create_res["project"]["name"] == "Reinforcement Learning Lab"

    # Turn 2: Inspect specific run details and artifacts
    get_run_call = MockToolCall(name="localtrack.get_run", arguments={"run_id": "run-finetuned"})
    run_res = await orchestrator._dispatch_call(conn, "turn-2", get_run_call, active_groups)
    assert "run" in run_res
    assert run_res["run"]["id"] == "run-finetuned"
    assert run_res["run"]["config"]["lora_rank"] == 16
    assert len(run_res["artifacts"]) == 1
    assert run_res["artifacts"][0]["filename"] == "eval_config.json"

    # Turn 3: Discover metric keys
    keys_call = MockToolCall(name="localtrack.get_metric_keys", arguments={"project_id": "eval-project"})
    keys_res = await orchestrator._dispatch_call(conn, "turn-3", keys_call, active_groups)
    assert "keys" in keys_res
    assert "train/loss" in keys_res["keys"]
    assert "eval/accuracy" in keys_res["keys"]

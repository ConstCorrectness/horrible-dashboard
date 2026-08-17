"""Agent tools for the LocalTrack experiment tracking module.

Provides the orchestrator agent with direct capabilities to inspect projects,
compare runs, query loss/accuracy metrics, analyze hyperparameters, and manage
local experiment tracking.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.modules.localtrack import store
from backend.sdk.registry import registry
from backend.sdk.types import AgentTool

logger = logging.getLogger(__name__)


async def _list_projects(_args: dict[str, Any]) -> dict[str, Any]:
    projs = store.list_projects()
    return {
        "projects": [
            {
                "id": p.id,
                "name": p.name,
                "description": p.description,
                "runCount": p.run_count,
                "lastRunAt": p.last_run_at,
            }
            for p in projs
        ]
    }


async def _create_project(args: dict[str, Any]) -> dict[str, Any]:
    name = str(args.get("name") or "").strip()
    if not name:
        return {"error": "Project name is required"}
    desc = str(args.get("description") or "")
    pid = str(args.get("id") or "") or None
    proj = store.create_project(project_id=pid, name=name, description=desc)
    return {"status": "created", "project": proj.model_dump()}


async def _list_runs(args: dict[str, Any]) -> dict[str, Any]:
    project_id = str(args.get("project_id") or "") or None
    runs = store.list_runs(project_id=project_id)
    return {
        "runs": [
            {
                "id": r.id,
                "name": r.name,
                "project_id": r.project_id,
                "status": r.status,
                "duration_seconds": r.duration_seconds,
                "summary": r.summary,
                "tags": r.tags,
                "start_time": r.start_time,
            }
            for r in runs
        ]
    }


async def _get_run(args: dict[str, Any]) -> dict[str, Any]:
    run_id = str(args.get("run_id") or "").strip()
    if not run_id:
        return {"error": "run_id is required"}
    run = store.get_run(run_id)
    if not run:
        return {"error": f"Run '{run_id}' not found"}
    artifacts = store.list_artifacts(run_id)
    return {
        "run": run.model_dump(),
        "artifacts": [a.model_dump() for a in artifacts],
    }


async def _query_metrics(args: dict[str, Any]) -> dict[str, Any]:
    run_ids = args.get("run_ids") or []
    if isinstance(run_ids, str):
        run_ids = [r.strip() for r in run_ids.split(",") if r.strip()]
    keys = args.get("keys") or []
    if isinstance(keys, str):
        keys = [k.strip() for k in keys.split(",") if k.strip()]
    max_points = int(args.get("max_points") or 100)
    smoothing = float(args.get("smoothing") or 0.0)

    if not run_ids or not keys:
        return {"error": "Both 'run_ids' and 'keys' are required"}

    series = store.query_metrics(
        run_ids=run_ids,
        keys=keys,
        max_points=max_points,
        smoothing=smoothing,
    )
    return {
        "series": [s.model_dump() for s in series]
    }


async def _get_metric_keys(args: dict[str, Any]) -> dict[str, Any]:
    project_id = str(args.get("project_id") or "") or None
    run_ids = args.get("run_ids")
    if isinstance(run_ids, str):
        run_ids = [r.strip() for r in run_ids.split(",") if r.strip()]
    keys = store.get_metric_keys(run_ids=run_ids, project_id=project_id)
    return {"keys": keys}


_TOOLS: list[AgentTool] = [
    AgentTool(
        name="localtrack.list_projects",
        description="List all local experiment tracking projects, with run counts and activity.",
        handler=_list_projects,
        parameters={},
        required=[],
        group="localtrack",
    ),
    AgentTool(
        name="localtrack.create_project",
        description="Create a new LocalTrack experiment project.",
        handler=_create_project,
        parameters={
            "name": {"type": "string", "description": "Human-readable name of the project"},
            "description": {"type": "string", "description": "Optional description of the project"},
            "id": {"type": "string", "description": "Optional slug identifier for the project"},
        },
        required=["name"],
        side_effect=True,
        specifier_template="create project {name}",
        group="localtrack",
    ),
    AgentTool(
        name="localtrack.list_runs",
        description="List experiment runs in a project, including status, tags, and summary metrics.",
        handler=_list_runs,
        parameters={
            "project_id": {"type": "string", "description": "Optional project ID to filter runs"},
        },
        required=[],
        group="localtrack",
    ),
    AgentTool(
        name="localtrack.get_run",
        description="Get full configuration, hyperparameters, summary metrics, and artifacts of an experiment run.",
        handler=_get_run,
        parameters={
            "run_id": {"type": "string", "description": "Unique ID of the run"},
        },
        required=["run_id"],
        group="localtrack",
    ),
    AgentTool(
        name="localtrack.query_metrics",
        description="Fetch time-series loss, accuracy, learning rate, and other metrics across runs with downsampling for comparison.",
        handler=_query_metrics,
        parameters={
            "run_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of run IDs to query metrics for",
            },
            "keys": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of metric keys to fetch (e.g. ['train/loss', 'eval/accuracy'])",
            },
            "max_points": {
                "type": "integer",
                "description": "Maximum data points per series (default 100)",
            },
            "smoothing": {
                "type": "number",
                "description": "Exponential moving average smoothing weight (0.0 to 0.99)",
            },
        },
        required=["run_ids", "keys"],
        group="localtrack",
    ),
    AgentTool(
        name="localtrack.get_metric_keys",
        description="Discover all distinct logged metric keys (e.g. train/loss, eval/accuracy) for a project or runs.",
        handler=_get_metric_keys,
        parameters={
            "project_id": {"type": "string", "description": "Optional project ID"},
            "run_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of run IDs",
            },
        },
        required=[],
        group="localtrack",
    ),
]


def register_agent_tools() -> None:
    """Insert the LocalTrack backend tools into the sdk registry (called from app.py)."""
    for tool in _TOOLS:
        registry.agent_tools[tool.name] = tool

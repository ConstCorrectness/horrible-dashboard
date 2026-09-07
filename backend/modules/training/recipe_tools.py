"""The recipe and sweep half of the `training` agent tool group.

The gap this closes: `agent_tools.py` had fourteen tools and not one of them
touched a recipe. The agent could search an environment, create a project, install
dependencies, start a script and convert a checkpoint — but the thing in the middle
that decides what the run actually *is* was reachable only by a human filling in a
form. "Fine-tune Qwen on my eval failures with a learning-rate ablation" ended at
"open the recipe pane".

A separate module rather than another 400 lines in `agent_tools.py`, and the tools
are registered from there so the group stays one group. Nothing here is a new
capability: every function is the same call the HTTP route makes.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.modules.training import envs, projects, recipes, sweeps
from backend.modules.training.models import ProjectModel
from backend.modules.training.stream import broadcast_threadsafe
from backend.sdk.types import AgentTool

logger = logging.getLogger(__name__)

_PROJECT = {
    "projectId": {"type": "string", "description": "The training project's id."}
}


def _project(args: dict[str, Any]) -> ProjectModel | None:
    return projects.get_project(str(args.get("projectId", "")))


def _missing(args: dict[str, Any]) -> dict[str, Any]:
    return {"error": f"no project {args.get('projectId')!r}"}


async def _get_recipe(args: dict[str, Any]) -> Any:
    project = _project(args)
    if project is None:
        return _missing(args)
    recipe = await asyncio.to_thread(recipes.load_recipe, project)
    intro = await asyncio.to_thread(
        recipes.introspect, project, backend_id=recipe.backend, task=recipe.task
    )
    return {
        "recipe": recipe.to_dict(),
        "fields": [
            f.to_dict()
            for f in recipes.catalog(recipe.backend, recipe.task, recipe.use_lora)
        ],
        "introspection": intro.to_dict(),
        "backends": recipes.backends(),
        "tasks": recipes.tasks(),
        "warnings": recipes.warnings_for(recipe.values, recipe.trackers),
    }


_SCALARS = {
    "backend": "backend",
    "task": "task",
    "base_model": "baseModel",
    "dataset": "dataset",
    "dataset_id": "datasetId",
    "dataset_split": "datasetSplit",
    "output_dir": "outputDir",
    "text_field": "textField",
}


async def _set_recipe(args: dict[str, Any]) -> Any:
    """Merge changes into the recipe.

    Merge, not replace: an agent setting one field must not silently reset the
    other thirty-nine to their defaults, which is what a whole-object PUT from a
    model that only remembered the field it cared about would do.
    """
    project = _project(args)
    if project is None:
        return _missing(args)
    recipe = await asyncio.to_thread(recipes.load_recipe, project)
    data = recipe.to_dict()
    for arg_name, key in _SCALARS.items():
        if args.get(arg_name) is not None:
            data[key] = args[arg_name]
    if args.get("use_lora") is not None:
        data["useLora"] = bool(args["use_lora"])
    if isinstance(args.get("trackers"), list):
        data["trackers"] = [str(t) for t in args["trackers"]]
    values = args.get("values") if isinstance(args.get("values"), dict) else {}
    if values:
        data["values"] = {**data.get("values", {}), **values}

    updated = recipes.Recipe.from_dict(data)
    known = {f.name for f in recipes.catalog(updated.backend, updated.task, True)}
    ignored = sorted(set(values) - known)
    await asyncio.to_thread(recipes.save_recipe, project, updated)

    result: dict[str, Any] = {"recipe": updated.to_dict()}
    if ignored:
        # Named rather than dropped silently. A value the catalog does not carry is
        # never emitted, and an agent that set it would otherwise report success
        # for a knob that will not reach the trainer.
        result["ignored"] = ignored
        result["note"] = (
            f"{', '.join(ignored)} are not fields of {updated.backend}/"
            f"{updated.task}, so they were not saved and will not be emitted."
        )
    return result


async def _apply_recipe(args: dict[str, Any]) -> Any:
    project = _project(args)
    if project is None:
        return _missing(args)
    recipe = await asyncio.to_thread(recipes.load_recipe, project)

    def work() -> int:
        intro = recipes.introspect(project, backend_id=recipe.backend, task=recipe.task)
        return recipes.apply_to_notebook(project, recipe, intro)

    try:
        written = await asyncio.to_thread(work)
    except (OSError, ValueError) as exc:
        return {"error": str(exc)}
    return {"cells": written, "notebook": "main.ipynb"}


async def _install_stack(args: dict[str, Any]) -> Any:
    project = _project(args)
    if project is None:
        return _missing(args)
    from backend.modules.hardware import probe as hardware
    from backend.modules.training.backends.base import get_backend

    recipe = await asyncio.to_thread(recipes.load_recipe, project)
    try:
        backend = get_backend(recipe.backend)
    except ValueError as exc:
        return {"error": str(exc)}
    profile = await asyncio.to_thread(hardware.get_profile)
    packages = backend.requirements(recipe.task, profile)

    def progress(line: str) -> None:
        broadcast_threadsafe("env_progress", {"projectId": project.id, "line": line})

    try:
        reason = await asyncio.to_thread(
            envs.install_stack, project, packages, profile, progress
        )
    except Exception as exc:  # noqa: BLE001 — an install failure is a result to read
        return {"error": str(exc)}
    intro = await asyncio.to_thread(
        recipes.introspect,
        project,
        refresh=True,
        backend_id=recipe.backend,
        task=recipe.task,
    )
    return {"installed": packages, "torch": reason, "validated": intro.available}


def _spec_from(args: dict[str, Any]) -> sweeps.SweepSpec:
    return sweeps.SweepSpec.from_dict(
        {
            "axes": args.get("axes") or [],
            "strategy": args.get("strategy") or "grid",
            "maxParallel": args.get("maxParallel") or 1,
            "count": args.get("count") or 8,
        }
    )


async def _sweep(args: dict[str, Any]) -> Any:
    project = _project(args)
    if project is None:
        return _missing(args)
    spec = _spec_from(args)
    recipe = await asyncio.to_thread(recipes.load_recipe, project)
    problems = sweeps.validate(recipe, spec)
    if problems:
        return {"error": "; ".join(problems), "problems": problems}
    try:
        points = sweeps.expand(recipe, spec)
    except sweeps.SweepError as exc:
        return {"error": str(exc)}
    if args.get("preview"):
        return {"points": [p.to_dict() for p in points]}
    if not envs.venv_ready(project):
        return {
            "error": "this project's venv is not ready — run training.install_stack first"
        }
    intro = await asyncio.to_thread(
        recipes.introspect, project, backend_id=recipe.backend, task=recipe.task
    )
    await asyncio.to_thread(sweeps.save_spec, project, spec)
    try:
        sweep_id = sweeps.start(project, recipe, spec, intro)
    except sweeps.SweepError as exc:
        return {"error": str(exc)}
    return {
        "sweepId": sweep_id,
        "points": len(points),
        "status": "started",
        "note": "Poll training.sweep_status, then localtrack.compare_runs on the "
        "metric run ids to see which axis moved the metric.",
    }


async def _sweep_status(args: dict[str, Any]) -> Any:
    return {"sweeps": sweeps.status(str(args.get("sweepId") or ""))}


async def _sweep_stop(args: dict[str, Any]) -> Any:
    return {"stopped": sweeps.stop(str(args.get("sweepId") or ""))}



# --- Weights & Biases, read back ---------------------------------------------
#
# These are `training.*` and not `trackers.*` on purpose. The `trackers` connector
# contributes no tools and `test_connectors.py` enforces that its id names no tool
# group — the orchestrator groups by name prefix, so a `trackers.wandb_import`
# would silently split the connector's tools off from its blurb and guide.


async def _wandb_projects(args: dict[str, Any]) -> Any:
    from backend.modules.training import wandb_sync

    try:
        return {
            "projects": await asyncio.to_thread(
                wandb_sync.list_projects, str(args.get("entity") or "")
            )
        }
    except wandb_sync.WandbError as exc:
        return {"error": str(exc)}


async def _wandb_runs(args: dict[str, Any]) -> Any:
    from backend.modules.training import wandb_sync

    project = str(args.get("project") or "")
    if not project:
        return {"error": "a W&B project is required (entity/project, or just project)"}
    try:
        return {
            "runs": await asyncio.to_thread(
                wandb_sync.list_runs, project, int(args.get("limit") or 25)
            )
        }
    except wandb_sync.WandbError as exc:
        return {"error": str(exc)}


async def _wandb_import(args: dict[str, Any]) -> Any:
    from backend.modules.training import wandb_sync

    path = str(args.get("run") or "")
    if not path:
        return {"error": "a W&B run path is required (entity/project/run_id)"}
    try:
        return await asyncio.to_thread(
            wandb_sync.import_run,
            path,
            project=str(args.get("localtrack_project") or "wandb"),
            exact=bool(args.get("exact")),
        )
    except wandb_sync.WandbError as exc:
        return {"error": str(exc)}


TOOLS: list[AgentTool] = [
    AgentTool(
        name="training.get_recipe",
        description="Read a project's fine-tuning recipe: the chosen framework and "
        "task, every knob and its value, and whether the project venv has validated "
        "them against the installed libraries.",
        parameters=dict(_PROJECT),
        required=["projectId"],
        handler=_get_recipe,
        group="training",
    ),
    AgentTool(
        name="training.set_recipe",
        description="Change a project's fine-tuning recipe. Merges: fields you omit "
        "keep their values. Check the dataset suits the task with datasets.adapt "
        "first — a preference dataset cannot train SFT and vice versa.",
        parameters={
            **_PROJECT,
            "backend": {
                "type": "string",
                "description": "trl | unsloth | torchtitan | nanotron",
            },
            "task": {
                "type": "string",
                "description": "sft | cpt | dpo | kto | reward | grpo | pretrain",
            },
            "base_model": {"type": "string", "description": "Hub id to fine-tune"},
            "dataset_id": {
                "type": "string",
                "description": "A registered dataset's id. Preferred over `dataset`: "
                "it carries the detected shape and column map, so the generated code "
                "reshapes correctly instead of guessing a text column.",
            },
            "dataset": {"type": "string", "description": "Hub id or path, free text"},
            "dataset_split": {"type": "string", "description": "Split, default train"},
            "use_lora": {"type": "boolean", "description": "Train a LoRA adapter"},
            "output_dir": {"type": "string", "description": "Where checkpoints land"},
            "trackers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "none | tensorboard | wandb | mlflow | trackio",
            },
            "values": {
                "type": "object",
                "description": "Knob values keyed by field name, e.g. "
                "{'learning_rate': 0.0002, 'r': 16}. training.get_recipe returns the "
                "field list for the selected framework and task.",
            },
        },
        required=["projectId"],
        side_effect=True,
        specifier_template="{projectId}",
        handler=_set_recipe,
        group="training",
    ),
    AgentTool(
        name="training.apply_recipe",
        description="Write the recipe's generated cells into the project notebook, "
        "replacing any previously generated block.",
        parameters=dict(_PROJECT),
        required=["projectId"],
        side_effect=True,
        specifier_template="{projectId}",
        handler=_apply_recipe,
        group="training",
    ),
    AgentTool(
        name="training.install_stack",
        description="Install the recipe framework's libraries (trl/peft/unsloth/...) "
        "into the project venv, picking the torch wheel that matches this machine's "
        "accelerator. Required before a recipe can be validated or run.",
        parameters=dict(_PROJECT),
        required=["projectId"],
        side_effect=True,
        specifier_template="{projectId}",
        handler=_install_stack,
        group="training",
    ),
    AgentTool(
        name="training.sweep",
        description="Run an ablation: the project's recipe N times with one or more "
        "fields varied. Every run records the config it exercised, so "
        "localtrack.compare_runs can attribute the difference to an axis. Pass "
        "preview=true to see the points without running them.",
        parameters={
            **_PROJECT,
            "axes": {
                "type": "array",
                "items": {"type": "object"},
                "description": "[{field, values: [...]}], e.g. [{'field': "
                "'learning_rate', 'values': [0.0001, 0.0002, 0.0005]}]",
            },
            "strategy": {
                "type": "string",
                "description": "grid (every combination) | zip (axes step together) | "
                "random (a seeded sample of the grid)",
            },
            "count": {
                "type": "integer",
                "description": "random strategy only: how many points to draw",
            },
            "maxParallel": {
                "type": "integer",
                "description": "Concurrent runs, default 1. Two fine-tunes on one GPU "
                "is an out-of-memory error, not double the throughput.",
            },
            "preview": {
                "type": "boolean",
                "description": "Return the points instead of running them",
            },
        },
        required=["projectId", "axes"],
        side_effect=True,
        specifier_template="{projectId}",
        handler=_sweep,
        group="training",
    ),
    AgentTool(
        name="training.sweep_status",
        description="Progress of ablation sweeps: which points are running, which "
        "finished or failed, and the metric run ids to compare.",
        parameters={"sweepId": {"type": "string", "description": "One sweep's id"}},
        required=[],
        handler=_sweep_status,
        group="training",
    ),
    AgentTool(
        name="training.sweep_stop",
        description="Stop a running sweep. Points that already finished keep their "
        "results.",
        parameters={"sweepId": {"type": "string", "description": "The sweep's id"}},
        required=["sweepId"],
        side_effect=True,
        specifier_template="{sweepId}",
        handler=_sweep_stop,
        group="training",
    ),
    AgentTool(
        name="training.wandb_projects",
        description="List the Weights & Biases projects this node's connected key "
        "can see.",
        parameters={
            "entity": {
                "type": "string",
                "description": "W&B entity (team or user). Defaults to the key's own.",
            }
        },
        required=[],
        handler=_wandb_projects,
        group="training",
    ),
    AgentTool(
        name="training.wandb_runs",
        description="List runs in a Weights & Biases project, with their configs "
        "and summary metrics.",
        parameters={
            "project": {
                "type": "string",
                "description": "`entity/project`, or just `project` for your own",
            },
            "limit": {"type": "integer", "description": "Max runs (default 25)"},
        },
        required=["project"],
        handler=_wandb_runs,
        group="training",
    ),
    AgentTool(
        name="training.wandb_import",
        description="Import a Weights & Biases run's config and metric history into "
        "localtrack, so a run from another machine charts and compares beside the "
        "ones trained here. Re-importing updates the same row.",
        parameters={
            "run": {
                "type": "string",
                "description": "Full run path: entity/project/run_id",
            },
            "localtrack_project": {
                "type": "string",
                "description": "Which localtrack project to import into (default `wandb`)",
            },
            "exact": {
                "type": "boolean",
                "description": "Pull every logged step instead of W&B's sampled "
                "history. Much slower; the default sample is reported either way.",
            },
        },
        required=["run"],
        side_effect=True,
        specifier_template="{run}",
        handler=_wandb_import,
        group="training",
    ),
]

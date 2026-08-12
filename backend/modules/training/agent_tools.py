"""Backend agent tools for the training module (group ``training``).

These are server-resolved (they run with no browser tab needed), registered into
the backend-sdk registry so the orchestrator discloses them under the ``training``
group (keyword-preloaded on prompts like "work on kaggle's pokemon tcg
competition"). They cover the whole project lifecycle — search → create → fetch →
install → run → push → render — so the agent can drive the flagship flow end to
end and then arrange panes with the existing layout verbs.

Notebook *cell* editing is a separate `notebook` group of **frontend** tools on
the notebook panel (see packages/core/.../training/index.ts) since the cells live
in the pane's store.
"""

from __future__ import annotations

import asyncio
from typing import Any

from backend.modules.training import envs, notebooks, projects
from backend.modules.training.providers import (
    ProviderError,
    get_provider,
    list_providers,
)
from backend.sdk.registry import registry
from backend.sdk.types import AgentTool


# --- read-only tools ---------------------------------------------------------


async def _search_environments(args: dict[str, Any]) -> Any:
    query = str(args.get("query", ""))
    kind = args.get("kind")
    provider_id = args.get("provider")
    provider_ids = (
        [provider_id] if provider_id else [p.provider for p in list_providers()]
    )
    results: list[dict[str, Any]] = []
    for pid in provider_ids:
        try:
            provider = get_provider(pid)
            hits = await asyncio.to_thread(provider.search, query, kind, 10)
            results.extend(h.model_dump() for h in hits)
        except ProviderError as exc:
            results.append({"provider": pid, "error": str(exc)})
    return {"results": results}


async def _resolve_environment(args: dict[str, Any]) -> Any:
    try:
        provider = get_provider(str(args.get("provider", "")))
        ref = await asyncio.to_thread(
            provider.resolve, str(args.get("id", "")), args.get("kind")
        )
        return ref.model_dump()
    except ProviderError as exc:
        return {"error": str(exc)}


async def _list_projects(_args: dict[str, Any]) -> Any:
    return {
        "projects": [
            p.model_dump() for p in await asyncio.to_thread(projects.list_projects)
        ]
    }


async def _project_status(args: dict[str, Any]) -> Any:
    project = projects.get_project(str(args.get("projectId", "")))
    if project is None:
        return {"error": "unknown project"}
    from backend.modules.training.kernels import training_kernels
    from backend.modules.training.runners.script_runner import script_runner

    kernel = training_kernels.session_for(project.id, projects.DEFAULT_NOTEBOOK)
    return {
        "id": project.id,
        "name": project.name,
        "venv_ready": envs.venv_ready(project),
        "data_ready": project.data_ready,
        "refs": [r.model_dump() for r in project.refs],
        "kernel": kernel.status if kernel else "not started",
        "runs": [r for r in script_runner.status() if r["projectId"] == project.id],
    }


# --- side-effecting tools ----------------------------------------------------


async def _create_project(args: dict[str, Any]) -> Any:
    from backend.modules.training.routes import _python, _start_bootstrap

    try:
        provider = get_provider(str(args.get("provider", "")))
        ref = await asyncio.to_thread(
            provider.resolve, str(args.get("ref", "")), args.get("kind")
        )
    except ProviderError as exc:
        return {"error": str(exc)}
    name = str(args.get("name") or ref.title or ref.id)
    project = await asyncio.to_thread(projects.create_project, name, [ref], _python())
    scaffold = await asyncio.to_thread(provider.scaffold, ref, project)
    await asyncio.to_thread(
        notebooks.new_notebook, project, projects.DEFAULT_NOTEBOOK, scaffold.cells
    )
    _start_bootstrap(project, scaffold.requirements)
    return {
        "projectId": project.id,
        "notebook": projects.DEFAULT_NOTEBOOK,
        "ref": ref.model_dump(),
        "note": "project created; venv bootstrapping in the background",
    }


async def _fetch_data(args: dict[str, Any]) -> Any:
    from backend.modules.training.routes import _start_fetch

    project = projects.get_project(str(args.get("projectId", "")))
    if project is None:
        return {"error": "unknown project"}
    if not project.refs:
        return {"error": "project has no environment refs"}
    _start_fetch(project)
    return {"status": "fetching", "note": "progress streams on the training channel"}


async def _install_deps(args: dict[str, Any]) -> Any:
    from backend.modules.training.routes import _start_install

    project = projects.get_project(str(args.get("projectId", "")))
    if project is None:
        return {"error": "unknown project"}
    packages = args.get("packages") or []
    if not isinstance(packages, list) or not packages:
        return {"error": "packages must be a non-empty list"}
    _start_install(project, [str(p) for p in packages])
    return {"status": "installing", "packages": packages}


async def _start_run(args: dict[str, Any]) -> Any:
    from backend.modules.training.runners.script_runner import script_runner

    project = projects.get_project(str(args.get("projectId", "")))
    if project is None:
        return {"error": "unknown project"}
    try:
        run = await asyncio.to_thread(
            script_runner.start, project, str(args.get("script", ""))
        )
    except ValueError as exc:
        return {"error": str(exc)}
    return {"runId": run.id, "state": "running"}


async def _stop_run(args: dict[str, Any]) -> Any:
    from backend.modules.training.runners.script_runner import script_runner

    stopped = script_runner.stop(str(args.get("runId", "")))
    return {"stopped": stopped}


async def _push(args: dict[str, Any]) -> Any:
    from pathlib import Path

    from backend.modules.training.push import PushError, get_target

    project = projects.get_project(str(args.get("projectId", "")))
    if project is None:
        return {"error": "unknown project"}
    try:
        target = get_target(str(args.get("target", "")))
        notebook = Path(project.root) / projects.DEFAULT_NOTEBOOK
        result = await asyncio.to_thread(
            target.push, project, notebook, lambda _l: None
        )
        return result.model_dump()
    except PushError as exc:
        return {"error": str(exc)}


async def _push_status(args: dict[str, Any]) -> Any:
    from backend.modules.training.push import PushError, get_target

    project = projects.get_project(str(args.get("projectId", "")))
    if project is None:
        return {"error": "unknown project"}
    try:
        result = await asyncio.to_thread(
            get_target(str(args.get("target", ""))).status, project
        )
        return result.model_dump()
    except PushError as exc:
        return {"error": str(exc)}


async def _render_manim(args: dict[str, Any]) -> Any:
    from backend.modules.training.models import ManimRequest
    from backend.modules.training.runners.manim_runner import manim_runner

    project = projects.get_project(str(args.get("projectId", "")))
    if project is None:
        return {"error": "unknown project"}
    manim_runner.render(
        project,
        ManimRequest(
            scene=str(args.get("scene", "Scene")),
            source=args.get("source"),
            file=args.get("file"),
        ),
    )
    return {"status": "rendering", "note": "progress streams on the training channel"}


# --- registration ------------------------------------------------------------

_STR = {"type": "string"}
_PROJECT = {"projectId": {"type": "string", "description": "Training project id."}}

_TOOLS = [
    AgentTool(
        name="training.search_environments",
        description=(
            "Search trainable environments across providers (Kaggle competitions/"
            "datasets, Hugging Face datasets, Gymnasium envs). Returns provider refs "
            "to create a project from."
        ),
        parameters={
            "query": {
                "type": "string",
                "description": "Search text, e.g. 'pokemon tcg'.",
            },
            "provider": {
                "type": "string",
                "description": "Optional: kaggle|huggingface|gymnasium.",
            },
            "kind": {
                "type": "string",
                "description": "Optional: competition|dataset|env.",
            },
        },
        required=["query"],
        handler=_search_environments,
        group="training",
    ),
    AgentTool(
        name="training.resolve_environment",
        description="Validate/enrich a specific environment id for a provider.",
        parameters={
            "provider": _STR,
            "id": _STR,
            "kind": {
                "type": "string",
                "description": "Optional: competition|dataset|env.",
            },
        },
        required=["provider", "id"],
        handler=_resolve_environment,
        group="training",
    ),
    AgentTool(
        name="training.list_projects",
        description="List existing training projects (id, name, refs, venv/data status).",
        handler=_list_projects,
        group="training",
    ),
    AgentTool(
        name="training.project_status",
        description="Detailed status of one project: venv/data readiness, kernel state, active runs.",
        parameters=dict(_PROJECT),
        required=["projectId"],
        handler=_project_status,
        group="training",
    ),
    AgentTool(
        name="training.create_project",
        description=(
            "Create a training project from a provider ref: makes the project dir + "
            "per-project uv venv, scaffolds a starter main.ipynb, and starts the venv "
            "bootstrap. Returns the projectId to open the notebook pane with."
        ),
        parameters={
            "provider": _STR,
            "ref": {
                "type": "string",
                "description": "The environment id (e.g. 'pokemon-tcg').",
            },
            "kind": {
                "type": "string",
                "description": "Optional: competition|dataset|env.",
            },
            "name": {"type": "string", "description": "Optional display name."},
        },
        required=["provider", "ref"],
        side_effect=True,
        specifier_template="{provider}:{ref}",
        handler=_create_project,
        group="training",
    ),
    AgentTool(
        name="training.fetch_data",
        description="Download the project's dataset(s) into its data/ dir (progress streams to the UI).",
        parameters=dict(_PROJECT),
        required=["projectId"],
        side_effect=True,
        specifier_template="{projectId}",
        handler=_fetch_data,
        group="training",
    ),
    AgentTool(
        name="training.install_deps",
        description="Install extra Python packages into the project's venv.",
        parameters={
            **_PROJECT,
            "packages": {"type": "array", "items": {"type": "string"}},
        },
        required=["projectId", "packages"],
        side_effect=True,
        specifier_template="{projectId}",
        handler=_install_deps,
        group="training",
    ),
    AgentTool(
        name="training.start_run",
        description="Run a training script in the project venv (metrics/frames stream to the panes).",
        parameters={
            **_PROJECT,
            "script": {
                "type": "string",
                "description": "Script path relative to the project root.",
            },
        },
        required=["projectId", "script"],
        side_effect=True,
        specifier_template="{projectId}",
        handler=_start_run,
        group="training",
    ),
    AgentTool(
        name="training.stop_run",
        description="Stop a running training script by run id.",
        parameters={"runId": _STR},
        required=["runId"],
        side_effect=True,
        specifier_template="{runId}",
        handler=_stop_run,
        group="training",
    ),
    AgentTool(
        name="training.push",
        description="Push the project notebook to a cloud target (kaggle | colab). Returns the URL.",
        parameters={
            **_PROJECT,
            "target": {"type": "string", "description": "kaggle|colab."},
        },
        required=["projectId", "target"],
        side_effect=True,
        specifier_template="{target}:{projectId}",
        handler=_push,
        group="training",
    ),
    AgentTool(
        name="training.push_status",
        description="Status of the last push to a cloud target for a project.",
        parameters={**_PROJECT, "target": _STR},
        required=["projectId", "target"],
        handler=_push_status,
        group="training",
    ),
    AgentTool(
        name="training.render_manim",
        description="Render a manim scene for the project (from inline source or a scene file).",
        parameters={
            **_PROJECT,
            "scene": {"type": "string", "description": "Scene class name."},
            "source": {
                "type": "string",
                "description": "Optional: manim scene source to write and render.",
            },
            "file": {
                "type": "string",
                "description": "Optional: existing scene file relative to the project root.",
            },
        },
        required=["projectId", "scene"],
        side_effect=True,
        specifier_template="{projectId}",
        handler=_render_manim,
        group="training",
    ),
]


def register_agent_tools() -> None:
    """Insert the training backend tools into the sdk registry (called from app.py).
    First-party consumer of the same registry backend plugins write to."""
    for tool in _TOOLS:
        registry.agent_tools[tool.name] = tool

    # The `trackers` connector rides along here because it belongs to this module
    # even though it contributes no tools of its own: W&B/MLflow credentials are a
    # property of a recipe, not a capability the agent should reach for. It is the
    # one connector whose id names no tool group — see `trackers.py`.
    from backend.modules.training.trackers import build as build_trackers

    connector = build_trackers()
    registry.connectors[connector.id] = connector

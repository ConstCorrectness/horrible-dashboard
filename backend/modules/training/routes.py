"""HTTP surface of the training module (`/api/training`).

Short blocking work (provider search/resolve, notebook IO) is offloaded with
``asyncio.to_thread``; long work (venv bootstrap, dataset fetch) runs on daemon
threads that stream progress over the shared `/ws` socket as `training` channel
events (`env_progress`, `fetch_progress`, `project_changed`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from backend.modules.settings.routes import get_value
from backend.modules.training import (
    basemodels,
    convert,
    envs,
    notebooks,
    projects,
    recipes,
    sweeps,
)
from backend.modules.training.models import (
    AcceptedResponse,
    CreateProjectRequest,
    EnvironmentRefModel,
    InstallDepsRequest,
    ManimRequest,
    NotebookModel,
    ProjectListResponse,
    ProjectModel,
    ProviderListResponse,
    PushResultModel,
    ResolveRequest,
    SearchResponse,
)
from backend.modules.training.providers import (
    ProviderError,
    get_provider,
    list_providers,
)
from backend.modules.training.stream import broadcast_threadsafe

logger = logging.getLogger(__name__)

#: Where `recipe/run` writes the scripts it generates, beside the sweep's own
#: `sweeps/` folder and under the project root so it travels with the project.
RECIPE_SCRIPT_DIR = "runs"

router = APIRouter(prefix="/training", tags=["training"])


def _provider_or_404(provider_id: str):
    try:
        return get_provider(provider_id)
    except ProviderError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _project_or_404(project_id: str) -> ProjectModel:
    project = projects.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"unknown project: {project_id}")
    return project


@router.get("/providers")
async def providers() -> ProviderListResponse:
    return ProviderListResponse(providers=list_providers())


@router.get("/providers/{provider_id}/search")
async def search(
    provider_id: str, q: str, kind: str | None = None, limit: int = 20
) -> SearchResponse:
    provider = _provider_or_404(provider_id)
    try:
        results = await asyncio.to_thread(provider.search, q, kind, limit)
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return SearchResponse(results=results)


@router.post("/providers/{provider_id}/resolve")
async def resolve(provider_id: str, req: ResolveRequest) -> EnvironmentRefModel:
    provider = _provider_or_404(provider_id)
    try:
        return await asyncio.to_thread(provider.resolve, req.id, req.kind)
    except ProviderError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/projects")
async def project_list() -> ProjectListResponse:
    return ProjectListResponse(projects=await asyncio.to_thread(projects.list_projects))


@router.get("/projects/{project_id}")
async def project_get(project_id: str) -> ProjectModel:
    project = _project_or_404(project_id)
    project.venv_ready = envs.venv_ready(project)
    return project


@router.post("/projects", status_code=201)
async def project_create(req: CreateProjectRequest) -> ProjectModel:
    provider = _provider_or_404(req.provider)
    try:
        ref = await asyncio.to_thread(provider.resolve, req.ref, req.kind)
    except ProviderError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    name = req.name or ref.title or ref.id
    project = await asyncio.to_thread(projects.create_project, name, [ref], _python())
    scaffold = await asyncio.to_thread(provider.scaffold, ref, project)
    await asyncio.to_thread(
        notebooks.new_notebook, project, projects.DEFAULT_NOTEBOOK, scaffold.cells
    )
    _start_bootstrap(project, scaffold.requirements)
    return project


@router.delete("/projects/{project_id}")
async def project_delete(project_id: str) -> dict[str, bool]:
    project = _project_or_404(project_id)
    deleted = await asyncio.to_thread(projects.delete_project, project_id)
    if deleted:
        # Announce it. Every other change to a project broadcasts, but a *delete*
        # did not — so the only list that updated was the one belonging to whoever
        # clicked, and a project removed through the agent or from a second window
        # stayed on screen until something else happened to trigger a refresh.
        # The doomed project's own body, so the event keeps its shape, plus the
        # flag that says which way it changed.
        broadcast_threadsafe(
            "project_changed", {**project.model_dump(), "deleted": True}
        )
    return {"deleted": deleted}


@router.post("/projects/{project_id}/fetch", status_code=202)
async def project_fetch(project_id: str) -> AcceptedResponse:
    project = _project_or_404(project_id)
    if not project.refs:
        raise HTTPException(status_code=400, detail="project has no environment refs")
    _start_fetch(project)
    return AcceptedResponse(detail="fetch started; progress on ws `training` channel")


@router.post("/projects/{project_id}/deps", status_code=202)
async def project_deps(project_id: str, req: InstallDepsRequest) -> AcceptedResponse:
    project = _project_or_404(project_id)
    if not req.packages:
        raise HTTPException(status_code=400, detail="no packages given")
    _start_install(project, req.packages)
    return AcceptedResponse(detail="install started; progress on ws `training` channel")


@router.get("/projects/{project_id}/notebook")
async def notebook_get(
    project_id: str, path: str = projects.DEFAULT_NOTEBOOK
) -> NotebookModel:
    project = _project_or_404(project_id)
    try:
        nb_path = notebooks.notebook_path(project, path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not nb_path.is_file():
        raise HTTPException(status_code=404, detail=f"no notebook at {path}")
    nb = await asyncio.to_thread(notebooks.load, nb_path)
    return notebooks.to_model(nb, path)


@router.put("/projects/{project_id}/notebook")
async def notebook_put(project_id: str, model: NotebookModel) -> NotebookModel:
    project = _project_or_404(project_id)
    try:
        nb_path = notebooks.notebook_path(project, model.path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    nb = notebooks.from_model(model)
    await asyncio.to_thread(notebooks.save, nb_path, nb)
    return notebooks.to_model(nb, model.path)


# --- runs, manim, media -------------------------------------------------------


@router.post("/projects/{project_id}/runs", status_code=202)
async def run_start(project_id: str, body: dict) -> dict:
    project = _project_or_404(project_id)
    from backend.modules.training.runners.script_runner import script_runner

    script = str(body.get("script", ""))
    try:
        run = await asyncio.to_thread(script_runner.start, project, script)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"runId": run.id, "script": script, "state": "running"}


@router.get("/runs")
async def run_list(tail: int = 0) -> dict:
    from backend.modules.training.runners.script_runner import script_runner

    return {"runs": script_runner.status(tail=max(0, min(tail, 200)))}


@router.get("/projects/{project_id}/runs/logs")
async def run_logs(project_id: str, name: str = "", tail: int = 200) -> dict:
    """A run's output read from disk, newest run first when `name` is omitted.

    Deliberately keyed by **file**, not by run id. The run registry is in memory,
    so a backend restart forgets every id it ever handed out while the logs are
    still sitting in the project — and "the run list is empty" is exactly what a
    caller must not read as "nothing went wrong". This answers from the directory.
    """
    project = _project_or_404(project_id)
    from backend.modules.training.runners.script_runner import LOG_DIR

    folder = Path(project.root) / LOG_DIR

    def work() -> dict:
        if not folder.is_dir():
            return {"logs": [], "name": "", "lines": []}
        files = sorted(
            folder.glob("*.log"), key=lambda f: f.stat().st_mtime, reverse=True
        )
        names = [f.name for f in files]
        if not files:
            return {"logs": [], "name": "", "lines": []}
        chosen = next((f for f in files if f.name == name), files[0])
        try:
            text = chosen.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return {"logs": names, "name": chosen.name, "error": str(exc), "lines": []}
        lines = text.splitlines()
        return {
            "logs": names,
            "name": chosen.name,
            "lines": lines[-max(1, min(tail, 2000)) :],
            "truncated": len(lines) > tail,
        }

    return await asyncio.to_thread(work)


@router.delete("/runs/{run_id}")
async def run_stop(run_id: str) -> dict:
    from backend.modules.training.runners.script_runner import script_runner

    stopped = script_runner.stop(run_id)
    if not stopped:
        raise HTTPException(status_code=404, detail=f"unknown run: {run_id}")
    return {"stopped": True}


@router.post("/projects/{project_id}/manim", status_code=202)
async def manim_render(project_id: str, req: ManimRequest) -> AcceptedResponse:
    project = _project_or_404(project_id)
    from backend.modules.training.runners.manim_runner import manim_runner

    manim_runner.render(project, req)
    return AcceptedResponse(detail="render started; watch manim_status on ws")


@router.get("/projects/{project_id}/media/{file_path:path}")
async def media(project_id: str, file_path: str) -> FileResponse:
    project = _project_or_404(project_id)
    media_root = (Path(project.root) / "media").resolve()
    target = (media_root / file_path).resolve()
    # Traversal guard: never serve anything outside the project's media dir.
    if not target.is_relative_to(media_root) or not target.is_file():
        raise HTTPException(status_code=404, detail="no such media file")
    return FileResponse(target)


# --- cloud push ----------------------------------------------------------------


@router.get("/push/targets")
async def push_targets() -> dict:
    from backend.modules.training.push import list_targets

    return {"targets": list_targets()}


@router.post("/projects/{project_id}/push/{target_id}")
async def push(project_id: str, target_id: str) -> PushResultModel:
    from backend.modules.training.push import PushError, get_target

    project = _project_or_404(project_id)
    try:
        target = get_target(target_id)
    except PushError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    notebook = Path(project.root) / projects.DEFAULT_NOTEBOOK
    if not notebook.is_file():
        raise HTTPException(status_code=400, detail="project has no main.ipynb")
    emit = _progress(project.id, "push_progress")
    try:
        return await asyncio.to_thread(target.push, project, notebook, emit)
    except PushError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/projects/{project_id}/push/{target_id}/status")
async def push_status(project_id: str, target_id: str) -> PushResultModel:
    from backend.modules.training.push import PushError, get_target

    project = _project_or_404(project_id)
    try:
        target = get_target(target_id)
        return await asyncio.to_thread(target.status, project)
    except PushError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/google/auth/start")
async def google_auth_start() -> dict:
    from backend.modules.training import google_auth
    from backend.modules.training.push.base import PushError

    try:
        return {"authUrl": await asyncio.to_thread(google_auth.auth_start)}
    except PushError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/google/auth/complete")
async def google_auth_complete(body: dict) -> dict:
    from backend.modules.training import google_auth
    from backend.modules.training.push.base import PushError

    code = str(body.get("code", ""))
    if not code:
        raise HTTPException(status_code=400, detail="missing code")
    try:
        await asyncio.to_thread(google_auth.auth_complete, code)
    except PushError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@router.get("/google/status")
async def google_status() -> dict:
    from backend.modules.training import google_auth

    return google_auth.status()


@router.delete("/google/auth")
async def google_disconnect() -> dict:
    from backend.modules.training import google_auth

    google_auth.disconnect()
    return {"ok": True}


# --- background workers ------------------------------------------------------


def _python() -> str:
    return str(get_value("training.defaultPython", "3.12"))


def _progress(project_id: str, event: str):
    def emit(line: str, pct: float | None = None) -> None:
        data = {"projectId": project_id, "line": line}
        if pct is not None:
            data["pct"] = pct
        broadcast_threadsafe(event, data)

    return emit


_mark_lock = threading.Lock()

# In-flight background workers (venv bootstrap, dep install, dataset fetch).
#
# These are fire-and-forget daemon threads, which means nothing could ever wait
# for one: no handle escaped `_start_*`. Tests had to poll a wall clock instead
# ("is the file there yet?"), which is a race disguised as a test — it passes when
# the machine is idle and fails when the suite is loaded, and the failure looks
# like a product bug rather than a scheduling one.
#
# Tracking them costs a set and buys a real completion signal. Threads remove
# themselves when they finish, so this stays bounded by what is actually running.
_workers: set[threading.Thread] = set()
_workers_lock = threading.Lock()


def _spawn(name: str, work: Callable[[], None]) -> threading.Thread:
    """Start a tracked background worker."""

    def runner() -> None:
        try:
            work()
        finally:
            with _workers_lock:
                _workers.discard(threading.current_thread())

    thread = threading.Thread(target=runner, daemon=True, name=name)
    with _workers_lock:
        _workers.add(thread)
    thread.start()
    return thread


def join_workers(timeout: float = 60.0) -> bool:
    """Block until every in-flight worker has finished. False if any outlived `timeout`.

    The timeout is a **deadlock backstop, not a schedule**: `join` returns the
    instant the thread finishes, however loaded the machine is, so a caller waiting
    on real work never fails merely because the CPU was busy. That distinction is
    the whole point — the polling this replaced could not tell "still working" from
    "never going to work".

    Re-checks the set after each pass, since a worker may start while we are
    waiting on another.
    """
    deadline = time.monotonic() + timeout
    while True:
        with _workers_lock:
            pending = [t for t in _workers if t.is_alive()]
        if not pending:
            return True
        for thread in pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            thread.join(remaining)


def _mark(project: ProjectModel, **flags: bool) -> None:
    # Re-read before writing (under a lock): the venv and fetch workers run
    # concurrently, each holding its own snapshot — writing a stale one would
    # clobber the other's flag.
    with _mark_lock:
        fresh = projects.get_project(project.id) or project
        for key, value in flags.items():
            setattr(fresh, key, value)
        projects.update_project(fresh)
    broadcast_threadsafe("project_changed", fresh.model_dump())


def _start_bootstrap(project: ProjectModel, requirements: list[str]) -> None:
    emit = _progress(project.id, "env_progress")

    def work() -> None:
        try:
            envs.bootstrap(project, requirements, emit)
            _mark(project, venv_ready=True)
            emit("venv ready", 1.0)
        except ProviderError as exc:
            emit(f"venv setup failed: {exc}")
        except Exception:
            logger.exception("venv bootstrap failed for %s", project.id)
            emit("venv setup failed — see backend log")

    _spawn(f"venv-{project.id}", work)


def _start_install(project: ProjectModel, packages: list[str]) -> None:
    emit = _progress(project.id, "env_progress")

    def work() -> None:
        try:
            envs.install(project, packages, emit)
            emit("install complete", 1.0)
        except ProviderError as exc:
            emit(f"install failed: {exc}")
        except Exception:
            logger.exception("dep install failed for %s", project.id)
            emit("install failed — see backend log")

    _spawn(f"deps-{project.id}", work)


def _start_fetch(project: ProjectModel) -> None:
    emit = _progress(project.id, "fetch_progress")

    def work() -> None:
        dest = notebooks.notebook_path(project, "data")
        try:
            for ref in project.refs:
                provider = get_provider(ref.provider)
                result = provider.fetch(ref, dest, emit)
                if result.note:
                    emit(result.note)
            _mark(project, data_ready=True)
            emit("data ready", 1.0)
        except ProviderError as exc:
            emit(f"fetch failed: {exc}")
        except Exception:
            logger.exception("fetch failed for %s", project.id)
            emit("fetch failed — see backend log")

    _spawn(f"fetch-{project.id}", work)


# --- the recipe surface -------------------------------------------------------
#
# A fine-tuning config as a schema rather than a blank cell. The form's answers
# live in `recipe.json`; applying one writes notebook cells, which is the same
# execution path everything else here uses.


def _shape_warnings(recipe: recipes.Recipe, shape: dict[str, Any] | None) -> list[str]:
    """What a typed dataset's shape means for the run, before it is a stack trace.

    The failure being pre-empted is specific and was reachable from the default
    path: a reasoning dataset spelled `question`/`solution` has no `text` column,
    trl's `SFTConfig` defaults to one, and the run raises `KeyError: 'text'`
    minutes in, after the base model has downloaded and loaded — a message that
    names neither the dataset nor the column that is missing.

    Silent when there is nothing to say. A dataset that could not be reached is
    **not** warned about: being offline is not evidence about a dataset's shape,
    the same posture as `basemodels.check`.
    """
    if not shape:
        return []
    adaptation = shape.get("adaptation") or {}
    if adaptation.get("ok"):
        return []
    detected = str(shape.get("format") or "unknown")
    problem = str(adaptation.get("problem") or "")
    return [
        f"`{recipe.dataset}` reads as **{detected}** data "
        f"({shape.get('reason', '')}) — {problem} Until it is mapped, the "
        f"generated cell trains on trl's default `text` column, which this "
        f"dataset does not have."
    ]


def _recipe_payload(project: ProjectModel, *, refresh: bool = False) -> dict:
    recipe = recipes.load_recipe(project)
    # Look at the dataset the recipe actually names, not only at a picked one.
    # Advisory and cached; it fills a blank column map and never argues with one.
    shape = recipes.ensure_shape(recipe)
    intro = recipes.introspect(
        project, refresh=refresh, backend_id=recipe.backend, task=recipe.task
    )
    from backend.modules.hardware import probe as hardware
    from backend.modules.training.backends.base import get_backend

    try:
        backend = get_backend(recipe.backend)
    except ValueError:
        backend = get_backend("trl")
    profile = hardware.get_profile()
    return {
        "recipe": recipe.to_dict(),
        # The catalog is per backend AND per task now: DPO renders `beta`, SFT
        # renders `packing`, and sending one list for both would show knobs the
        # selected trainer has never heard of.
        "fields": [
            f.to_dict()
            for f in recipes.catalog(recipe.backend, recipe.task, recipe.use_lora)
        ],
        "introspection": intro.to_dict(),
        "resolved": [
            r.to_dict()
            for r in recipes.resolve_all(
                intro, recipe.backend, recipe.task, recipe.use_lora
            )
        ],
        # The base model is free text that goes straight into `from_pretrained`,
        # so it is checked here rather than discovered minutes into a run — see
        # `basemodels.check`. Advisory: it never blocks generating the cells.
        "warnings": recipes.warnings_for(recipe.values, recipe.trackers)
        + basemodels.check(recipe.base_model)
        + _shape_warnings(recipe, shape)
        + backend.check(recipe.task, profile),
        # What five real rows said about the typed ref, so the pane can show the
        # verdict and its evidence rather than a column map that appeared by
        # itself. `None` when there is nothing to say (a picked dataset, an empty
        # field, or a Hub that could not be asked).
        "datasetShape": shape,
        "trackers": list(recipes.TRACKERS),
        "tasks": recipes.tasks(),
        "backends": recipes.backends(),
        "requirements": backend.requirements(recipe.task, profile),
        "sweep": sweeps.load_spec(project).to_dict(),
        "outputTypes": list(convert.OUTPUT_TYPES),
    }


@router.get("/learn/glossary")
async def learn_glossary() -> dict:
    """The recipe catalog's knobs with their help text — what the Learn strip
    matches a notebook cell against. See `recipes.glossary`."""
    return {"terms": recipes.glossary()}


@router.get("/models/search")
async def model_search(q: str = "", limit: int = 20) -> dict:
    """Hugging Face text-generation models, for the recipe's base-model picker.

    So the name is picked rather than remembered: it is the one recipe field that
    is copied verbatim into `from_pretrained`, and a wrong one costs a download and
    a stack trace to discover.
    """
    try:
        results = await asyncio.to_thread(basemodels.search, q, limit)
    except Exception as exc:  # noqa: BLE001 — an unreachable Hub is the user's to read
        raise HTTPException(status_code=502, detail=f"model search failed: {exc}")
    return {"models": results}


@router.get("/projects/{project_id}/recipe")
async def recipe_get(project_id: str, refresh: bool = False) -> dict:
    project = _project_or_404(project_id)
    # Introspection spawns the project's python, so it is offloaded like every
    # other blocking call here rather than run on the event loop.
    return await asyncio.to_thread(_recipe_payload, project, refresh=refresh)


@router.put("/projects/{project_id}/recipe")
async def recipe_put(project_id: str, body: dict) -> dict:
    project = _project_or_404(project_id)
    recipe = recipes.Recipe.from_dict(body)
    await asyncio.to_thread(recipes.save_recipe, project, recipe)
    return await asyncio.to_thread(_recipe_payload, project)


@router.post("/projects/{project_id}/recipe/apply")
async def recipe_apply(project_id: str, body: dict) -> dict:
    """Write the recipe's cells into `main.ipynb`, replacing a previous block."""
    project = _project_or_404(project_id)
    recipe = recipes.Recipe.from_dict(body) if body else recipes.load_recipe(project)

    def work() -> int:
        # The same detection the form runs, at the moment the cells are written.
        # Both seams need it: the human clicks "Write cells" after the form has
        # already adopted a map, but `recipe.apply` is an agent tool that can be
        # the first thing to touch this recipe at all.
        recipes.ensure_shape(recipe)
        recipes.save_recipe(project, recipe)
        intro = recipes.introspect(project, backend_id=recipe.backend, task=recipe.task)
        return recipes.apply_to_notebook(project, recipe, intro)

    try:
        written = await asyncio.to_thread(work)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"cells": written, "notebook": "main.ipynb"}


@router.post("/projects/{project_id}/recipe/run", status_code=202)
async def recipe_run(project_id: str, body: dict | None = None) -> dict:
    """Run the recipe as a script, outside the kernel — the headless half.

    The gap this fills: `recipe/apply` writes cells into `main.ipynb`, and the
    only thing that can execute those cells is the notebook *pane* — the
    `cells.run_all` tool lives on the frontend and resolves an open session. So
    with no pane open there was no way to train at all: an agent could pick a
    model, pick a dataset, install the stack, generate correct code, and then
    stop, one step short. `training.start_run` was not that step either — it
    takes a script path and nothing in the product wrote one.

    Reuses the sweep's machinery exactly (`materialize_script` -> a `.py` in the
    project -> `script_runner`), because a sweep of one point is already what
    "run this recipe once, headlessly" means, and a second execution path would
    be the place the notebook and the script start disagreeing about what a
    recipe says.

    The script is written to a **timestamped** file rather than one fixed name:
    a run that is still going must stay readable while the next one is generated,
    and the path is what the run reports.
    """
    project = _project_or_404(project_id)
    recipe = recipes.load_recipe(project)
    run_name = str((body or {}).get("runName") or "") or None

    def work() -> tuple[str, str]:
        recipes.ensure_shape(recipe)
        recipes.save_recipe(project, recipe)
        intro = recipes.introspect(project, backend_id=recipe.backend, task=recipe.task)
        folder = Path(project.root) / RECIPE_SCRIPT_DIR
        folder.mkdir(parents=True, exist_ok=True)
        name = f"run-{time.strftime('%Y%m%d-%H%M%S')}.py"
        source = recipes.materialize_script(recipe, intro, run_name)
        (folder / name).write_text(source, encoding="utf-8")
        return f"{RECIPE_SCRIPT_DIR}/{name}", source

    script, source = await asyncio.to_thread(work)
    from backend.modules.training.runners.script_runner import script_runner

    try:
        run = await asyncio.to_thread(script_runner.start, project, script)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "runId": run.id,
        "script": script,
        "state": "running",
        # Returned so the caller can read what it is about to be judged on
        # without a second round-trip — an agent that starts a run it cannot see
        # is the thing this route exists to stop being.
        "source": source,
    }


@router.post("/projects/{project_id}/recipe/install-stack")
async def recipe_install_stack(project_id: str) -> dict:
    """Install the selected backend's libraries into the project venv.

    The step that never existed: `bootstrap` installs ipykernel and the helper and
    nothing else, which is why the recipe form has always reported "trl and peft
    are not installed". torch is resolved against the hardware probe, so the wheel
    matches the card — and the reason is returned, because a CPU build landing on a
    machine whose owner knows they have a GPU needs an explanation, not a shrug.
    """
    project = _project_or_404(project_id)
    recipe = recipes.load_recipe(project)
    from backend.modules.hardware import probe as hardware
    from backend.modules.training.backends.base import get_backend

    try:
        backend = get_backend(recipe.backend)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    profile = await asyncio.to_thread(hardware.get_profile)
    packages = backend.requirements(recipe.task, profile)

    def progress(line: str) -> None:
        broadcast_threadsafe("env_progress", {"projectId": project.id, "line": line})

    def work() -> str:
        reason = envs.install_stack(project, packages, profile, progress)
        # The venv changed, so the cached probe is stale — and the whole point of
        # installing is that the form should now say "validated".
        recipes.introspect(
            project, refresh=True, backend_id=recipe.backend, task=recipe.task
        )
        return reason

    try:
        reason = await asyncio.to_thread(work)
    except Exception as exc:  # noqa: BLE001 — install failures are the user's to read
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"installed": packages, "torch": reason}


# --- sweeps -------------------------------------------------------------------
#
# The same recipe N times with one thing changed. Points run as processes through
# the script runner, never on the kernel, which is single and serial.


@router.get("/projects/{project_id}/sweep")
async def sweep_get(project_id: str) -> dict:
    project = _project_or_404(project_id)
    spec = await asyncio.to_thread(sweeps.load_spec, project)
    recipe = await asyncio.to_thread(recipes.load_recipe, project)
    return {
        "spec": spec.to_dict(),
        "problems": sweeps.validate(recipe, spec) if spec.axes else [],
        "sweeps": sweeps.status(),
        "strategies": list(sweeps.STRATEGIES),
        "maxPoints": sweeps.MAX_POINTS,
    }


@router.put("/projects/{project_id}/sweep")
async def sweep_put(project_id: str, body: dict) -> dict:
    project = _project_or_404(project_id)
    spec = sweeps.SweepSpec.from_dict(body)
    await asyncio.to_thread(sweeps.save_spec, project, spec)
    return await sweep_get(project_id)


@router.post("/projects/{project_id}/sweep/preview")
async def sweep_preview(project_id: str, body: dict) -> dict:
    """What this sweep would run, without running it."""
    project = _project_or_404(project_id)
    spec = sweeps.SweepSpec.from_dict(body)
    recipe = await asyncio.to_thread(recipes.load_recipe, project)
    problems = sweeps.validate(recipe, spec)
    if problems:
        return {"problems": problems, "points": []}
    try:
        points = sweeps.expand(recipe, spec)
    except sweeps.SweepError as exc:
        return {"problems": [str(exc)], "points": []}
    return {"problems": [], "points": [p.to_dict() for p in points]}


@router.post("/projects/{project_id}/sweep/start")
async def sweep_start(project_id: str, body: dict) -> dict:
    project = _project_or_404(project_id)
    if not envs.venv_ready(project):
        raise HTTPException(status_code=400, detail="project venv is not ready")
    spec = sweeps.SweepSpec.from_dict(body) if body else sweeps.load_spec(project)
    recipe = await asyncio.to_thread(recipes.load_recipe, project)
    intro = await asyncio.to_thread(
        recipes.introspect, project, backend_id=recipe.backend, task=recipe.task
    )
    try:
        await asyncio.to_thread(sweeps.save_spec, project, spec)
        sweep_id = sweeps.start(project, recipe, spec, intro)
    except sweeps.SweepError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"sweepId": sweep_id}


@router.post("/sweeps/{sweep_id}/stop")
async def sweep_stop(sweep_id: str) -> dict:
    return {"stopped": sweeps.stop(sweep_id)}


@router.get("/sweeps")
async def sweep_list(sweep_id: str = "") -> list[dict]:
    return sweeps.status(sweep_id)


@router.get("/recipe/docs")
async def recipe_docs() -> dict:
    """Doc links for the config classes, from this node's own crawled index."""
    return {"links": await recipes.doc_links()}


# --- checkpoint → GGUF --------------------------------------------------------


@router.get("/inventory")
async def inventory() -> dict:
    """Every model this node made: provenance, metrics and scores in one row.

    Deliberately NOT a `response_model`. A Pydantic response model silently drops
    any field it does not declare, and this payload is a join over four stores whose
    shapes move independently — declaring one here means a field added to lineage or
    to a run summary reaches the browser as `undefined`, with nothing failing. The
    route is tested against its HTTP body for the same reason.
    """
    from backend.modules.training import lineage

    return {"models": await asyncio.to_thread(lineage.inventory)}


@router.get("/projects/{project_id}/checkpoints")
async def checkpoints(project_id: str) -> dict:
    project = _project_or_404(project_id)
    found = await asyncio.to_thread(convert.list_checkpoints, project)
    return {"checkpoints": found, "note": convert.python_version_note()}


@router.post("/projects/{project_id}/convert")
async def convert_checkpoint(project_id: str, body: dict) -> StreamingResponse:
    """Convert a checkpoint to GGUF, streaming progress as NDJSON.

    NDJSON on the request rather than the `training` channel, matching the
    llama.cpp module's downloads: a conversion belongs to the request that asked
    for it, and navigating away should stop it rather than leave a broadcast
    running for nobody.
    """
    project = _project_or_404(project_id)

    async def gen():
        async for event in convert.run_conversion(
            project,
            str(body.get("checkpoint") or ""),
            out_type=str(body.get("outType") or "f16"),
            base_model=str(body.get("baseModel") or ""),
        ):
            yield json.dumps(event) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")

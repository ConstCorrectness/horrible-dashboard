"""HTTP surface of the training module (`/api/training`).

Short blocking work (provider search/resolve, notebook IO) is offloaded with
``asyncio.to_thread``; long work (venv bootstrap, dataset fetch) runs on daemon
threads that stream progress over the shared `/ws` socket as `training` channel
events (`env_progress`, `fetch_progress`, `project_changed`).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from backend.modules.settings.routes import get_value
from backend.modules.training import envs, notebooks, projects
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
    _project_or_404(project_id)
    deleted = await asyncio.to_thread(projects.delete_project, project_id)
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
async def run_list() -> dict:
    from backend.modules.training.runners.script_runner import script_runner

    return {"runs": script_runner.status()}


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


# --- peer fabric ---------------------------------------------------------------


@router.get("/fabric/ads")
async def fabric_ads() -> dict:
    from backend.modules.training import fabric

    return {"ads": [ad.model_dump() for ad in fabric.known_ads()]}


@router.post("/fabric/advertise")
async def fabric_advertise(body: dict) -> dict:
    """Set this node's advertise mode (off|offering|seeking) + note, persist it to
    settings, and re-broadcast the ad to peers."""
    from backend.modules.network.hub import peer_hub
    from backend.modules.settings.routes import set_value
    from backend.modules.training import fabric

    status = str(body.get("status", "off"))
    if status not in ("off", "offering", "seeking"):
        raise HTTPException(
            status_code=400, detail="status must be off|offering|seeking"
        )
    set_value("training.fabric.advertise", status)
    if "note" in body:
        set_value("training.fabric.note", str(body.get("note", "")))
    await fabric.broadcast_ad(peer_hub)
    return {"status": status}


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

    threading.Thread(target=work, daemon=True, name=f"venv-{project.id}").start()


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

    threading.Thread(target=work, daemon=True, name=f"deps-{project.id}").start()


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

    threading.Thread(target=work, daemon=True, name=f"fetch-{project.id}").start()

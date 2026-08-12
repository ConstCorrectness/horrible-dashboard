"""HTTP surface for the MCP module: configure servers, connect them, inspect them.

Mounted at `/api/mcp`. Every mutation that changes what's connected re-syncs the agent
bridge, so the tool catalog the model sees never lags what the pane shows.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from backend.modules.mcp import config as cfg
from backend.modules.mcp.client import manager
from backend.modules.mcp.models import (
    CallToolRequest,
    CallToolResponse,
    ConformanceResponse,
    CostResponse,
    DiscoverResponse,
    ExportStatus,
    ExportTokenResponse,
    FileResponse,
    FileWriteRequest,
    ProbeResponse,
    ProjectInput,
    ProjectListResponse,
    ProjectModel,
    ReadResourceRequest,
    ResourceContentResponse,
    ServerInput,
    ServerListResponse,
    ServerStatus,
    TranscriptResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp", tags=["mcp"])


def _statuses() -> list[ServerStatus]:
    return [ServerStatus(**r.public()) for r in manager.runtimes()]


@router.get("/servers", response_model=ServerListResponse)
def list_servers() -> ServerListResponse:
    """Every configured server with its live state. Never includes a token."""
    return ServerListResponse(servers=_statuses())


@router.post("/servers", response_model=ServerStatus)
async def upsert_server(payload: ServerInput) -> ServerStatus:
    """Add or update a server, then (re)connect it if enabled."""
    config = payload.model_dump(exclude={"token", "secretEnvValues"})
    if err := cfg.validate(config):
        raise HTTPException(status_code=400, detail=err)

    cfg.save_server(config)
    # The token goes to the encrypted store, never into mcp-servers.json. A blank
    # token means "leave whatever is stored alone" — the UI can't prefill it, so
    # blank cannot mean "clear it".
    if payload.token:
        cfg.set_auth_token(payload.id, payload.token)
    # Same rule for environment secrets, and the same reason: `env` is persisted in
    # the clear, so a value submitted here must never reach `config`.
    for name, value in payload.secretEnvValues.items():
        if value:
            cfg.set_env_secret(payload.id, name, value)

    if payload.enabled:
        await manager.start_server(payload.id)
    else:
        await manager.stop_server(payload.id)

    status = next((s for s in _statuses() if s.id == payload.id), None)
    if status is None:
        raise HTTPException(status_code=500, detail="server vanished after save")
    return status


@router.delete("/servers/{server_id}", response_model=ServerListResponse)
async def delete_server(server_id: str) -> ServerListResponse:
    """Disconnect and forget a server, including its stored token."""
    from backend.modules.mcp import transcript

    await manager.stop_server(server_id)
    if not cfg.delete_server(server_id):
        raise HTTPException(status_code=404, detail=f"no MCP server '{server_id}'")
    # The ring outlives a reconnect, but not the server itself — otherwise a later
    # server reusing the id inherits someone else's conversation.
    transcript.forget(server_id)
    return ServerListResponse(servers=_statuses())


@router.post("/servers/{server_id}/connect", response_model=ServerStatus)
async def connect_server(server_id: str) -> ServerStatus:
    """(Re)connect one server — the retry button after a failure."""
    if cfg.get_server(server_id) is None:
        raise HTTPException(status_code=404, detail=f"no MCP server '{server_id}'")
    await manager.start_server(server_id)
    status = next((s for s in _statuses() if s.id == server_id), None)
    if status is None:
        raise HTTPException(status_code=500, detail="server vanished after connect")
    return status


@router.post("/servers/{server_id}/disconnect", response_model=ServerStatus)
async def disconnect_server(server_id: str) -> ServerStatus:
    if cfg.get_server(server_id) is None:
        raise HTTPException(status_code=404, detail=f"no MCP server '{server_id}'")
    await manager.stop_server(server_id)
    status = next((s for s in _statuses() if s.id == server_id), None)
    if status is None:
        raise HTTPException(status_code=500, detail="server vanished after disconnect")
    return status


@router.get("/servers/{server_id}/transcript", response_model=TranscriptResponse)
def server_transcript(server_id: str) -> TranscriptResponse:
    """The recent JSON-RPC conversation with one server.

    Survives a reconnect on purpose: the handshake of the attempt that *failed* is
    usually what you came to read, and clearing it on retry would delete the evidence
    at the moment the user goes looking.
    """
    from backend.modules.mcp import transcript

    return TranscriptResponse(messages=transcript.for_server(server_id).public())


@router.delete("/servers/{server_id}/transcript", response_model=TranscriptResponse)
def clear_transcript(server_id: str) -> TranscriptResponse:
    from backend.modules.mcp import transcript

    transcript.for_server(server_id).clear()
    return TranscriptResponse(messages=[])


@router.get("/servers/{server_id}/cost", response_model=CostResponse)
async def server_cost(server_id: str) -> CostResponse:
    """What this server costs the model, and which agents can load it."""
    from backend.modules.mcp import bridge

    session = manager.get(server_id)
    if session is None or session.runtime.state != "ready":
        raise HTTPException(
            status_code=409, detail=f"MCP server '{server_id}' is not connected"
        )
    report = await bridge.context_cost(session.runtime)
    return CostResponse(
        **report, agents=bridge.agents_with_group(session.runtime.group)
    )


@router.get("/discover", response_model=DiscoverResponse)
async def discover(q: str = "", limit: int = 30) -> DiscoverResponse:
    """Browse the official registry, with our curated overlay in front of it."""
    from backend.modules.mcp import catalog

    live = await catalog.search_registry(q, limit=limit)
    curated = [e for e in catalog.curated_entries() if catalog.matches(e, q)]
    entries = catalog.merge(curated, live)
    return DiscoverResponse(
        entries=[e.public() for e in entries],
        # An empty live list with a non-empty overlay is a *degraded* result, not an
        # empty one, and the pane says which so "the registry is down" doesn't read as
        # "nothing matched".
        registryOnline=bool(live),
    )


@router.post("/probe", response_model=ProbeResponse)
async def probe(payload: ServerInput) -> ProbeResponse:
    """Connect a candidate server once, report what it really is, throw it away.

    The point of the exercise: a registry entry's description is written by its
    publisher, while this is the server's own tool list, annotations and instructions,
    read from the running thing. It never touches `mcp-servers.json`, never registers
    an agent tool, and gets its own transcript ring so a candidate's handshake can't
    land in a configured server's history.

    It does **run the server** — for a stdio package that means fetching and executing
    third-party code on this machine. That is the same act as adding it, minus the
    persistence, and it happens only on an explicit request.
    """
    from backend.modules.mcp import transcript
    from backend.modules.mcp.client import McpSession

    config = payload.model_dump(exclude={"token", "secretEnv"})
    # A probe is not persisted, so its secrets live only in this in-memory config —
    # which is precisely why they may be plain `env` here and must not be when saved.
    config["env"] = {**(config.get("env") or {}), **payload.secretEnvValues}
    config.pop("secretEnvValues", None)
    if err := cfg.validate(config):
        raise HTTPException(status_code=400, detail=err)

    wire = transcript.Transcript()
    session = McpSession(config, wire=wire)
    try:
        await session.start()
        runtime = session.runtime
        if runtime.state != "ready":
            return ProbeResponse(
                ok=False,
                error=runtime.error or "server did not become ready",
                messages=wire.public(),
            )
        return ProbeResponse(
            ok=True,
            serverName=runtime.server_name,
            serverVersion=runtime.server_version,
            instructions=runtime.instructions,
            tools=[
                {
                    "name": t.name,
                    "description": t.description,
                    "readOnly": t.read_only,
                    "destructive": t.destructive,
                }
                for t in runtime.tools
            ],
            prompts=[
                {"name": p.name, "description": p.description} for p in runtime.prompts
            ],
            resources=[
                {"uri": r.uri, "name": r.name, "description": r.description}
                for r in runtime.resources
            ],
            messages=wire.public(),
        )
    finally:
        # Always — a probe that leaves an npx process running is a leak the user has
        # no UI to find.
        await session.stop()


@router.get("/export", response_model=ExportStatus)
def export_status() -> ExportStatus:
    """Status of the MCP server this node *exports* (the other direction).

    Returns whether a token exists, never the token — `POST /export/token` reveals it
    once, on explicit request.
    """
    from backend.modules.mcp import server as export

    return ExportStatus(
        enabled=export.is_enabled(),
        mountPath=export.MOUNT_PATH,
        enableEnv=export.ENABLE_ENV,
        hasToken=export.get_token() is not None,
        exposeContent=export.expose_content(),
    )


@router.post("/export/token", response_model=ExportTokenResponse)
def export_token(rotate: bool = False) -> ExportTokenResponse:
    """Reveal (or rotate) the exported server's bearer token.

    A deliberate, explicit action rather than a field on the status payload: the token
    grants read access to this node's trajectories and telemetry, so it should appear
    only when the user asks to see it.
    """
    from backend.modules.database.secrets_store import delete_secret

    from backend.modules.mcp import server as export

    if rotate:
        delete_secret(export.TOKEN_SECRET_KEY)
    return ExportTokenResponse(token=export.ensure_token(), mountPath=export.MOUNT_PATH)


@router.post("/servers/{server_id}/resource", response_model=ResourceContentResponse)
async def read_resource(
    server_id: str, payload: ReadResourceRequest
) -> ResourceContentResponse:
    """Read one resource by URI — the pane's preview, and phase 2's ingestion path."""
    session = manager.get(server_id)
    if session is None:
        raise HTTPException(
            status_code=404, detail=f"MCP server '{server_id}' not connected"
        )
    result = await session.read_resource(payload.uri)
    return ResourceContentResponse(
        contents=result.get("contents", []), error=result.get("error")
    )


# --- inspecting a server by hand ----------------------------------------------


@router.post("/servers/{server_id}/call", response_model=CallToolResponse)
async def call_tool(server_id: str, payload: CallToolRequest) -> CallToolResponse:
    """Invoke one tool directly — the same call the agent would make, minus the model.

    This is the shortest path between "the server says it has this tool" and "the tool
    does what I think": no turn, no permission gate, no prompt to get right first. It
    deliberately runs the call through `McpSession.call_tool`, the same entry point the
    bridge uses, so what you see here is what the model would get — including the
    flattening, which is itself frequently the thing that's wrong.

    It is **not** gated on `readOnlyHint`. The gate exists to stop a *model* taking an
    action the user didn't ask for; here the user is the one pressing the button, and
    refusing to run their own write tool would make the pane useless for the case it
    was built for.
    """
    import time

    session = manager.get(server_id)
    if session is None or session.runtime.state != "ready":
        raise HTTPException(
            status_code=409, detail=f"MCP server '{server_id}' is not connected"
        )
    started = time.monotonic()
    result = await session.call_tool(payload.name, payload.arguments)
    elapsed = int((time.monotonic() - started) * 1000)
    return CallToolResponse(
        content=str(result.get("content") or ""),
        structured=result.get("structured"),
        attachments=[str(a) for a in result.get("attachments") or []],
        error=result.get("error"),
        elapsedMs=elapsed,
    )


@router.post("/servers/{server_id}/conformance", response_model=ConformanceResponse)
async def run_conformance(server_id: str) -> ConformanceResponse:
    """Check a connected server against the protocol's expectations.

    See `conformance.py` for what this can and cannot establish: it checks the server's
    declarations for coherence and its edges for sane behaviour, never whether a
    `readOnlyHint` is telling the truth.
    """
    from backend.modules.mcp import conformance

    session = manager.get(server_id)
    if session is None or session.runtime.state != "ready":
        raise HTTPException(
            status_code=409, detail=f"MCP server '{server_id}' is not connected"
        )
    return ConformanceResponse(**await conformance.run(session))


# --- authoring ----------------------------------------------------------------


@router.get("/projects", response_model=ProjectListResponse)
def list_projects() -> ProjectListResponse:
    """Every scaffolded server project, plus whether this machine can provision one."""
    from backend.modules.mcp import author

    return ProjectListResponse(
        projects=[ProjectModel(**p.public()) for p in author.list_projects()],
        **author.toolchains(),
    )


def _project_or_404(project_id: str):
    from backend.modules.mcp import author

    project = author.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"no MCP project '{project_id}'")
    return project


@router.get("/projects/{project_id}", response_model=ProjectModel)
def get_project(project_id: str) -> ProjectModel:
    return ProjectModel(**_project_or_404(project_id).public())


@router.post("/projects", response_model=ProjectModel)
def create_project(payload: ProjectInput) -> ProjectModel:
    """Scaffold a project and register its server, disabled until it's provisioned."""
    from backend.modules.mcp import author

    project, err = author.create_project(payload.id, payload.template, payload.title)
    if err or project is None:
        raise HTTPException(status_code=400, detail=err or "could not create project")
    return ProjectModel(**project.public())


@router.post("/projects/{project_id}/provision", response_model=ProjectModel)
async def provision_project(project_id: str) -> ProjectModel:
    """Build the project's runtime (uv venv / npm install), then enable its server.

    Synchronous on purpose despite taking tens of seconds: it is one explicit button
    press whose whole output the user wants to read, and a fire-and-forget version
    would need a second channel to report the failure that actually matters here — the
    one where `uv` isn't installed.
    """
    from backend.modules.mcp import author

    project = _project_or_404(project_id)
    await author.provision(project)
    return ProjectModel(**project.public())


@router.delete("/projects/{project_id}", response_model=ProjectListResponse)
async def delete_project(
    project_id: str, deleteFiles: bool = False
) -> ProjectListResponse:
    """Unregister a project. Its source is kept unless `deleteFiles` says otherwise.

    Defaulting to keeping the files is the whole point: this is code the user wrote,
    and "Remove" on a list row is not consent to delete a source tree.
    """
    from backend.modules.mcp import author

    await manager.stop_server(project_id)
    if not author.delete_project(project_id, delete_files=deleteFiles):
        raise HTTPException(status_code=404, detail=f"no MCP project '{project_id}'")
    return ProjectListResponse(
        projects=[ProjectModel(**p.public()) for p in author.list_projects()],
        **author.toolchains(),
    )


@router.post("/projects/{project_id}/register", response_model=ProjectModel)
def register_project(project_id: str) -> ProjectModel:
    """Add an unregistered project back to the server list."""
    from backend.modules.mcp import author

    project = _project_or_404(project_id)
    author.register(project)
    return ProjectModel(**project.public())


@router.get("/projects/{project_id}/file", response_model=FileResponse)
def read_project_file(project_id: str, path: str) -> FileResponse:
    from backend.modules.mcp import author

    project = _project_or_404(project_id)
    try:
        return FileResponse(path=path, text=author.read_file(project, path))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/projects/{project_id}/file", response_model=FileResponse)
async def write_project_file(
    project_id: str, payload: FileWriteRequest
) -> FileResponse:
    """Save a file and, if it's part of the running server, restart it.

    The restart is the edit loop. MCP gives a client no way to ask a server to re-read
    its own source, so "hot reload" here is an honest process restart — after which the
    bridge re-syncs from live state and the agent's catalog reflects the edit.
    """
    from backend.modules.mcp import author

    project = _project_or_404(project_id)
    try:
        author.write_file(project, payload.path, payload.text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    restarted = False
    restart_error: str | None = None
    if payload.restart and author.touches_runtime(project, payload.path):
        restart_error = await author.restart(project)
        restarted = restart_error is None
    return FileResponse(
        path=payload.path,
        text=payload.text,
        restarted=restarted,
        restartError=restart_error,
    )


@router.post("/projects/{project_id}/restart", response_model=ServerStatus)
async def restart_project(project_id: str) -> ServerStatus:
    """Restart an authored server without touching a file."""
    from backend.modules.mcp import author

    project = _project_or_404(project_id)
    await author.restart(project)
    status = next((s for s in _statuses() if s.id == project.id), None)
    if status is None:
        raise HTTPException(status_code=404, detail=f"no MCP server '{project_id}'")
    return status

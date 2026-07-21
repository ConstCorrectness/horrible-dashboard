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
    ExportStatus,
    ExportTokenResponse,
    ReadResourceRequest,
    ResourceContentResponse,
    ServerInput,
    ServerListResponse,
    ServerStatus,
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
    config = payload.model_dump(exclude={"token"})
    if err := cfg.validate(config):
        raise HTTPException(status_code=400, detail=err)

    cfg.save_server(config)
    # The token goes to the encrypted store, never into mcp-servers.json. A blank
    # token means "leave whatever is stored alone" — the UI can't prefill it, so
    # blank cannot mean "clear it".
    if payload.token:
        cfg.set_auth_token(payload.id, payload.token)

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
    await manager.stop_server(server_id)
    if not cfg.delete_server(server_id):
        raise HTTPException(status_code=404, detail=f"no MCP server '{server_id}'")
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

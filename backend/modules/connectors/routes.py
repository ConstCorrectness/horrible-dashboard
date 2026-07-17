"""The `/api/connectors` surface: what's connectable, what's connected, and the
begin/submit/poll machine that moves between the two.

Connectors are contributed through `backend.sdk` (`host.add_connector`), so built-in
modules and backend plugins register the same way. This router just projects the
registry and dispatches to a connector's own callbacks.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from backend.modules.connectors.models import (
    AccountModel,
    ConnectorListModel,
    ConnectorModel,
    ConnectRequest,
    ScopeModel,
    StepModel,
    SubmitRequest,
)
from backend.sdk.registry import registry
from backend.sdk.types import Connector

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/connectors", tags=["connectors"])


def _get(connector_id: str) -> Connector:
    connector = registry.connectors.get(connector_id)
    if connector is None:
        raise HTTPException(
            status_code=404, detail=f"unknown connector {connector_id!r}"
        )
    return connector


async def _call(fn: Any, *args: Any) -> dict[str, Any]:
    """Invoke a connector callback, awaiting it if async. A failing connector returns
    an `{error}` step rather than a 500 — one broken integration must not take the
    home page's tile row down with it."""
    try:
        result = fn(*args)
        if inspect.isawaitable(result):
            result = await result
        return result if isinstance(result, dict) else {}
    except Exception as exc:  # noqa: BLE001 — connector failures are values, not crashes
        logger.exception("connector callback failed")
        return {"error": f"{type(exc).__name__}: {exc}"}


def _describe(connector: Connector) -> ConnectorModel:
    """Project one connector + its live status into a tile."""
    try:
        status = connector.status()
    except Exception as exc:  # noqa: BLE001 — a broken status must not hide the tile
        logger.exception("connector %s status failed", connector.id)
        return ConnectorModel(
            id=connector.id,
            label=connector.label,
            kind=connector.kind,
            icon=connector.icon,
            blurb=connector.blurb,
            connected=False,
            error=f"{type(exc).__name__}: {exc}",
        )
    return ConnectorModel(
        id=connector.id,
        label=connector.label,
        kind=connector.kind,
        icon=connector.icon,
        blurb=connector.blurb,
        connected=status.connected,
        account=AccountModel(
            id=status.account.id,
            label=status.account.label,
            avatar_url=status.account.avatar_url,
        )
        if status.account
        else None,
        scopes=[
            ScopeModel(id=s.id, label=s.label, description=s.description)
            for s in connector.scopes
        ],
        granted_scopes=list(status.scopes),
        error=status.error,
    )


@router.get("", response_model=ConnectorListModel)
def list_connectors() -> ConnectorListModel:
    """Every registered connector with its current state — the home tile row."""
    return ConnectorListModel(
        connectors=[
            _describe(c)
            for c in sorted(registry.connectors.values(), key=lambda c: c.label)
        ]
    )


@router.post("/{connector_id}/connect", response_model=StepModel)
async def connect(connector_id: str, body: ConnectRequest | None = None) -> StepModel:
    """Start a connect flow. Returns the first step (device code, authorize URL, or a
    form to fill)."""
    connector = _get(connector_id)
    options = body.options if body else {}
    return StepModel.from_result(await _call(connector.begin, options))


@router.post("/{connector_id}/submit", response_model=StepModel)
async def submit(connector_id: str, body: SubmitRequest) -> StepModel:
    """Answer a `form` step. May return another `form` step (Clubhouse: phone → code)
    or a terminal result."""
    connector = _get(connector_id)
    if connector.submit is None:
        raise HTTPException(
            status_code=400, detail=f"connector {connector_id!r} takes no form input"
        )
    return StepModel.from_result(await _call(connector.submit, body.values))


@router.post("/{connector_id}/poll", response_model=StepModel)
async def poll(connector_id: str) -> StepModel:
    """Check an in-flight flow. `{pending: true}` until the user finishes authorizing."""
    connector = _get(connector_id)
    if connector.poll is None:
        raise HTTPException(
            status_code=400, detail=f"connector {connector_id!r} is not pollable"
        )
    return StepModel.from_result(await _call(connector.poll))


@router.delete("/{connector_id}", response_model=ConnectorModel)
async def disconnect(connector_id: str) -> ConnectorModel:
    """Drop the stored credential. Idempotent — disconnecting an unconnected
    connector is a no-op, not an error."""
    connector = _get(connector_id)
    await _call(connector.disconnect)
    return _describe(connector)


# The page a provider redirects back to after consent. Deliberately terminal: it
# returns HTML that tells the user to close the tab, and does NOT redirect into the
# app. That keeps the app's origin out of the OAuth loop entirely, so dev (behind the
# Vite proxy), prod, and Tauri all register one identical redirect URI — and the token
# never leaves the process that minted it. The app tab polls instead.
_CALLBACK_HTML = """<!doctype html>
<meta charset="utf-8">
<title>{title}</title>
<style>
  body {{ font: 15px/1.5 system-ui, sans-serif; background: #14110d; color: #fafafa;
         display: grid; place-items: center; height: 100vh; margin: 0; }}
  .card {{ text-align: center; }}
  .muted {{ color: rgba(250, 250, 250, 0.55); }}
</style>
<div class="card">
  <h1>{heading}</h1>
  <p class="muted">{body}</p>
</div>
"""


def _callback_page(heading: str, body: str, *, status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(
        _CALLBACK_HTML.format(title=heading, heading=heading, body=body),
        status_code=status_code,
    )


@router.get("/{connector_id}/callback")
async def callback(connector_id: str, code: str = "", state: str = "") -> HTMLResponse:
    """The provider's redirect target for redirect-style OAuth connectors."""
    connector = registry.connectors.get(connector_id)
    if connector is None or connector.poll is None:
        return _callback_page(
            "Unknown connector",
            f"No connector {connector_id!r} is expecting a sign-in.",
            status_code=404,
        )

    from backend.modules.connectors import oauth

    result = await oauth.finish_redirect(connector_id, code=code, state=state)
    if result.get("error"):
        return _callback_page("Couldn't connect", str(result["error"]), status_code=400)
    return _callback_page("Connected", "You can close this tab and return to the app.")

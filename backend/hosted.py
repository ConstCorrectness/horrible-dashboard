"""Running as one user's instance behind the hosted hub.

A local backend is private because it binds 127.0.0.1 — nothing on `/api` or `/ws`
asks who is calling. Hosted, the same process runs in a per-user container that the
hub (`backend/hub/`) reverse-proxies to, and the container's port is reachable from
the hub's internal network. Two environment variables turn that into a safe
instance; with both unset, nothing here does anything, so `pnpm dev` and the
desktop shell are unaffected.

- ``HORRIBLE_INSTANCE_TOKEN`` — every HTTP request and WebSocket upgrade must carry
  it in ``X-Horrible-Instance-Token``. The hub generates one per container and adds
  the header on every proxied request; anything else reaching the port gets a 401
  (HTTP) or a refused upgrade (WS), before any route or ``accept()`` runs.
- ``HORRIBLE_PROFILE=hosted`` — drop the modules that drive this machine's desktop
  (the Clubhouse .NET helper) and report the profile on
  ``GET /api/host`` so the frontend can hide what isn't there.

The hub also forwards who the user is, in ``X-Horrible-User`` (JSON
``{"id", "handle"}``). It is trusted only because the token check passed first —
see :func:`hub_user`.
"""

from __future__ import annotations

import hmac
import json
import os
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

TOKEN_HEADER = b"x-horrible-instance-token"
USER_HEADER = b"x-horrible-user"


def instance_token() -> str | None:
    """The token this instance requires, or None when it isn't behind a hub."""
    return os.environ.get("HORRIBLE_INSTANCE_TOKEN") or None


def profile() -> str:
    """``hosted`` behind the hub, ``local`` everywhere else."""
    return "hosted" if os.environ.get("HORRIBLE_PROFILE") == "hosted" else "local"


def is_hosted() -> bool:
    return profile() == "hosted"


def _header(scope: dict[str, Any], name: bytes) -> bytes | None:
    for key, value in scope.get("headers") or ():
        if key.lower() == name:
            return value
    return None


def token_matches(presented: bytes | None, expected: str) -> bool:
    if presented is None:
        return False
    # Constant-time: the comparison must not leak how many leading bytes matched.
    return hmac.compare_digest(presented, expected.encode())


def hub_user(scope: dict[str, Any]) -> dict[str, Any] | None:
    """The user the hub says this request is for. Only meaningful once
    :class:`InstanceGate` has admitted the request; ``None`` when absent or
    malformed."""
    raw = _header(scope, USER_HEADER)
    if not raw:
        return None
    try:
        data = json.loads(raw.decode())
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


class InstanceGate:
    """Pure-ASGI gate on the instance token.

    Pure ASGI rather than ``@app.middleware("http")`` so WebSocket upgrades are
    covered too: an upgrade refused here never reaches ``websocket.accept()``, and
    Starlette turns a close-before-accept into an HTTP 403 for the client. Lifespan
    events pass straight through. The token is read per request so a test can set
    it with ``monkeypatch.setenv`` against the shared module-level app.
    """

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        kind = scope.get("type")
        expected = instance_token()
        if expected is None or kind not in ("http", "websocket"):
            await self.inner(scope, receive, send)
            return
        if token_matches(_header(scope, TOKEN_HEADER), expected):
            await self.inner(scope, receive, send)
            return
        if kind == "websocket":
            await send({"type": "websocket.close", "code": 4401})
            return
        body = b'{"detail":"instance token required"}'
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


# ---- hub → instance control -------------------------------------------------
#
# Mounted only on a hosted instance, under `/api/hosted`. The hub's proxy refuses
# this prefix from browsers (`backend/hub/proxy.py`), so in practice only the hub
# calls it — though nothing here would matter if a user did: it is their own
# sandbox, and they could write the same file from its terminal.

router = APIRouter(prefix="/hosted", tags=["hosted"])


class GamesSession(BaseModel):
    token: str
    account: dict[str, Any]


@router.put("/games-session")
def put_games_session(body: GamesSession) -> dict[str, bool]:
    """Sign this instance's games module in as the hub's user.

    The hub signed the user in to the game server to let them in at all; handing the
    instance the same JWT means Plaza and games work without a second sign-in. Written
    where `games.server_auth` already reads it, without the machine-enrollment side
    effect a node sign-in has — a hosted instance is not one of the user's machines.
    """
    from backend import jsonstore
    from backend.modules.games import server_auth

    jsonstore.write_text(server_auth._token_path(), json.dumps(body.model_dump()))
    return {"ok": True}

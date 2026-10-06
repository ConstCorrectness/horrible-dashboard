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


# ---- is this instance doing work? ----------------------------------------------
#
# The hub stops an instance once its user has given no input for a while — an open
# tab is not use (it polls /api/health every 10 s whether anyone is there or not).
# But stopping the process kills whatever it is in the middle of, so before it does
# the hub asks here. Each probe reads in-memory state (or one small SQLite count)
# and is independent: a probe that fails is skipped, never the whole answer.


def _agent_turns() -> bool:
    from backend.modules.agent import orchestrator

    return bool(orchestrator._turns)


def _kernels_executing() -> bool:
    from backend.modules.notebook.manager import notebook_manager
    from backend.modules.scrive.kernel import scrive_kernels
    from backend.modules.training.kernels import training_kernels

    for manager in (notebook_manager, scrive_kernels, training_kernels):
        for session in list(manager.sessions.values()):
            if session.status == "busy" or session.exec_queue.qsize() > 0:
                return True
    return False


def _training_runs() -> bool:
    from backend.modules.training import sweeps
    from backend.modules.training.runners.script_runner import script_runner

    if any(run.running for run in list(script_runner.runs.values())):
        return True
    return any(
        record.get("state") in ("queued", "running", "stopping")
        for record in list(sweeps._sweeps.values())
    )


def _eval_sweeps() -> bool:
    from backend.modules.evals.sweep import active_sweeps

    return bool(active_sweeps())


def _doc_crawls() -> bool:
    from backend.modules.docviewer import crawl

    return any(not task.done() for task in list(crawl._running.values()))


def _research_runs() -> bool:
    from backend.modules.research import runstore

    return bool(runstore.list_resumable_runs())


def _queued_tasks() -> bool:
    # Library ingest, CLIP embedding, search crawls, Drive sync. Only rows touched
    # in the last hour count: nothing resets a `running` row after a crash, and a
    # stale one must not keep a machine up forever.
    from backend.modules.tasks.queue import _get_db_conn

    with _get_db_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM async_tasks WHERE status IN ('pending', 'running')"
            " AND updated_at > datetime('now', '-1 hour')"
        ).fetchone()
    return bool(row and row[0])


#: name → probe. Names are what the hub logs when it keeps an instance running.
BUSY_PROBES: dict[str, Any] = {
    "agent turn": _agent_turns,
    "kernel executing": _kernels_executing,
    "training run": _training_runs,
    "eval sweep": _eval_sweeps,
    "docs crawl": _doc_crawls,
    "research run": _research_runs,
    "background task": _queued_tasks,
}


def busy_reasons() -> list[str]:
    reasons = []
    for name, probe in BUSY_PROBES.items():
        try:
            if probe():
                reasons.append(name)
        except Exception:  # noqa: BLE001 - one broken probe must not hide the rest
            import logging

            logging.getLogger(__name__).debug("busy probe %s failed", name, exc_info=True)
    return reasons


@router.get("/busy")
async def get_busy() -> dict[str, Any]:
    """Whether stopping this instance now would cut work short, and what work."""
    import asyncio

    # Off the loop: two probes touch SQLite.
    reasons = await asyncio.to_thread(busy_reasons)
    return {"busy": bool(reasons), "reasons": reasons}

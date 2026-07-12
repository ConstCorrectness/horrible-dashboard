"""Standalone game-server app. Run separately from a node's own backend:

    uv run uvicorn backend.games_server.app:app --port 9200

A node connects one authenticated socket per account to `/game-ws` and speaks the
`{"type": ...}` protocol in `models.py`. The `GameHub` is process-global so every
connection shares the same lobby of tables.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, Header, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from backend.games_engine.base import list_games
from backend.games_server import auth, store
from backend.games_server.hub import GameHub

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    store.init_db()  # accounts + ratings + results + task bank
    from backend.games_server import task_bank

    task_bank.ensure_builtin()  # load the bundled bug-hunt starter set
    hub.town.start_loop()  # AgentTown's world clock (tick cadence from env)
    hub.matchmaker.start_loop()  # ranked queue sweep
    yield
    hub.matchmaker.stop_loop()
    hub.town.stop_loop()


app = FastAPI(title="horrible-dashboard game server", lifespan=_lifespan)

# One lobby shared by every connection to this process. (AgentTown's tick cadence
# is env-tunable via TOWN_TICK_SECONDS — see town.py.)
hub = GameHub()


@app.get("/health")
def health() -> dict[str, object]:
    return {"status": "ok", "games": [g.id for g in list_games()]}


@app.get("/games")
def games() -> dict[str, object]:
    return {
        "games": [
            {
                "id": g.id,
                "name": g.name,
                "min_players": g.min_players,
                "max_players": g.max_players,
            }
            for g in list_games()
        ]
    }


@app.get("/leaderboard")
def leaderboard(game_id: str = "tictactoe", limit: int = 50) -> dict[str, object]:
    return {"game_id": game_id, "entries": store.leaderboard(game_id, limit)}


@app.get("/challenges/leaderboard")
def challenge_leaderboard(
    game_id: str = "tictactoe", limit: int = 50
) -> dict[str, object]:
    return {"game_id": game_id, "entries": store.challenge_leaderboard(game_id, limit)}


# ---- replays ----------------------------------------------------------------


def _viewer(authorization: str | None) -> str | None:
    """Resolve an optional `Authorization: Bearer <token>` to an account id (JWT or
    dev token — same resolution as `/game-ws` auth)."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    resolved = auth.resolve_token(authorization[7:].strip())
    return resolved["account_id"] if resolved else None


@app.get("/replays")
def replays_index(
    game_id: str | None = None,
    scope: str = "public",
    limit: int = 50,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Replay summaries: `scope=public` is the public replay browser; `scope=mine`
    lists the caller's own matches (participants always see theirs)."""
    if scope == "mine":
        viewer = _viewer(authorization)
        if viewer is None:
            return {"replays": [], "error": "sign in required"}
        entries = store.list_replays(
            game_id=game_id, account_id=viewer, limit=min(limit, 200)
        )
    else:
        entries = store.list_replays(
            game_id=game_id, public_only=True, limit=min(limit, 200)
        )
    return {"replays": entries}


@app.get("/replays/{replay_id}")
def replay_get(
    replay_id: str, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    """One replay with its full event log — participants always; others only once
    published. Not-found and not-allowed are indistinguishable on purpose."""
    replay = store.get_replay(replay_id, viewer=_viewer(authorization))
    if replay is None:
        return {"error": "replay not found"}
    return {"replay": replay}


@app.post("/replays/{replay_id}/publish")
def replay_publish(
    replay_id: str, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    """Open a replay up to the public browser. Participants only."""
    viewer = _viewer(authorization)
    if viewer is None:
        return {"error": "sign in required"}
    if not store.publish_replay(replay_id, viewer):
        return {"error": "replay not found"}
    return {"ok": True}


class _DevicePoll(BaseModel):
    device_code: str


@app.post("/auth/github/start")
async def github_start() -> dict[str, Any]:
    """Begin GitHub device-flow sign-in. Returns the user_code + verification_uri the
    player enters at github.com."""
    try:
        return await auth.github_device_start()
    except ValueError as exc:
        return {"error": str(exc)}


@app.post("/auth/github/poll")
async def github_poll(body: _DevicePoll) -> dict[str, Any]:
    """Poll once for the token. `{pending: true}` until authorized, then `{token, account}`."""
    try:
        return await auth.github_device_poll(body.device_code)
    except Exception as exc:  # network / provider error — report, don't crash
        logger.warning("github poll failed: %s", exc)
        return {"error": str(exc)}


@app.post("/auth/google/start")
async def google_start() -> dict[str, Any]:
    """Begin Google device-flow sign-in (code entered at google.com/device)."""
    try:
        return await auth.google_device_start()
    except ValueError as exc:
        return {"error": str(exc)}


@app.post("/auth/google/poll")
async def google_poll(body: _DevicePoll) -> dict[str, Any]:
    """Poll once for the token. `{pending: true}` until authorized, then `{token, account}`."""
    try:
        return await auth.google_device_poll(body.device_code)
    except Exception as exc:  # network / provider error — report, don't crash
        logger.warning("google poll failed: %s", exc)
        return {"error": str(exc)}


@app.websocket("/game-ws")
async def game_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    session = hub.connect(websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if isinstance(msg, dict):
                await hub.handle(session, msg)
    except WebSocketDisconnect:
        pass
    finally:
        await hub.disconnect(session)

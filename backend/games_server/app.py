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

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from backend.games_engine.base import list_games
from backend.games_server import auth, store
from backend.games_server.hub import GameHub

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    store.init_db()  # accounts + ratings + results
    yield


app = FastAPI(title="horrible-dashboard game server", lifespan=_lifespan)

# One lobby shared by every connection to this process.
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

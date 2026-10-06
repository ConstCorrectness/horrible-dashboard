"""A fake upstream for the hub: one real HTTP server that plays both the game
server (`/auth/...`) and a user's instance (`/api/...`, `/ws`).

Real rather than an httpx mock because the hub's WebSocket proxy dials it with the
`websockets` client, and streaming should be exercised over an actual socket.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.testclient import TestClient

from backend.hub.app import create_app
from backend.hub.config import HubConfig
from backend.hub.spawner.static import StaticSpawner

INSTANCE_TOKEN = "instance-secret"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_upstream() -> tuple[FastAPI, dict[str, Any]]:
    seen: dict[str, Any] = {"games_session": None, "requests": []}
    app = FastAPI()

    # ---- game server ----------------------------------------------------------

    def _account(email: str) -> dict[str, Any]:
        handle = email.split("@")[0]
        return {"id": f"acc-{handle}", "handle": handle, "display_name": handle.title()}

    @app.post("/auth/local/login")
    async def login(body: dict[str, Any]) -> Any:
        if body.get("password") != "right":
            return JSONResponse({"error": "wrong email or password"}, status_code=401)
        return {"token": f"jwt-{body['email']}", "account": _account(body["email"])}

    @app.post("/auth/local/signup")
    async def signup(body: dict[str, Any]) -> Any:
        return {"token": f"jwt-{body['email']}", "account": _account(body["email"])}

    @app.get("/auth/providers")
    async def providers() -> dict[str, Any]:
        return {"github": {"device": True, "web": False}}

    # ---- instance -------------------------------------------------------------

    def _authorized(request: Request) -> bool:
        return request.headers.get("x-horrible-instance-token") == INSTANCE_TOKEN

    @app.get("/api/health")
    async def health(request: Request) -> Any:
        if not _authorized(request):
            return JSONResponse({}, status_code=401)
        return {"status": "ok"}

    @app.put("/api/hosted/games-session")
    async def games_session(request: Request) -> Any:
        if not _authorized(request):
            return JSONResponse({}, status_code=401)
        seen["games_session"] = await request.json()
        return {"ok": True}

    @app.api_route("/api/echo", methods=["GET", "POST"])
    async def echo(request: Request) -> Any:
        if not _authorized(request):
            return JSONResponse({}, status_code=401)
        body = await request.body()
        return {
            "method": request.method,
            "query": str(request.url.query),
            "user": request.headers.get("x-horrible-user"),
            "cookie": request.headers.get("cookie"),
            "body_len": len(body),
        }

    @app.get("/api/stream")
    async def stream() -> StreamingResponse:
        async def chunks():
            for i in range(5):
                yield f"chunk{i}\n".encode()
                await asyncio.sleep(0.01)

        res = StreamingResponse(chunks(), media_type="text/plain")
        res.set_cookie("hd_session", "evil")
        return res

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        if websocket.headers.get("x-horrible-instance-token") != INSTANCE_TOKEN:
            await websocket.close(code=4401)
            return
        await websocket.accept()
        await websocket.send_json(
            {"event": "hello", "user": websocket.headers.get("x-horrible-user")}
        )
        while True:
            msg = await websocket.receive()
            if msg["type"] == "websocket.disconnect":
                return
            if msg.get("text") is not None:
                await websocket.send_text("echo:" + msg["text"])
            else:
                await websocket.send_bytes(b"echo:" + msg["bytes"])

    return app, seen


class Upstream:
    def __init__(self) -> None:
        self.app, self.seen = make_upstream()
        self.port = _free_port()
        self.server = uvicorn.Server(
            uvicorn.Config(
                self.app, host="127.0.0.1", port=self.port, log_level="warning"
            )
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("upstream did not start")
            time.sleep(0.02)

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


@pytest.fixture
def upstream():
    up = Upstream()
    up.start()
    yield up
    up.stop()


@pytest.fixture
def make_hub(upstream, tmp_path, monkeypatch):
    """Build a hub TestClient against the fake upstream. Keyword overrides go to
    `HubConfig`."""
    monkeypatch.setenv("GAMES_SERVER_URL", upstream.url)
    clients: list[TestClient] = []

    def _make(**overrides: Any) -> TestClient:
        settings: dict[str, Any] = {
            "db_path": tmp_path / f"hub{len(clients)}.db",
            "web_dist": tmp_path / "no-dist",
            "cookie_secure": False,
            **overrides,
        }
        config = HubConfig(**settings)
        spawner = StaticSpawner(upstream.url, INSTANCE_TOKEN)
        client = TestClient(create_app(config, spawner))
        client.__enter__()
        clients.append(client)
        return client

    yield _make
    for client in clients:
        client.__exit__(None, None, None)


def sign_in(client: TestClient, email: str = "alice@example.com") -> dict[str, Any]:
    res = client.post(
        "/hub/auth/local/login", json={"email": email, "password": "right"}
    )
    assert res.status_code == 200
    return res.json()

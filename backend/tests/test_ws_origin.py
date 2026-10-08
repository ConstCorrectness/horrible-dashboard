"""The `/ws` Origin check (`backend/origins.py`): CORS does not cover WebSocket
upgrades, so without it any web page in the user's browser could open the
backend's socket and drive the agent, terminals and files."""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend import origins
from backend.app import app


@pytest.fixture
def client(monkeypatch) -> TestClient:
    monkeypatch.delenv("HORRIBLE_INSTANCE_TOKEN", raising=False)
    return TestClient(app)


@pytest.mark.parametrize("origin", origins.UI_ORIGINS)
def test_ui_origins_connect(client, origin) -> None:
    with client.websocket_connect("/ws", headers={"origin": origin}) as ws:
        assert ws.receive_json()["event"] == "hello"


def test_no_origin_connects(client) -> None:
    # Tests, the CLI/SDK and the hosted hub's proxy send none.
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["event"] == "hello"


def test_same_origin_connects(client) -> None:
    # The UI opened at 127.0.0.1 through the Vite proxy, which forwards Host as is.
    # (websocket_connect ignores base_url, so Host is set by hand.)
    with client.websocket_connect(
        "/ws", headers={"origin": "http://127.0.0.1:5173", "host": "127.0.0.1:5173"}
    ) as ws:
        assert ws.receive_json()["event"] == "hello"


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "http://localhost:3000",  # another local dev server
        "http://localhost:5173.evil.example",
        "null",  # sandboxed iframe / file:
    ],
)
def test_foreign_origin_is_refused(client, origin) -> None:
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws", headers={"origin": origin}) as ws:
            ws.receive_json()
    assert exc.value.code == 1008


def test_cors_uses_the_same_list(client) -> None:
    res = client.options(
        "/api/health",
        headers={
            "origin": "http://tauri.localhost",
            "access-control-request-method": "GET",
        },
    )
    assert res.headers["access-control-allow-origin"] == "http://tauri.localhost"
    res = client.options(
        "/api/health",
        headers={
            "origin": "https://evil.example",
            "access-control-request-method": "GET",
        },
    )
    assert "access-control-allow-origin" not in res.headers


@pytest.mark.parametrize(
    ("origin", "host", "allowed"),
    [
        ("http://127.0.0.1:5173", "127.0.0.1:5173", True),
        ("http://[::1]:5173", "[::1]:5173", True),
        ("http://192.168.1.20:5173", "192.168.1.20:5173", True),  # pnpm dev:lan
        ("http://LOCALHOST:5180", "localhost:5180", True),
        # DNS rebinding: the attacker's name resolves to loopback, so Origin and
        # Host agree — but neither is a literal.
        ("http://rebind.evil.example:8100", "rebind.evil.example:8100", False),
        # The Scrive apps origin is a .localhost name, never same-origin here.
        ("http://scrive-apps.localhost:8100", "scrive-apps.localhost:8100", False),
        ("http://127.0.0.1:5173", "127.0.0.1:8100", False),  # port differs
        ("ftp://127.0.0.1:8100", "127.0.0.1:8100", False),
        ("http://127.0.0.1:8100", None, False),
    ],
)
def test_ws_origin_allowed(origin, host, allowed) -> None:
    assert origins.ws_origin_allowed(origin, host) is allowed

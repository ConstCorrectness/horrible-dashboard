"""The hosted-instance gate (`backend/hosted.py`): a per-user container behind the
hub answers only requests carrying the hub's instance token, and drops the modules
that drive a desktop it doesn't have."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend import hosted
from backend.app import app

TOKEN = "t0ken-for-tests"
HEADER = {"X-Horrible-Instance-Token": TOKEN}


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_unset_token_leaves_the_backend_open(client, monkeypatch) -> None:
    monkeypatch.delenv("HORRIBLE_INSTANCE_TOKEN", raising=False)
    assert client.get("/api/health").status_code == 200
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["event"] == "hello"


def test_http_without_token_is_refused(client, monkeypatch) -> None:
    monkeypatch.setenv("HORRIBLE_INSTANCE_TOKEN", TOKEN)
    res = client.get("/api/health")
    assert res.status_code == 401
    assert res.json() == {"detail": "instance token required"}
    # A route that would write is refused the same way, before it runs.
    assert client.put("/api/settings/x", json={"value": 1}).status_code == 401


def test_http_with_wrong_token_is_refused(client, monkeypatch) -> None:
    monkeypatch.setenv("HORRIBLE_INSTANCE_TOKEN", TOKEN)
    res = client.get("/api/health", headers={"X-Horrible-Instance-Token": "nope"})
    assert res.status_code == 401


def test_http_with_token_passes(client, monkeypatch) -> None:
    monkeypatch.setenv("HORRIBLE_INSTANCE_TOKEN", TOKEN)
    assert client.get("/api/health", headers=HEADER).status_code == 200


@pytest.mark.parametrize("path", ["/ws", "/peer-ws"])
def test_ws_without_token_is_refused_before_accept(client, monkeypatch, path) -> None:
    monkeypatch.setenv("HORRIBLE_INSTANCE_TOKEN", TOKEN)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(path) as ws:
            ws.receive_json()


def test_ws_with_token_connects(client, monkeypatch) -> None:
    monkeypatch.setenv("HORRIBLE_INSTANCE_TOKEN", TOKEN)
    with client.websocket_connect("/ws", headers=HEADER) as ws:
        assert ws.receive_json()["event"] == "hello"


def test_host_reports_profile_and_hub_user(client, monkeypatch) -> None:
    monkeypatch.setenv("HORRIBLE_INSTANCE_TOKEN", TOKEN)
    monkeypatch.setenv("HORRIBLE_PROFILE", "hosted")
    user = {"id": "acc_1", "handle": "alice"}
    res = client.get(
        "/api/host", headers={**HEADER, "X-Horrible-User": json.dumps(user)}
    )
    assert res.json() == {"profile": "hosted", "account": user}


def test_host_is_local_by_default(client, monkeypatch) -> None:
    monkeypatch.delenv("HORRIBLE_INSTANCE_TOKEN", raising=False)
    monkeypatch.delenv("HORRIBLE_PROFILE", raising=False)
    assert client.get("/api/host").json() == {"profile": "local", "account": None}


def test_malformed_hub_user_is_none() -> None:
    scope = {"headers": [(b"x-horrible-user", b"{not json")]}
    assert hosted.hub_user(scope) is None


@pytest.mark.timeout(120)
def test_hosted_profile_drops_desktop_modules(tmp_path) -> None:
    """Router registration happens at import, so the profile is checked in a fresh
    interpreter rather than by re-importing the shared app."""
    repo = Path(__file__).resolve().parents[2]
    script = (
        "from backend.app import app\n"
        "paths = sorted({getattr(r, 'path', '') for r in app.routes})\n"
        "print('\\n'.join(paths))\n"
    )
    env = {
        **os.environ,
        "HORRIBLE_PROFILE": "hosted",
        "HORRIBLE_DATA_DIR": str(tmp_path),
        "HORRIBLE_ENABLE_SERVER_BROWSER": "0",
    }
    out = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=110,
        check=True,
    ).stdout
    routes = out.split()
    assert "/api/host" in routes
    assert "/api/hosted/games-session" in routes
    # The mixer's saved routing is used hosted too (its Voicemeeter half no-ops).
    assert any(p.startswith("/api/audio/mixer") for p in routes)
    assert not any(p.startswith("/api/clubhouse") for p in routes)
    # Everything a hosted user does use stays.
    assert any(p.startswith("/api/settings") for p in routes)
    assert any(p.startswith("/api/files") for p in routes)

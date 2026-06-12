import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.agent import routes


class _UnreachableClient:
    """Stand-in for the Ollama HTTP client: never opens a real connection.

    Avoids real network I/O in unit tests — and avoids constructing a real httpx
    client, whose SSL-context init aborts the process when a MinGW OpenSSL is on
    PATH (see memory: openssl-applink-mingw-path).
    """

    def __init__(self, *args, **kwargs) -> None: ...

    async def __aenter__(self) -> "_UnreachableClient":
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def get(self, *args, **kwargs) -> httpx.Response:
        raise httpx.ConnectError("mocked: ollama unreachable")


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    # No real network: agent status treats Ollama as unreachable.
    monkeypatch.setattr(
        routes, "instrumented_client", lambda *a, **k: _UnreachableClient()
    )
    return TestClient(app)


def test_status_unconfigured_and_unreachable(client: TestClient) -> None:
    res = client.get("/api/agent/status")
    assert res.status_code == 200
    body = res.json()
    assert body["configured"] is False
    assert body["ollama_reachable"] is False
    assert body["model"] is None
    assert body["available_models"] == []


def test_chat_requires_onboarding(client: TestClient) -> None:
    res = client.post("/api/agent/chat", json={"prompt": "hello"})
    assert res.status_code == 409


def test_config_roundtrip(client: TestClient) -> None:
    res = client.put("/api/agent/config", json={"model": "gemma4:e2b"})
    assert res.status_code == 200
    assert res.json()["endpoint"] == "http://localhost:11434"

    status = client.get("/api/agent/status").json()
    assert status["configured"] is True
    assert status["model"] == "gemma4:e2b"

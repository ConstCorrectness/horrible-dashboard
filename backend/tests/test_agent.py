import pytest
from fastapi.testclient import TestClient

from backend.app import app


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    # Point at a port nothing listens on so status is deterministic in CI.
    monkeypatch.setenv("HORRIBLE_OLLAMA_URL", "http://127.0.0.1:9")
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

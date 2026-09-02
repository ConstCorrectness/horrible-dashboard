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
    assert body["reachable"] is False
    assert body["model"] is None
    assert body["available_models"] == []
    # All known providers are probed and reported, none reachable here.
    kinds = {p["kind"] for p in body["providers"]}
    assert kinds == {
        "ollama",
        "lmstudio",
        "llamacpp",
        "vllm",
        "openai",
        "anthropic",
        "gemini",
    }
    # litellm providers are always considered reachable since we can't reliably ping them
    assert all(
        p["reachable"] is False
        for p in body["providers"]
        if p["kind"] in {"ollama", "lmstudio", "vllm"}
    )


def test_chat_requires_onboarding(client: TestClient) -> None:
    res = client.post("/api/agent/chat", json={"prompt": "hello"})
    assert res.status_code == 409


def test_config_roundtrip(client: TestClient) -> None:
    res = client.put("/api/agent/config", json={"model": "gemma4:e2b"})
    assert res.status_code == 200
    body = res.json()
    # Provider defaults to Ollama; its endpoint is filled from the provider default.
    assert body["provider"] == "ollama"
    assert body["endpoint"] == "http://localhost:11434"

    status = client.get("/api/agent/status").json()
    assert status["configured"] is True
    assert status["provider"] == "ollama"
    assert status["model"] == "gemma4:e2b"


def test_config_lmstudio_defaults_endpoint(client: TestClient) -> None:
    res = client.put(
        "/api/agent/config", json={"model": "local-model", "provider": "lmstudio"}
    )
    assert res.status_code == 200
    assert res.json()["endpoint"] == "http://localhost:1234"


def test_config_rejects_unknown_provider(client: TestClient) -> None:
    res = client.put("/api/agent/config", json={"model": "m", "provider": "bogus"})
    assert res.status_code == 422


def test_pull_rejected_for_non_pulling_provider(client: TestClient) -> None:
    client.put("/api/agent/config", json={"model": "m", "provider": "lmstudio"})
    res = client.post("/api/agent/pull", json={"model": "m"})
    assert res.status_code == 400


def test_complete_requires_onboarding(client: TestClient) -> None:
    res = client.post("/api/agent/complete", json={"prefix": "def foo("})
    assert res.status_code == 409


def test_complete_returns_completion(client: TestClient, monkeypatch) -> None:
    client.put("/api/agent/config", json={"model": "gemma4:e2b"})

    async def fake_generate(*args, **kwargs) -> str:
        return "x):\n    return x"

    monkeypatch.setattr(routes.P, "generate", fake_generate)
    res = client.post(
        "/api/agent/complete",
        json={"prefix": "def foo(", "suffix": "", "language": "Python"},
    )
    assert res.status_code == 200
    assert res.json()["completion"] == "x):\n    return x"


def test_complete_prompt_includes_lsp_grounding(
    client: TestClient, monkeypatch
) -> None:
    client.put("/api/agent/config", json={"model": "gemma4:e2b"})
    captured: dict[str, str] = {}

    async def fake_generate(_client, _info, _endpoint, _model, prompt) -> str:  # noqa: ANN001
        captured["prompt"] = prompt
        return "done"

    monkeypatch.setattr(routes.P, "generate", fake_generate)
    res = client.post(
        "/api/agent/complete",
        json={
            "prefix": "user.",
            "suffix": "",
            "language": "TypeScript",
            "completions": ["name", "email"],
            "hover": "(property) User.name: string",
        },
    )
    assert res.status_code == 200
    prompt = captured["prompt"]
    # The grounding (in-scope symbols + cursor type) is fed into the prompt.
    assert "name, email" in prompt
    assert "User.name: string" in prompt


def test_stt_handles_empty_or_corrupt_audio(client: TestClient) -> None:
    # The STT route lazy-imports torch/transformers and answers 503 without them,
    # so this asserts nothing on a node that has not run `uv sync --extra voice`.
    # Skipped rather than asserted-around, the same way the playwright, aiortc and
    # torch tests here already do it -- a test that fails on a fresh clone trains
    # people to ignore a red suite.
    pytest.importorskip("torch")
    pytest.importorskip("transformers")

    # Assert on the transcript, not the whole envelope: the response also carries
    # `ranOn` (local vs a borrowed peer), and an exact-dict match made adding that
    # field look like an STT regression.
    res = client.post(
        "/api/agent/stt",
        files={"file": ("empty.webm", b"", "audio/webm")},
    )
    assert res.status_code == 200
    assert res.json()["text"] == ""

    res = client.post(
        "/api/agent/stt",
        files={"file": ("corrupt.webm", b"garbage-bytes-12345", "audio/webm")},
    )
    assert res.status_code == 200
    assert res.json()["text"] == ""


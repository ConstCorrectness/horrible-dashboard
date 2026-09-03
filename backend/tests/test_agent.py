import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.agent import providers as P
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
    # A hosted provider counts a key exported in the environment, so a developer
    # who happens to have one set would otherwise see it reported as configured
    # here and the status assertions would fail on their machine only.
    for info in P.PROVIDERS.values():
        if info.env_var:
            monkeypatch.delenv(info.env_var, raising=False)
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
        "openrouter",
    }
    assert all(p["reachable"] is False for p in body["providers"])
    # A hosted provider's readiness is whether we hold a key, not whether a port
    # answers — with no key configured it must not report itself usable.
    hosted = {p["kind"]: p for p in body["providers"] if p["hosted"]}
    assert set(hosted) == {"openai", "anthropic", "gemini", "openrouter"}
    assert all(not p["has_api_key"] and not p["reachable"] for p in hosted.values())
    assert hosted["openrouter"]["api_key_url"]


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


def test_provider_key_roundtrip(client: TestClient) -> None:
    """A stored key is what makes a hosted provider usable, and it is never read back."""
    status = {p["kind"]: p for p in client.get("/api/agent/status").json()["providers"]}
    assert status["openrouter"]["has_api_key"] is False

    res = client.put("/api/agent/providers/openrouter/key", json={"key": "sk-or-test"})
    assert res.status_code == 200
    assert res.json() == {"has_api_key": True}

    info = P.PROVIDERS["openrouter"]
    assert P.api_key_for(info) == "sk-or-test"
    # The key is not on any response the browser sees.
    body = client.get("/api/agent/status").text
    assert "sk-or-test" not in body

    assert client.delete("/api/agent/providers/openrouter/key").json() == {
        "has_api_key": False
    }
    assert P.api_key_for(info) is None


def test_provider_key_rejects_local_provider(client: TestClient) -> None:
    """Ollama has no API key; storing one under its name would be a secret nothing
    ever reads."""
    res = client.put("/api/agent/providers/ollama/key", json={"key": "x"})
    assert res.status_code == 404


def test_blank_key_removes_rather_than_stores(client: TestClient) -> None:
    """An emptied field must delete: an empty stored secret would shadow the
    environment variable and still report the provider as configured."""
    client.put("/api/agent/providers/openai/key", json={"key": "sk-test"})
    assert client.put("/api/agent/providers/openai/key", json={"key": "  "}).json() == {
        "has_api_key": False
    }


def test_env_var_counts_as_configured(client: TestClient, monkeypatch) -> None:
    """litellm reads the variable itself, so reporting "no key" for a provider that
    would work is a failure the user cannot debug."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-env")
    assert P.api_key_for(P.PROVIDERS["openrouter"]) == "sk-or-env"
    providers = {
        p["kind"]: p for p in client.get("/api/agent/status").json()["providers"]
    }
    assert providers["openrouter"]["has_api_key"] is True


def test_qualify_model_adds_routing_prefix() -> None:
    """A bare OpenRouter id is not a model litellm can place; an already-prefixed
    one must not be prefixed twice."""
    orouter = P.PROVIDERS["openrouter"]
    assert P.qualify_model(orouter, "minimax/minimax-m3:free") == (
        "openrouter/minimax/minimax-m3:free"
    )
    assert P.qualify_model(orouter, "openrouter/minimax/minimax-m3:free") == (
        "openrouter/minimax/minimax-m3:free"
    )
    assert P.qualify_model(P.PROVIDERS["gemini"], "gemini-2.5-pro") == (
        "gemini/gemini-2.5-pro"
    )
    # Providers whose ids are already unambiguous are left alone.
    assert P.qualify_model(P.PROVIDERS["openai"], "gpt-4o") == "gpt-4o"


@pytest.mark.anyio
async def test_catalog_orders_tool_capable_models_first() -> None:
    """A model without tool support never calls a tool in the orchestrator loop,
    which reads as the agent ignoring you rather than as a model that cannot."""

    class _Catalog:
        async def get(self, url, **kwargs):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "chat-only", "supported_parameters": ["temperature"]},
                        {"id": "tools-a", "supported_parameters": ["tools"]},
                    ]
                },
                request=httpx.Request("GET", url),
            )

    P._CATALOG_CACHE.clear()
    models = await P._catalog_models(_Catalog(), P.PROVIDERS["openrouter"])
    assert models == ["tools-a", "chat-only"]
    P._CATALOG_CACHE.clear()


@pytest.mark.anyio
async def test_catalog_failure_falls_back_rather_than_raising() -> None:
    """The key decides usability; an unreachable catalog must not make a working
    provider look broken."""

    class _Down:
        async def get(self, url, **kwargs):
            raise httpx.ConnectError("no network")

    P._CATALOG_CACHE.clear()
    models = await P._catalog_models(_Down(), P.PROVIDERS["openrouter"])
    assert models == list(P.PROVIDERS["openrouter"].static_models)
    P._CATALOG_CACHE.clear()

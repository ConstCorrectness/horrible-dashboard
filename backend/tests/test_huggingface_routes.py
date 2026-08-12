"""Tests for the Hugging Face browser's HTTP surface.

Everything is asserted against the **HTTP response**, never the helper's return
value: the routes declare `response_model`s, and a Pydantic response model silently
drops any field it doesn't declare. A test that checks what the tool returned would
pass while the browser receives `undefined`.

`huggingface_tools`' network calls are stubbed at `_request` / `_read_file`, so
nothing here touches the Hub or needs a token.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.connectors.providers import huggingface_routes as routes
from backend.modules.connectors.providers import huggingface_tools as hf


@pytest.fixture(autouse=True)
def _clean_cache():
    routes.clear_cache()
    yield
    routes.clear_cache()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _hit(repo_id: str = "google/gemma-4-12b-it", **over: Any) -> dict[str, Any]:
    base = {
        "id": repo_id,
        "type": "model",
        "private": False,
        "downloads": 12345,
        "likes": 678,
        "updated_at": "2026-08-01T00:00:00.000Z",
        "task": "text-generation",
        "tags": ["transformers", "gemma4"],
        "url": f"https://huggingface.co/{repo_id}",
    }
    base.update(over)
    return base


# ── search ───────────────────────────────────────────────────────────────────


def test_search_serves_every_field_the_pane_renders(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Asserted on the JSON body: the response model is the thing that can drop a
    field, so checking `_search`'s return value would prove nothing."""

    async def fake_request(path: str, *, params: Any = None) -> Any:
        assert path == "/models"
        assert params["search"] == "gemma"
        assert params["sort"] == "likes"
        assert params["filter"] == "text-generation"
        return [
            {
                "id": "google/gemma-4-12b-it",
                "downloads": 12345,
                "likes": 678,
                "lastModified": "2026-08-01T00:00:00.000Z",
                "pipeline_tag": "text-generation",
                "tags": ["transformers", "gemma4"],
                "private": False,
            }
        ]

    monkeypatch.setattr(hf, "_request", fake_request)

    res = client.get(
        "/api/connectors/huggingface/search",
        params={"q": "gemma", "task": "text-generation", "sort": "likes"},
    )
    assert res.status_code == 200
    hit = res.json()["results"][0]
    assert hit["id"] == "google/gemma-4-12b-it"
    assert hit["task"] == "text-generation"
    assert hit["downloads"] == 12345
    assert hit["likes"] == 678
    assert hit["tags"] == ["transformers", "gemma4"]
    assert hit["url"] == "https://huggingface.co/google/gemma-4-12b-it"
    assert hit["updated_at"] == "2026-08-01T00:00:00.000Z"


def test_datasets_use_the_datasets_endpoint(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    async def fake_request(path: str, *, params: Any = None) -> Any:
        seen.append(path)
        return []

    monkeypatch.setattr(hf, "_request", fake_request)
    client.get(
        "/api/connectors/huggingface/search", params={"q": "squad", "type": "dataset"}
    )
    assert seen == ["/datasets"]


def test_unknown_type_is_a_400_not_a_silent_model_search(client: TestClient) -> None:
    res = client.get(
        "/api/connectors/huggingface/search", params={"q": "x", "type": "space"}
    )
    assert res.status_code == 400
    assert "type must be one of" in res.json()["detail"]


def test_empty_query_is_rejected_by_validation(client: TestClient) -> None:
    assert (
        client.get("/api/connectors/huggingface/search", params={"q": ""}).status_code
        == 422
    )


# ── errors as values become status codes ─────────────────────────────────────


def test_not_connected_is_409_so_the_pane_can_offer_to_connect(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def not_connected(path: str, *, params: Any = None) -> Any:
        return dict(hf._NOT_CONNECTED)

    monkeypatch.setattr(hf, "_request", not_connected)
    res = client.get("/api/connectors/huggingface/search", params={"q": "gemma"})
    assert res.status_code == 409
    assert "isn't connected" in res.json()["detail"]


def test_upstream_failure_is_502_not_409(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two are handled differently by the pane — one offers a connect button,
    the other a retry — so collapsing them would be a wrong prompt, not just a
    wrong number."""

    async def upstream_down(path: str, *, params: Any = None) -> Any:
        return {"error": "couldn't reach Hugging Face: timeout"}

    monkeypatch.setattr(hf, "_request", upstream_down)
    res = client.get("/api/connectors/huggingface/search", params={"q": "gemma"})
    assert res.status_code == 502


# ── repo info ────────────────────────────────────────────────────────────────


def test_repo_info_carries_files_gated_and_library(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_request(path: str, *, params: Any = None) -> Any:
        assert path == "/models/google/gemma-4-12b-it"
        return {
            "id": "google/gemma-4-12b-it",
            "downloads": 1,
            "likes": 2,
            "gated": "manual",
            "library_name": "transformers",
            "siblings": [
                {"rfilename": "config.json"},
                {"rfilename": "README.md"},
                {"rfilename": "model-00001-of-00005.safetensors"},
            ],
        }

    monkeypatch.setattr(hf, "_request", fake_request)
    res = client.get(
        "/api/connectors/huggingface/repo", params={"repo": "google/gemma-4-12b-it"}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["files"][:2] == ["config.json", "README.md"]
    assert body["gated"] == "manual"
    assert body["library"] == "transformers"


def test_gated_false_and_gated_absent_stay_distinguishable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`gated` is three-state. Coercing a missing value to False would tell the user
    a repo is open when the Hub never said so."""

    async def no_gate_field(path: str, *, params: Any = None) -> Any:
        return {"id": "a/b", "siblings": []}

    monkeypatch.setattr(hf, "_request", no_gate_field)
    body = client.get("/api/connectors/huggingface/repo", params={"repo": "a/b"}).json()
    assert body["gated"] is None

    async def open_repo(path: str, *, params: Any = None) -> Any:
        return {"id": "a/b", "gated": False, "siblings": []}

    monkeypatch.setattr(hf, "_request", open_repo)
    routes.clear_cache()
    body = client.get("/api/connectors/huggingface/repo", params={"repo": "a/b"}).json()
    assert body["gated"] is False


def test_a_slash_in_the_repo_id_survives_because_it_is_a_query_param(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    async def fake_request(path: str, *, params: Any = None) -> Any:
        seen.append(path)
        return {"id": "lmstudio-community/gemma-4-12B-it-QAT-GGUF", "siblings": []}

    monkeypatch.setattr(hf, "_request", fake_request)
    res = client.get(
        "/api/connectors/huggingface/repo",
        params={"repo": "lmstudio-community/gemma-4-12B-it-QAT-GGUF"},
    )
    assert res.status_code == 200
    assert seen == ["/models/lmstudio-community/gemma-4-12B-it-QAT-GGUF"]


# ── files ────────────────────────────────────────────────────────────────────


def test_file_route_returns_content(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_read(args: dict[str, Any]) -> Any:
        assert args == {
            "repo": "a/b",
            "path": "config.json",
            "type": "model",
            "revision": "main",
        }
        return {
            "repo": "a/b",
            "type": "model",
            "path": "config.json",
            "revision": "main",
            "content": '{"model_type":"gemma4"}',
            "truncated": False,
            "url": "https://huggingface.co/a/b/resolve/main/config.json",
        }

    monkeypatch.setattr(hf, "_read_file", fake_read)
    body = client.get(
        "/api/connectors/huggingface/file",
        params={"repo": "a/b", "path": "config.json"},
    ).json()
    assert body["content"] == '{"model_type":"gemma4"}'
    assert body["truncated"] is False


def test_binary_file_is_refused_as_an_error_not_streamed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A click on a `.safetensors` shard must not pull gigabytes through the
    backend. `_read_file` refuses it upstream; this pins that the refusal reaches
    the browser as a failure rather than a page of replacement characters."""

    async def binary(args: dict[str, Any]) -> Any:
        return {"error": "model.safetensors looks like a binary file"}

    monkeypatch.setattr(hf, "_read_file", binary)
    res = client.get(
        "/api/connectors/huggingface/file",
        params={"repo": "a/b", "path": "model.safetensors"},
    )
    assert res.status_code == 502
    assert "binary" in res.json()["detail"]


# ── caching ──────────────────────────────────────────────────────────────────


def test_repeat_search_is_served_from_cache(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}

    async def counting(path: str, *, params: Any = None) -> Any:
        calls["n"] += 1
        return [{"id": "a/b"}]

    monkeypatch.setattr(hf, "_request", counting)
    for _ in range(3):
        client.get("/api/connectors/huggingface/search", params={"q": "gemma"})
    assert calls["n"] == 1


def test_fresh_bypasses_the_cache(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}

    async def counting(path: str, *, params: Any = None) -> Any:
        calls["n"] += 1
        return [{"id": "a/b"}]

    monkeypatch.setattr(hf, "_request", counting)
    client.get("/api/connectors/huggingface/search", params={"q": "gemma"})
    client.get(
        "/api/connectors/huggingface/search", params={"q": "gemma", "fresh": "true"}
    )
    assert calls["n"] == 2


def test_an_error_is_never_cached(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Caching a failure would keep a "not connected" banner up for a minute after
    the user connected — `_check` raises before `_store` is reached."""
    state = {"fail": True}

    async def flaky(path: str, *, params: Any = None) -> Any:
        if state["fail"]:
            return dict(hf._NOT_CONNECTED)
        return [{"id": "a/b"}]

    monkeypatch.setattr(hf, "_request", flaky)
    assert (
        client.get(
            "/api/connectors/huggingface/search", params={"q": "gemma"}
        ).status_code
        == 409
    )
    state["fail"] = False
    res = client.get("/api/connectors/huggingface/search", params={"q": "gemma"})
    assert res.status_code == 200
    assert res.json()["results"][0]["id"] == "a/b"


def test_cache_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    routes.clear_cache()
    for i in range(routes.CACHE_MAX + 25):
        routes._store(f"k{i}", i, 60.0)
    assert len(routes._cache) <= routes.CACHE_MAX

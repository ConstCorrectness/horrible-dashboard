"""The embedding client: batch requests, cached model resolution, offline fallback.

These are the paths a bulk index build depends on. The per-document version of this
code made one model-discovery request (`GET /api/tags`) *and* one fresh HTTP client
per text, which is why a large index build spent almost all its time outside the
embedder — the model answered in ~65ms and the requests were ~2s apart.
"""

import asyncio

import httpx
import pytest

from backend.modules.database import embeddings as em


class _Recorder:
    """Stands in for the provider, counting what actually goes over the wire."""

    def __init__(self, dim: int = 4, batch_ok: bool = True) -> None:
        self.dim = dim
        self.batch_ok = batch_ok
        self.tags = 0
        self.batch_posts = 0
        self.single_posts = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/api/tags"):
            self.tags += 1
            return httpx.Response(200, json={"models": [{"name": "all-minilm:latest"}]})
        if url.endswith("/api/embed"):
            if not self.batch_ok:
                return httpx.Response(404, json={"error": "not supported"})
            self.batch_posts += 1
            import json as _json

            texts = _json.loads(request.content)["input"]
            return httpx.Response(
                200, json={"embeddings": [[0.5] * self.dim for _ in texts]}
            )
        if url.endswith("/api/embeddings"):
            self.single_posts += 1
            return httpx.Response(200, json={"embedding": [0.5] * self.dim})
        return httpx.Response(404)


@pytest.fixture(autouse=True)
def _reset_model_cache(monkeypatch):
    """The resolution cache is process-global; keep tests independent.

    Also unpins the embedding model. `_pinned_embedding_model` reads the real
    settings file, so without this every test here would assert against whatever
    this machine happens to have pinned rather than against discovery.
    """
    monkeypatch.setattr(em, "_model_cache", None)
    monkeypatch.setattr(em, "_pinned_embedding_model", lambda: "")
    yield
    monkeypatch.setattr(em, "_model_cache", None)


def _install(monkeypatch, rec: _Recorder) -> None:
    """Point the module at a mock transport and a fixed ollama config."""

    class _Config:
        provider = "ollama"
        endpoint = "http://test-host:11434"
        model = "gemma4:e2b"

    monkeypatch.setattr(em, "_load_config", lambda: _Config())
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(rec.handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(em.httpx, "AsyncClient", factory)


def test_batch_embedding_is_one_request_for_many_texts(monkeypatch):
    rec = _Recorder()
    _install(monkeypatch, rec)

    texts = [f"symbol number {i}" for i in range(50)]
    vectors, method = asyncio.run(em.get_embeddings(texts))

    assert len(vectors) == 50 and all(len(v) == 4 for v in vectors)
    assert method == "ollama/all-minilm:latest"
    # The whole point: one embed call and one discovery call, not 50 of each.
    assert rec.batch_posts == 1
    assert rec.single_posts == 0
    assert rec.tags == 1


def test_model_resolution_is_cached_across_calls(monkeypatch):
    rec = _Recorder()
    _install(monkeypatch, rec)

    asyncio.run(em.get_embeddings(["a", "b"]))
    asyncio.run(em.get_embeddings(["c", "d"]))
    asyncio.run(em.get_embedding("e"))

    # Discovery happened once for three separate calls; it used to run per text.
    assert rec.tags == 1


def test_falls_back_to_concurrent_singles_without_batch_endpoint(monkeypatch):
    rec = _Recorder(batch_ok=False)
    _install(monkeypatch, rec)

    vectors, method = asyncio.run(em.get_embeddings(["x", "y", "z"]))

    assert len(vectors) == 3
    assert method == "ollama/all-minilm:latest"
    assert rec.single_posts == 3  # one per text, but over a shared client


def test_offline_provider_degrades_to_hash_fallback(monkeypatch):
    class _Config:
        provider = "ollama"
        endpoint = "http://test-host:11434"
        model = "gemma4:e2b"

    monkeypatch.setattr(em, "_load_config", lambda: _Config())

    def dead(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        em.httpx,
        "AsyncClient",
        lambda *a, **k: real_client(
            *a, **{**k, "transport": httpx.MockTransport(dead)}
        ),
    )

    vectors, method = asyncio.run(em.get_embeddings(["a", "b"]))
    # Never raises, and says it fell back so callers can refuse to persist it.
    assert method == "local-fallback"
    assert len(vectors) == 2 and len(vectors[0]) == em.FALLBACK_DIMENSION


def test_no_config_uses_fallback_without_network(monkeypatch):
    monkeypatch.setattr(em, "_load_config", lambda: None)
    vectors, method = asyncio.run(em.get_embeddings(["a"]))
    assert method == "local-fallback" and len(vectors) == 1


def test_empty_input_is_a_noop(monkeypatch):
    vectors, method = asyncio.run(em.get_embeddings([]))
    assert vectors == [] and method == "noop"


def test_pinned_model_overrides_discovery(monkeypatch):
    """A pin is used verbatim and skips discovery entirely.

    Discovery is a keyword priority over the provider's catalog, so it changes model
    — and vector width — on its own when that catalog changes. That is what broke
    every 384-dim collection on this node when the provider moved from Ollama to
    LM Studio. A pin is how a node states which embedding space its indexes are in.
    """
    rec = _Recorder()
    _install(monkeypatch, rec)
    monkeypatch.setattr(em, "_pinned_embedding_model", lambda: "nomic-embed-text-v1.5")

    _vectors, method = asyncio.run(em.get_embeddings(["a", "b"]))

    assert method == "ollama/nomic-embed-text-v1.5"
    # The pin is authoritative, so the catalog is never even asked for.
    assert rec.tags == 0


def test_changing_the_pin_takes_effect_immediately(monkeypatch):
    """The pin is part of the cache key, not a value behind its TTL.

    Resolution is cached for a minute. If the pin were only read on a cache miss, a
    rebuild kicked off right after changing this setting would index with the old
    model — writing a whole collection in the wrong embedding space, silently.
    """
    rec = _Recorder()
    _install(monkeypatch, rec)

    monkeypatch.setattr(em, "_pinned_embedding_model", lambda: "first-model")
    _v, first = asyncio.run(em.get_embeddings(["a"]))
    monkeypatch.setattr(em, "_pinned_embedding_model", lambda: "second-model")
    _v, second = asyncio.run(em.get_embeddings(["b"]))

    assert first == "ollama/first-model"
    assert second == "ollama/second-model"


def test_unpinning_returns_to_discovery(monkeypatch):
    """Blank means discover, and a blank pin must not be cached as a pinned model."""
    rec = _Recorder()
    _install(monkeypatch, rec)

    monkeypatch.setattr(em, "_pinned_embedding_model", lambda: "pinned-model")
    _v, pinned = asyncio.run(em.get_embeddings(["a"]))
    monkeypatch.setattr(em, "_pinned_embedding_model", lambda: "")
    _v, discovered = asyncio.run(em.get_embeddings(["b"]))

    assert pinned == "ollama/pinned-model"
    assert discovered == "ollama/all-minilm:latest"
    assert rec.tags == 1

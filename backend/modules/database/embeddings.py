import asyncio
import logging
import hashlib
import math
import time
from dataclasses import dataclass
from typing import Literal

import httpx
from backend.modules.agent.routes import _load_config
from backend.modules.agent import providers as P

logger = logging.getLogger(__name__)

# Standard dimension for fallback embeddings
FALLBACK_DIMENSION = 384


def get_local_fallback_embedding(text: str) -> list[float]:
    """
    Generate a simple, deterministic 384-dimensional text embedding
    using character/word hash mapping.
    This serves as a zero-dependency local fallback when the remote LLM
    provider is unavailable or offline.
    """
    if not text:
        return [0.0] * FALLBACK_DIMENSION

    # Initialize a zero vector
    vector = [0.0] * FALLBACK_DIMENSION

    # Process text: lowercase and tokenize
    words = text.lower().split()
    if not words:
        words = [text.lower()]

    # Bag-of-words hashing to indices
    for word in words:
        # Hash word to an index
        h_word = int(hashlib.md5(word.encode("utf-8")).hexdigest(), 16)
        index = h_word % FALLBACK_DIMENSION
        vector[index] += 1.0

        # Also hash sliding character 3-grams for subword similarity
        if len(word) >= 3:
            for i in range(len(word) - 2):
                trigram = word[i : i + 3]
                h_trigram = int(hashlib.md5(trigram.encode("utf-8")).hexdigest(), 16)
                index_tri = h_trigram % FALLBACK_DIMENSION
                vector[index_tri] += 0.3

    # Normalize vector to unit length (L2 norm)
    sq_sum = sum(v * v for v in vector)
    norm = math.sqrt(sq_sum)
    if norm > 0:
        vector = [v / norm for v in vector]
    else:
        # fallback if somehow norm is 0
        vector[0] = 1.0

    return vector


#: Which side of a retrieval pair a text is. Asymmetric embedders (NVIDIA's
#: nemotron-embed) place a search query and the passage that answers it in different
#: regions on purpose, so the caller has to say which one it is holding. Indexing
#: is the common case and the default; every search path passes "query".
InputType = Literal["query", "passage"]


@dataclass(frozen=True)
class _Embedder:
    model: str
    endpoint: str
    info: P.ProviderInfo

    @property
    def method(self) -> str:
        """The `<provider>/<model>` tag stamped on every vector this produced."""
        return f"{'ollama' if self.info.dialect == 'ollama' else self.info.kind}/{self.model}"

    def headers(self) -> dict[str, str]:
        """The provider's key, read per request rather than cached, so a rotated key
        takes effect without waiting out `_MODEL_CACHE_TTL_S`. Empty for every local
        server: `api_key_for` answers None for anything not hosted."""
        key = P.api_key_for(self.info)
        return {"Authorization": f"Bearer {key}"} if key else {}

    def body(self, inputs: str | list[str], input_type: InputType) -> dict[str, object]:
        body: dict[str, object] = {"model": self.model, "input": inputs}
        if self.info.embed_input_type:
            body["input_type"] = input_type
        return body


# Resolved embedder cache. Model discovery costs a `GET /api/tags` round-trip, and
# `get_embedding` did it on **every single call** — in a bulk index that's one
# discovery request per document, for an answer that never changes mid-run. Cached
# with a short TTL so a newly pulled embedding model is still picked up within a
# minute. The pin and the provider setting are part of the key, not just the value:
# either must take effect on the next call, not up to a TTL later, or a rebuild
# started right after changing it silently indexes with the old model.
_MODEL_CACHE_TTL_S = 60.0
_model_cache: tuple[float, tuple[str, str], _Embedder] | None = None

#: `database.embedProvider` value meaning "whatever the agent uses" — the behaviour
#: before embeddings had a provider of their own, kept as the default so a node
#: whose indexes were built that way keeps the same embedding space.
FOLLOW_AGENT = "agent"


def _embed_provider_setting() -> str:
    from backend.modules.settings.routes import get_value

    return str(
        get_value("database.embedProvider", FOLLOW_AGENT) or FOLLOW_AGENT
    ).strip()


# How many embeddings to request concurrently when the provider has no batch endpoint.
# The server processes one at a time per slot, but overlapping the request/response
# round-trips still removes most of the per-call latency.
EMBED_CONCURRENCY = 8


def _pinned_embedding_model() -> str:
    """The `database.embedModel` setting, or "" to discover one.

    Discovery is a keyword *priority* over whatever the provider happens to offer, so
    it silently changes model — and therefore vector width — when the provider's
    catalog changes. Switching this node from Ollama to LM Studio dropped
    `all-minilm` (384-dim) from the list and the next keyword, `nomic-embed`, took
    over at 768, breaking every collection built before the switch. A collection's
    width is fixed at creation, so that is not a degradation but a hard stop. Pinning
    is how a node says which embedding space its indexes are in.
    """
    from backend.modules.settings.routes import get_value

    return str(get_value("database.embedModel", "") or "").strip()


def _select_embedding_model(available: list[str], default: str) -> str:
    """The best dedicated embedding model among those pulled, else `default`."""
    for kw in ("all-minilm", "nomic-embed", "bge-", "embed"):
        matched = next((m for m in available if kw in m.lower()), None)
        if matched:
            return matched
    return default


def _embedding_provider() -> tuple[P.ProviderInfo, str, str] | None:
    """(provider, endpoint, fallback model) to embed with, or None when there is
    nothing usable — logged with the reason, since every caller degrades to the hash
    embedding and would otherwise fail silently.

    `database.embedProvider` decides; its default follows the agent. A provider
    chosen here that is also the agent's borrows the agent's saved endpoint (a
    repointed vLLM, a non-default LM Studio port) and model; any other uses its own
    defaults, because the agent's model id means nothing to a different vendor.
    """
    config = _load_config()
    choice = _embed_provider_setting()
    if choice == FOLLOW_AGENT:
        if not config:
            logger.info("No agent config and no embedding provider set.")
            return None
        info = P.provider_for(config.provider)
    else:
        info = P.PROVIDERS.get(choice)
        if info is None:
            logger.warning(f"database.embedProvider names no provider: {choice!r}")
            return None
    same = config is not None and config.provider == info.kind
    # `embed_endpoint` first: it is set only where the chat endpoint is not where
    # embeddings live (litellm providers have no chat endpoint to save at all).
    saved = config.endpoint if config is not None and same else ""
    endpoint = info.embed_endpoint or saved or info.default_endpoint
    if info.dialect not in ("ollama", "openai") and not info.embed_endpoint:
        logger.warning(f"{info.label} serves no embeddings this node can call.")
        return None
    if info.hosted and not P.api_key_for(info):
        logger.warning(f"No API key for {info.label}; cannot embed there.")
        return None
    return info, endpoint, (config.model if same and config else "")


async def _resolve_model(client: httpx.AsyncClient) -> _Embedder | None:
    """The embedder to use, or None with nothing configured. Cached for
    `_MODEL_CACHE_TTL_S` so bulk indexing doesn't re-probe per document."""
    global _model_cache
    now = time.monotonic()
    key = (_pinned_embedding_model(), _embed_provider_setting())
    if (
        _model_cache
        and _model_cache[1] == key
        and now - _model_cache[0] < _MODEL_CACHE_TTL_S
    ):
        return _model_cache[2]
    resolved = _embedding_provider()
    if resolved is None:
        return None
    info, endpoint, agent_model = resolved
    pin = key[0]
    if pin:
        # Deliberately not validated against the provider's catalog. A pin that names
        # a model the server isn't serving must fail as a loud request error, not
        # quietly fall back to discovery — falling back is what would reintroduce the
        # silent width change this setting exists to prevent.
        model = pin
    elif info.default_embed_model:
        # A hosted provider's listing cannot be discovered from -- see the field.
        model = info.default_embed_model
    else:
        model = agent_model
        try:
            model = _select_embedding_model(
                await P.list_models(client, info, endpoint), model
            )
        except Exception as exc:  # noqa: BLE001 — discovery is best-effort
            logger.debug(f"Failed to query available models list: {exc}")
    embedder = _Embedder(model=model, endpoint=endpoint, info=info)
    _model_cache = (now, key, embedder)
    return embedder


class _ProviderUnreachable(Exception):
    """The embedding endpoint cannot serve this request at all: nothing is
    listening, or a hosted provider refused it (bad key, retired model). Unlike a
    missing batch endpoint, retrying text by text cannot help — it only turns one
    refusal into hundreds of them."""


def _refusal(res: httpx.Response) -> str:
    """A provider's error body, short enough to log. NIM's 410 names the retirement
    date and OpenRouter's 404 the missing model — the status code alone says neither."""
    return f"HTTP {res.status_code}: {res.text[:300]}"


async def _embed_batch(
    client: httpx.AsyncClient,
    texts: list[str],
    embedder: _Embedder,
    input_type: InputType,
) -> list[list[float]] | None:
    """One request for many texts, or None if the provider has no batch endpoint.
    Ollama's `/api/embed` and the OpenAI-compatible `/v1/embeddings` both take an
    array `input`."""
    try:
        if embedder.info.dialect == "ollama":
            res = await client.post(
                f"{embedder.endpoint}/api/embed",
                json={"model": embedder.model, "input": texts},
            )
            res.raise_for_status()
            out = res.json().get("embeddings")
        else:
            res = await client.post(
                f"{embedder.endpoint}/v1/embeddings",
                json=embedder.body(texts, input_type),
                headers=embedder.headers(),
            )
            # A hosted provider always has the batch shape, so a refusal is a
            # refusal -- not a missing endpoint that per-text requests could avoid.
            if embedder.info.hosted and res.is_error:
                raise _ProviderUnreachable(_refusal(res))
            res.raise_for_status()
            data = res.json().get("data", [])
            # OpenAI's shape carries `index`; order by it rather than trusting the
            # array order of a router fanning one batch out upstream.
            data = sorted(data, key=lambda d: d.get("index", 0))
            out = [d.get("embedding") for d in data]
        if isinstance(out, list) and len(out) == len(texts):
            return [[float(x) for x in vec] for vec in out]
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        raise _ProviderUnreachable(str(exc)) from exc
    except _ProviderUnreachable:
        raise
    except Exception as exc:  # noqa: BLE001 — fall back to per-text requests
        logger.debug(f"Batch embedding unavailable ({exc}); using per-text requests.")
    return None


async def get_embeddings(
    texts: list[str], *, allow_peer: bool = True, input_type: InputType = "passage"
) -> tuple[list[list[float]], str]:
    """Embed many texts in as few round-trips as possible.

    The bulk path behind index builds. Three things it does that calling
    `get_embedding` in a loop does not: resolve the model **once** (not one
    `GET /api/tags` per text), reuse **one** HTTP client (not a fresh connection pool
    per text), and send a **batch** request when the provider supports one — falling
    back to bounded-concurrency single requests when it doesn't.

    Returns `(vectors, method)` with vectors in input order. Like `get_embedding`, it
    degrades to the deterministic hash embedding rather than raising, and the returned
    method says which was used so callers can refuse to persist a fallback.

    `input_type` is "passage" for anything being indexed and "query" for a search
    string -- see `InputType`. Providers that do not distinguish never see it.
    """
    if not texts:
        return [], "noop"
    # A batch is inherently a long request — a few hundred short texts at ~65ms each is
    # tens of seconds of honest work. This must not be a per-request timeout budget: a
    # timeout here degrades to the hash fallback, which the index build correctly
    # refuses to persist, so it would abandon the whole build.
    async with httpx.AsyncClient(timeout=300.0) as client:
        embedder = await _resolve_model(client)
        if embedder is None:
            return [get_local_fallback_embedding(t) for t in texts], "local-fallback"
        method = embedder.method

        # A batch is the one embedding shape worth sending off-machine: a 200 ms
        # round trip is nothing against a multi-second batch. Opt-in
        # (`database.embedOnPeer`, default off) because shipping every index build
        # to a friend's node is a surprising thing to do quietly, and
        # `allow_peer=False` on the lender's own route so serving a batch can
        # never re-borrow it from a third node. Passages only: the peer protocol
        # carries no `input_type`, and the lender embeds everything as a passage.
        if allow_peer and input_type == "passage" and _peer_offload_enabled():
            from backend.modules.database import embed_peer

            peered = await embed_peer.try_peer_batch(texts, method)
            if peered is not None:
                vectors, peer_method = peered
                return vectors, peer_method

        try:
            batched = await _embed_batch(client, texts, embedder, input_type)
        except _ProviderUnreachable as exc:
            logger.warning(
                f"Embedding provider {embedder.info.kind} at {embedder.endpoint} "
                f"cannot embed with {embedder.model} ({exc}); using local fallback."
            )
            return [get_local_fallback_embedding(t) for t in texts], "local-fallback"
        if batched is not None:
            return batched, method

        # No batch endpoint — overlap the individual requests instead.
        sem = asyncio.Semaphore(EMBED_CONCURRENCY)

        async def one(text: str) -> list[float] | None:
            async with sem:
                return await _embed_one(client, text, embedder, input_type)

        try:
            results = await asyncio.gather(*(one(t) for t in texts))
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Batch embedding failed ({exc}); using local fallback.")
            results = [None] * len(texts)
        if any(r is None for r in results):
            logger.warning(
                f"Failed to fetch remote embeddings from {embedder.info.kind}; "
                "falling back to local deterministic hash embedding."
            )
            return [get_local_fallback_embedding(t) for t in texts], "local-fallback"
        return [r for r in results if r is not None], method


def _peer_offload_enabled() -> bool:
    from backend.modules.settings.routes import get_value

    return bool(get_value("database.embedOnPeer", False))


async def _embed_one(
    client: httpx.AsyncClient, text: str, embedder: _Embedder, input_type: InputType
) -> list[float] | None:
    """A single embedding over an existing client, or None on failure."""
    try:
        if embedder.info.dialect == "ollama":
            res = await client.post(
                f"{embedder.endpoint}/api/embeddings",
                json={"model": embedder.model, "prompt": text},
            )
            res.raise_for_status()
            emb = res.json().get("embedding")
            if emb and isinstance(emb, list):
                return [float(x) for x in emb]
            return None
        res = await client.post(
            f"{embedder.endpoint}/v1/embeddings",
            json=embedder.body(text, input_type),
            headers=embedder.headers(),
        )
        if res.is_error:
            # Warning, not debug: a hosted refusal (retired model, bad key) is the
            # whole answer to "why is search running on hashes", and debug hid it.
            logger.warning(f"Embedding with {embedder.model} refused: {_refusal(res)}")
            return None
        data = res.json().get("data", [])
        if data:
            emb = data[0].get("embedding")
            if emb and isinstance(emb, list):
                return [float(x) for x in emb]
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"Single embedding failed: {exc}")
    return None


async def get_embedding(
    text: str, *, input_type: InputType = "passage"
) -> tuple[list[float], str]:
    """
    Generate an embedding vector for the text with the embedding provider
    (`database.embedProvider`, by default the agent's), falling back to the local
    deterministic hash embedding if it is offline or refuses.

    Single-text convenience over the same cached model resolution the batch path uses,
    so it no longer re-probes `GET /api/tags` on every call. For more than one text
    prefer `get_embeddings`, which batches the requests too. A search string passes
    `input_type="query"`; see `InputType`.

    Returns:
        tuple[embedding_list, model_or_method_name]
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        embedder = await _resolve_model(client)
        if embedder is None:
            return get_local_fallback_embedding(text), "local-fallback"
        vector = await _embed_one(client, text, embedder, input_type)
        if vector is not None:
            return vector, embedder.method
        logger.warning(
            f"Failed to fetch remote embedding from {embedder.info.kind}; "
            "falling back to local deterministic hash embedding."
        )
        return get_local_fallback_embedding(text), "local-fallback"

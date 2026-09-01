import asyncio
import logging
import hashlib
import math
import time

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


# Resolved (model, endpoint, dialect, kind) cache. Model discovery costs a
# `GET /api/tags` round-trip, and `get_embedding` did it on **every single call** — in
# a bulk index that's one discovery request per document, for an answer that never
# changes mid-run. Cached with a short TTL so a newly pulled embedding model is still
# picked up within a minute.
_MODEL_CACHE_TTL_S = 60.0
_model_cache: tuple[float, str, str, str, str] | None = None

# How many embeddings to request concurrently when the provider has no batch endpoint.
# The server processes one at a time per slot, but overlapping the request/response
# round-trips still removes most of the per-call latency.
EMBED_CONCURRENCY = 8


def _select_embedding_model(available: list[str], default: str) -> str:
    """The best dedicated embedding model among those pulled, else `default`."""
    for kw in ("all-minilm", "nomic-embed", "bge-", "embed"):
        matched = next((m for m in available if kw in m.lower()), None)
        if matched:
            return matched
    return default


async def _resolve_model(client: httpx.AsyncClient) -> tuple[str, str, str, str] | None:
    """(model, endpoint, dialect, kind) for embedding, or None with no agent config.
    Cached for `_MODEL_CACHE_TTL_S` so bulk indexing doesn't re-probe per document."""
    global _model_cache
    now = time.monotonic()
    if _model_cache and now - _model_cache[0] < _MODEL_CACHE_TTL_S:
        return _model_cache[1:]
    config = _load_config()
    if not config:
        return None
    info = P.provider_for(config.provider)
    endpoint = config.endpoint or info.default_endpoint
    model = config.model
    try:
        model = _select_embedding_model(
            await P.list_models(client, info, endpoint), model
        )
    except Exception as exc:  # noqa: BLE001 — discovery is best-effort
        logger.debug(f"Failed to query available models list: {exc}")
    _model_cache = (now, model, endpoint, info.dialect, info.kind)
    return _model_cache[1:]


async def _embed_batch(
    client: httpx.AsyncClient,
    texts: list[str],
    model: str,
    endpoint: str,
    dialect: str,
    kind: str,
) -> list[list[float]] | None:
    """One request for many texts, or None if the provider has no batch endpoint.
    Ollama's `/api/embed` and the OpenAI-compatible `/v1/embeddings` both take an
    array `input` and return results in order."""
    try:
        if dialect == "ollama":
            res = await client.post(
                f"{endpoint}/api/embed", json={"model": model, "input": texts}
            )
            res.raise_for_status()
            out = res.json().get("embeddings")
        else:
            res = await client.post(
                f"{endpoint}/v1/embeddings", json={"model": model, "input": texts}
            )
            res.raise_for_status()
            out = [d.get("embedding") for d in res.json().get("data", [])]
        if isinstance(out, list) and len(out) == len(texts):
            return [[float(x) for x in vec] for vec in out]
    except Exception as exc:  # noqa: BLE001 — fall back to per-text requests
        logger.debug(f"Batch embedding unavailable ({exc}); using per-text requests.")
    return None


async def get_embeddings(
    texts: list[str], *, allow_peer: bool = True
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
    """
    if not texts:
        return [], "noop"
    # A batch is inherently a long request — a few hundred short texts at ~65ms each is
    # tens of seconds of honest work. This must not be a per-request timeout budget: a
    # timeout here degrades to the hash fallback, which the index build correctly
    # refuses to persist, so it would abandon the whole build.
    async with httpx.AsyncClient(timeout=300.0) as client:
        resolved = await _resolve_model(client)
        if resolved is None:
            logger.info("Agent config not found; using local fallback embedding.")
            return [get_local_fallback_embedding(t) for t in texts], "local-fallback"
        model, endpoint, dialect, kind = resolved
        method = f"{'ollama' if dialect == 'ollama' else kind}/{model}"

        # A batch is the one embedding shape worth sending off-machine: a 200 ms
        # round trip is nothing against a multi-second batch. Opt-in
        # (`database.embedOnPeer`, default off) because shipping every index build
        # to a friend's node is a surprising thing to do quietly, and
        # `allow_peer=False` on the lender's own route so serving a batch can
        # never re-borrow it from a third node.
        if allow_peer and _peer_offload_enabled():
            from backend.modules.database import embed_peer

            peered = await embed_peer.try_peer_batch(texts, method)
            if peered is not None:
                vectors, peer_method = peered
                return vectors, peer_method

        batched = await _embed_batch(client, texts, model, endpoint, dialect, kind)
        if batched is not None:
            return batched, method

        # No batch endpoint — overlap the individual requests instead.
        sem = asyncio.Semaphore(EMBED_CONCURRENCY)

        async def one(text: str) -> list[float] | None:
            async with sem:
                return await _embed_one(client, text, model, endpoint, dialect)

        try:
            results = await asyncio.gather(*(one(t) for t in texts))
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Batch embedding failed ({exc}); using local fallback.")
            results = [None] * len(texts)
        if any(r is None for r in results):
            logger.warning(
                f"Failed to fetch remote embeddings from {kind}; "
                "falling back to local deterministic hash embedding."
            )
            return [get_local_fallback_embedding(t) for t in texts], "local-fallback"
        return [r for r in results if r is not None], method


def _peer_offload_enabled() -> bool:
    from backend.modules.settings.routes import get_value

    return bool(get_value("database.embedOnPeer", False))


async def _embed_one(
    client: httpx.AsyncClient, text: str, model: str, endpoint: str, dialect: str
) -> list[float] | None:
    """A single embedding over an existing client, or None on failure."""
    try:
        if dialect == "ollama":
            res = await client.post(
                f"{endpoint}/api/embeddings", json={"model": model, "prompt": text}
            )
            res.raise_for_status()
            emb = res.json().get("embedding")
            if emb and isinstance(emb, list):
                return [float(x) for x in emb]
            return None
        res = await client.post(
            f"{endpoint}/v1/embeddings", json={"model": model, "input": text}
        )
        res.raise_for_status()
        data = res.json().get("data", [])
        if data:
            emb = data[0].get("embedding")
            if emb and isinstance(emb, list):
                return [float(x) for x in emb]
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"Single embedding failed: {exc}")
    return None


async def get_embedding(text: str) -> tuple[list[float], str]:
    """
    Generate an embedding vector for the text using the configured agent model,
    falling back to the local deterministic hash embedding if remote server is offline.

    Single-text convenience over the same cached model resolution the batch path uses,
    so it no longer re-probes `GET /api/tags` on every call. For more than one text
    prefer `get_embeddings`, which batches the requests too.

    Returns:
        tuple[embedding_list, model_or_method_name]
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        resolved = await _resolve_model(client)
        if resolved is None:
            logger.info("Agent config not found; using local fallback embedding.")
            return get_local_fallback_embedding(text), "local-fallback"
        model, endpoint, dialect, kind = resolved
        vector = await _embed_one(client, text, model, endpoint, dialect)
        if vector is not None:
            return vector, f"{'ollama' if dialect == 'ollama' else kind}/{model}"
        logger.warning(
            f"Failed to fetch remote embedding from {kind}; "
            "falling back to local deterministic hash embedding."
        )
        return get_local_fallback_embedding(text), "local-fallback"

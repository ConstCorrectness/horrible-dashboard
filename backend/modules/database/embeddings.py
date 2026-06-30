import logging
import hashlib
import math
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


async def get_embedding(text: str) -> tuple[list[float], str]:
    """
    Generate an embedding vector for the text using the configured agent model,
    falling back to the local deterministic hash embedding if remote server is offline.
    Returns:
        tuple[embedding_list, model_or_method_name]
    """
    config = _load_config()
    if not config:
        logger.info("Agent config not found; using local fallback embedding.")
        return get_local_fallback_embedding(text), "local-fallback"

    info = P.provider_for(config.provider)
    endpoint = config.endpoint or info.default_endpoint

    # Base fallback is the user's configured agent model
    model = config.model

    async with httpx.AsyncClient(timeout=10.0) as client:
        # Check if there is a dedicated embedding model already pulled
        try:
            available_models = await P.list_models(client, info, endpoint)
            # Prioritize standard dedicated embedding models
            embedding_keywords = ["all-minilm", "nomic-embed", "bge-", "embed"]
            for kw in embedding_keywords:
                matched = next((m for m in available_models if kw in m.lower()), None)
                if matched:
                    model = matched
                    logger.info(
                        f"Detected and selected dedicated embedding model: {model}"
                    )
                    break
        except Exception as e:
            logger.debug(f"Failed to query available models list: {e}")

        try:
            if info.dialect == "ollama":
                # Try standard Ollama embeddings endpoint first
                try:
                    url = f"{endpoint}/api/embeddings"
                    res = await client.post(url, json={"model": model, "prompt": text})
                    res.raise_for_status()
                    emb = res.json().get("embedding")
                    if emb and isinstance(emb, list):
                        return [float(x) for x in emb], f"ollama/{model}"
                except httpx.HTTPError:
                    # Try newer `/api/embed` endpoint
                    url = f"{endpoint}/api/embed"
                    res = await client.post(url, json={"model": model, "input": text})
                    res.raise_for_status()
                    embeddings = res.json().get("embeddings")
                    if (
                        embeddings
                        and isinstance(embeddings, list)
                        and len(embeddings) > 0
                    ):
                        return [float(x) for x in embeddings[0]], f"ollama/{model}"
                    # Maybe it returns a flat "embedding" key
                    emb = res.json().get("embedding")
                    if emb and isinstance(emb, list):
                        return [float(x) for x in emb], f"ollama/{model}"
                    raise
            else:
                # OpenAI / vLLM compatible embeddings endpoint
                url = f"{endpoint}/v1/embeddings"
                res = await client.post(url, json={"model": model, "input": text})
                res.raise_for_status()
                data = res.json().get("data", [])
                if data and isinstance(data, list) and len(data) > 0:
                    emb = data[0].get("embedding")
                    if emb and isinstance(emb, list):
                        return [float(x) for x in emb], f"{info.kind}/{model}"

            raise RuntimeError("Embeddings response format not recognized.")
        except Exception as exc:
            logger.warning(
                f"Failed to fetch remote embedding from {info.kind} ({exc}); "
                "falling back to local deterministic hash embedding."
            )
            return get_local_fallback_embedding(
                text
            ), f"local-fallback (error: {type(exc).__name__})"

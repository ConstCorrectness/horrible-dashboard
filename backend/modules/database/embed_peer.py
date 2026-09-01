"""Embedding a batch on a friend's machine.

Embedding is the best-shaped work on this fabric: it is a **big batch with no
latency sensitivity**. A 200 ms round trip is irrelevant against a multi-second
batch of a few hundred texts, which is exactly the opposite of the profile that
makes speculative decoding a poor fit. Library ingest, CLIP indexing and the
webindex crawler all funnel through one chokepoint, so one path serves all three.

:::danger The safety property is not the network, it is the model
`get_embeddings` already returns `(vectors, method)` so callers can refuse to
persist a hash fallback. A peer running a **different embedding model** is exactly
as dangerous as that fallback, and less obvious: the vectors are real, they are
the right width, and nothing errors. They simply live in a different space, so
mixing them into one LanceDB table silently ruins retrieval — and there is no
recovery short of a full reindex.

So the model id travels with the request and a mismatch is **refused**, not
adapted to. A dimension check is not enough: two 768-dimension models disagree
completely while agreeing on width.
:::

Local always wins. This exists for a node whose own embedding provider is absent
or slow, never as a default route -- shipping every ingest off-machine would be a
surprising thing to do quietly.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

SERVICE = "embed"

#: One request carries the whole batch. Sending N requests would pay the fabric's
#: per-message signing cost N times for work that is inherently batched -- and the
#: batch is the reason this is a good fit for the fabric at all.
REQUEST_TIMEOUT_S = 600.0


class EmbeddingModelMismatch(RuntimeError):
    """The peer embeds with a different model than this node uses.

    Raised rather than handled: the caller must not persist these vectors, and
    quietly falling back would hide that the peer is misconfigured.
    """


async def embed_via_peer(
    endpoint: str, texts: list[str], *, expect_model: str
) -> tuple[list[list[float]], str]:
    """Embed `texts` on a peer, refusing anything that is not `expect_model`.

    Returns `(vectors, method)` in the same shape as `get_embeddings`, with
    `method` naming the node so a caller can see the vectors were not local.
    """
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S) as client:
        res = await client.post(
            f"{endpoint}/api/database/embeddings/batch",
            json={"texts": texts, "expectModel": expect_model},
        )
        res.raise_for_status()
        body = res.json()

    method = str(body.get("method") or "")
    vectors = body.get("vectors")

    # The peer is asked to refuse a mismatch too, but this side checks
    # independently: a borrower must not depend on a remote node's diligence for
    # the integrity of its own index.
    if method != expect_model:
        raise EmbeddingModelMismatch(
            f"peer embedded with {method!r}, this node uses {expect_model!r}; "
            "mixing embedding spaces in one table silently ruins retrieval"
        )
    if not isinstance(vectors, list) or len(vectors) != len(texts):
        raise ValueError(
            f"peer returned {len(vectors) if isinstance(vectors, list) else '?'} "
            f"vectors for {len(texts)} texts"
        )
    if any(not isinstance(v, list) or not v for v in vectors):
        raise ValueError("peer returned an empty or malformed vector")

    return vectors, method


async def try_peer_batch(texts: list[str], expect_model: str) -> Any:
    """Route a batch to a peer if one is leasable, else return None.

    Returns `(vectors, method)` or None. Never raises for an ordinary failure --
    a peer that is absent, busy or refuses is simply not used, and the caller
    proceeds locally. The one exception it *does* let through is
    `EmbeddingModelMismatch`, because that is a misconfiguration the user needs to
    see rather than a transient miss.
    """
    from backend.modules.network.hub import peer_hub
    from backend.modules.network.lease import leases

    borrowed = leases.active_borrow(SERVICE)
    if borrowed is None:
        candidates = _candidates()
        if not candidates:
            return None
        try:
            borrowed = await leases.request(peer_hub, candidates[0], SERVICE)
        except Exception as exc:  # noqa: BLE001 - a refusal is not an error here
            logger.info("embed: no peer lease (%s)", exc)
            return None

    try:
        vectors, method = await embed_via_peer(
            borrowed.endpoint, texts, expect_model=expect_model
        )
        # The node is named here rather than by `embed_via_peer`, which knows only
        # a tunnel endpoint -- and a tunnel endpoint is `127.0.0.1`, so a result
        # that carried it would read as local. Callers already branch on `method`
        # to refuse a fallback; this makes "ran on somebody else's machine"
        # visible in the same field, which is the whole of the never-silently-
        # remote rule.
        return vectors, f"peer:{borrowed.node_id}/{method}"
    except EmbeddingModelMismatch:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.info("embed: peer batch failed, staying local (%s)", exc)
        return None


def _candidates() -> list[str]:
    """Trusted, connected peers advertising an embedding-capable provider."""
    from backend.modules.network.hub import peer_hub

    out: list[str] = []
    for info in peer_hub.list_peers():
        if not info.trusted or info.status != "connected":
            continue
        if any(cap.id == "inference" for cap in info.caps):
            out.append(info.node_id)
    return out

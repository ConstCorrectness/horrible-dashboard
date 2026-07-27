"""Reciprocal Rank Fusion — combining ranked lists that share no score scale.

Extracted from the library's hybrid text+CLIP search, which needed it first, and
lifted here because web search needs exactly the same thing for exactly the same
reason. Tavily's `0.83`, Exa's `0.83` and a cosine distance from our own crawl index
are three different units; averaging them is arithmetic on incomparable numbers.
Ranks are all the lists have in common, so ranks are what we fuse.

`backend.modules.library.routes` imports from here rather than keeping its own copy —
two implementations of one formula is how they drift.
"""

from __future__ import annotations

from collections.abc import Iterable

# 60 is the value from the original paper and the de-facto default; it damps the
# difference between the top few ranks so one list can't dominate purely by being
# more confident.
RRF_K = 60


def rrf(ranked_keys: Iterable[str], *, k: int = RRF_K) -> dict[str, float]:
    """Reciprocal-rank score per key, from one ranked list.

    Only the *best* rank a key achieves counts — a source with six matching chunks
    isn't six times more relevant than one with a single strong chunk.
    """
    scores: dict[str, float] = {}
    for rank, key in enumerate(ranked_keys):
        if key not in scores:
            scores[key] = 1.0 / (k + rank + 1)
    return scores


def fuse(lists: Iterable[Iterable[str]], *, k: int = RRF_K) -> dict[str, float]:
    """Sum the reciprocal-rank scores of several ranked lists into one ranking.

    A key that several lists agree on accumulates from each, which is the property
    that makes fusion better than any single list: agreement across independent
    rankers is evidence, and it costs nothing to measure.
    """
    fused: dict[str, float] = {}
    for ranked in lists:
        for key, score in rrf(ranked, k=k).items():
            fused[key] = fused.get(key, 0.0) + score
    return fused

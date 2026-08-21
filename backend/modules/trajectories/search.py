"""Semantic search over runs — the retrieval half of the continual-learning loop.

Replaces the `LIKE` over goals this module shipped with. A substring match finds
"deploy the service" when you type "deploy"; it does not find it when you type
"ship the thing to prod", which is the whole reason you want retrieval.

## One vector per run, not per step

The unit you retrieve is a run: "how did I handle a task like this". Embedding
every step would mean tens of thousands of rows for marginal recall, and
`merge_insert` is a **whole-table** operation (~1.5s per call regardless of batch
size), so it would also make indexing quadratic in the wrong variable.

The document is composed rather than dumped — goal, harness label, outcome, the
tool names in order, and the final answer. Ordered tool names matter: "read then
edit then test" and "test then read then edit" are different strategies, and a
bag of tool names cannot tell them apart.

## Hash-fallback vectors are refused, never persisted

`get_embedding` degrades to a deterministic hash embedding when no embedder is
reachable, and returns the method so callers can tell. Persisting those would
poison the collection permanently: LanceDB fixes a table's vector width at first
write, so a collection first built on the 384-dim fallback is pinned to it, and
every real embedding afterwards is a `DimensionMismatch`. This is the failure the
library module already hit once. So indexing **skips** rather than storing junk,
and reports how many it skipped.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.modules.database.embeddings import get_embedding, get_embeddings
from backend.modules.database.vectorstore import (
    delete_collection,
    search_documents,
    upsert_documents,
)
from backend.modules.trajectories import store
from backend.modules.trajectories.models import TrajectoryRun

logger = logging.getLogger("trajectories")

#: The LanceDB collection runs are indexed into.
COLLECTION = "trajectories"

#: `get_embedding` returns this as its method when nothing real answered.
FALLBACK_METHOD = "local-fallback"

#: How many tool names go into the document. A 200-step run's full call list is
#: mostly repetition and would drown the goal, which is what actually matches.
MAX_TOOLS_IN_DOC = 40


def _is_fallback(method: str) -> bool:
    return FALLBACK_METHOD in (method or "")


def compose_document(run: TrajectoryRun, steps: list[Any] | None = None) -> str:
    """The text that represents a run in the index.

    Pure, so it can be tested without a database or an embedder.
    """
    parts = [run.goal or ""]
    if run.agent_id or run.model:
        parts.append(f"agent: {run.agent_id or 'main'} model: {run.model or 'unknown'}")
    if run.outcome:
        parts.append(f"outcome: {run.outcome}")
    if steps:
        names = [s.name for s in steps if s.kind == "action" and s.name]
        if names:
            parts.append("tools: " + " → ".join(names[:MAX_TOOLS_IN_DOC]))
        answers = [
            s.content
            for s in steps
            if s.kind == "message" and s.role == "assistant" and s.content
        ]
        if answers:
            parts.append(answers[-1][:1000])
    return "\n".join(p for p in parts if p)


async def index_runs(run_ids: list[str]) -> dict[str, int]:
    """Index runs into the vector collection in **one** write.

    Returns `{indexed, skipped}`. `skipped` counts runs whose embedding came back
    as the hash fallback — see the module docstring for why those are dropped.
    """
    if not run_ids:
        return {"indexed": 0, "skipped": 0}

    docs: list[tuple[str, str, dict[str, Any]]] = []
    for run_id in run_ids:
        run = store.get_run(run_id, with_steps=True)
        if run is None:
            continue
        text = compose_document(run, run.step_list)
        if not text.strip():
            continue
        docs.append(
            (
                run.id,
                text,
                {
                    "run_id": run.id,
                    "dataset_id": run.dataset_id,
                    "outcome": run.outcome or "",
                    "harness": run.harness or "",
                    "source": run.source,
                },
            )
        )
    if not docs:
        return {"indexed": 0, "skipped": 0}

    vectors, method = await get_embeddings([d[1] for d in docs])
    if _is_fallback(method):
        logger.info(
            "trajectories: no embedder reachable; skipped indexing %d runs", len(docs)
        )
        return {"indexed": 0, "skipped": len(docs)}

    upsert_documents(
        COLLECTION,
        [(doc_id, text, meta, vec) for (doc_id, text, meta), vec in zip(docs, vectors)],
    )
    store.mark_indexed([d[0] for d in docs])
    return {"indexed": len(docs), "skipped": 0}


async def reindex(
    dataset_id: str | None = None, *, full: bool = False
) -> dict[str, int]:
    """Index everything not yet indexed. `full` rebuilds from scratch.

    A full rebuild drops the collection first, which is also the only way to
    recover a collection that was accidentally pinned to the wrong vector width.
    """
    if full:
        delete_collection(COLLECTION)
        store.mark_indexed([], reset_dataset=dataset_id)
    pending = store.unindexed_run_ids(dataset_id)
    total = {"indexed": 0, "skipped": 0}
    # Batched across runs, not per run: one whole-table merge instead of N.
    for start in range(0, len(pending), 200):
        result = await index_runs(pending[start : start + 200])
        total["indexed"] += result["indexed"]
        total["skipped"] += result["skipped"]
    return total


async def search_runs(
    query: str,
    *,
    limit: int = 5,
    dataset_id: str | None = None,
    outcome: str | None = "success",
    harness: str | None = None,
) -> tuple[list[TrajectoryRun], str]:
    """Semantic search, returning `(runs, method)`.

    `method` is `semantic`, or `substring` when no embedder answered — the caller
    is told which it got rather than being handed silently worse results. A
    fallback query is *not* an error: finding something by substring beats
    returning nothing because the embedder is down.

    Filters are applied **after** the vector search, so it over-fetches to keep the
    requested `limit` reachable. Filtering inside LanceDB would be better and is a
    later optimisation; getting it wrong silently returns too few rows.
    """
    query = (query or "").strip()
    if not query:
        runs, _ = store.list_runs(
            dataset_id=dataset_id, outcome=outcome, harness=harness, limit=limit
        )
        return runs, "recent"

    vector, method = await get_embedding(query)
    if _is_fallback(method):
        runs, _ = store.list_runs(
            dataset_id=dataset_id,
            outcome=outcome,
            harness=harness,
            q=query,
            limit=limit,
        )
        return runs, "substring"

    hits = search_documents(COLLECTION, vector, max(limit * 5, 25))
    if not hits:
        runs, _ = store.list_runs(
            dataset_id=dataset_id,
            outcome=outcome,
            harness=harness,
            q=query,
            limit=limit,
        )
        return runs, "substring"

    out: list[TrajectoryRun] = []
    for hit in hits:
        run = store.get_run(str(hit.get("id") or ""), with_steps=False)
        if run is None:
            continue
        if dataset_id and run.dataset_id != dataset_id:
            continue
        if outcome and run.outcome != outcome:
            continue
        if harness and run.harness != harness:
            continue
        out.append(run)
        if len(out) >= limit:
            break
    return out, "semantic"

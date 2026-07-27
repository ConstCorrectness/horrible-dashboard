"""Task-queue handler for crawls, registered at import.

`backend/app.py` imports this module purely for the side effect of the
`register_handler` call at the bottom — the same arrangement the library uses.

**The queue is serial.** One worker drains it in order, so a 200-page crawl at one
request per second holds up every `ingest_source` behind it for a few minutes. That
is accepted rather than worked around: a crawl's durability already lives in
`crawl_pages`, so standing up a second worker pool would buy concurrency at the cost
of a whole scheduler. If it ever does bite, the fix is to persist the frontier and
process one wave per task, letting other work interleave — not another runner.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.modules.search.broadcast import publish_progress, publish_seed
from backend.modules.search.crawl import store
from backend.modules.search.crawl.crawler import crawl_seed
from backend.modules.tasks import queue

logger = logging.getLogger(__name__)


async def handle_crawl_seed(payload: dict[str, Any]) -> None:
    seed_id = str(payload.get("seed_id") or "")
    if not seed_id:
        logger.error("crawl_seed: no seed_id in %s", payload)
        return

    store.init_crawl_db()
    seed = store.get_seed(seed_id)
    if seed is None:
        logger.error("crawl_seed: unknown seed %s", seed_id)
        return

    store.finish_seed(seed_id, status="running", error=None, pages=seed["pages"])
    publish_seed(store.get_seed(seed_id) or seed)

    try:
        stats = await crawl_seed(
            seed_id,
            force=bool(payload.get("force")),
            on_progress=lambda s: publish_progress(s),
        )
    except Exception as exc:  # noqa: BLE001 — one bad seed must not kill the worker
        logger.exception("crawl of %s failed", seed_id)
        store.finish_seed(seed_id, status="failed", error=str(exc), pages=seed["pages"])
        publish_seed(store.get_seed(seed_id) or seed)
        return

    counts = store.seed_stats(seed_id)
    # A run that errored on some pages but indexed others is a partial success, and
    # saying so beats either "done" (hides a broken seed) or "failed" (hides work).
    status = "failed" if stats.errors and not stats.indexed else "done"
    store.finish_seed(
        seed_id,
        status=status,
        error="; ".join(stats.notes) or None,
        pages=counts["pages"],
    )
    publish_progress(stats.as_dict(), force=True)
    publish_seed(store.get_seed(seed_id) or seed)
    logger.info("crawl %s: %s", seed_id, stats.as_dict())


queue.register_handler("crawl_seed", handle_crawl_seed)

"""Web search: pluggable providers, an AI layer on top, and this node's own index.

The public surface, so callers don't reach into submodules:

- `router` — `/api/search/*`
- `register_search_tools()` — the `search` agent tool group, the built-in providers,
  and the `search` connector
- `push_crawl_events(conn)` — the `crawl` `/ws` pump
- `init_search_db()` — the cache and crawl tables

See `pipeline.py` for what the layer actually does, and `base.py` for the egress
rules, which differ by leg and are the easiest thing here to get wrong.
"""

from __future__ import annotations

from backend.modules.search.agent_tools import register_search_tools
from backend.modules.search.broadcast import push_crawl_events
from backend.modules.search.routes import router

__all__ = [
    "init_search_db",
    "push_crawl_events",
    "register_search_tools",
    "router",
]


def init_search_db() -> None:
    """Create the search caches and crawl tables. Idempotent."""
    from backend.modules.search.cache import init_cache_db
    from backend.modules.search.crawl.store import init_crawl_db

    init_cache_db()
    init_crawl_db()

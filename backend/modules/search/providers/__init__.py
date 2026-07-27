"""Built-in search providers.

Registration order is the tie-break order elsewhere (the first configured keyed
provider is what "auto" reaches for first), so it is deliberate: the agent-tuned APIs
first, then the keyless options, then this node's own index.
"""

from __future__ import annotations

from backend.modules.search.base import register_provider


def register_builtin_providers() -> None:
    """Put every built-in provider in the registry. Idempotent — registration is a
    dict write keyed by provider id, so calling this twice is harmless."""
    from backend.modules.search.providers.brave import BraveProvider
    from backend.modules.search.providers.crawl import CrawlProvider
    from backend.modules.search.providers.ddg import DdgProvider
    from backend.modules.search.providers.exa import ExaProvider
    from backend.modules.search.providers.searxng import SearxngProvider
    from backend.modules.search.providers.serper import SerperProvider
    from backend.modules.search.providers.tavily import TavilyProvider

    for provider in (
        TavilyProvider(),
        BraveProvider(),
        ExaProvider(),
        SerperProvider(),
        SearxngProvider(),
        CrawlProvider(),
        DdgProvider(),
    ):
        register_provider(provider)

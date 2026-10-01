"""Discover: one browse surface over the external catalogs the app works with.

Hugging Face (models, datasets, Spaces, papers), arXiv, GitHub, Kaggle, the MCP
registry, plugins, agent skills and documentation — each behind an adapter that
delegates to the module already owning that upstream, normalised to one item shape,
cached with in-flight coalescing and a last-good fallback. See
docs/modules/discover.mdx.
"""

from backend.modules.discover.routes import router

__all__ = ["router"]

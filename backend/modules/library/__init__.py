"""Knowledge library module: ingest blogs and notes into the shared vector store
for semantic search / RAG. See docs/modules/library.mdx."""

from backend.modules.library.broadcast import push_library_events
from backend.modules.library.routes import router

__all__ = ["push_library_events", "router"]

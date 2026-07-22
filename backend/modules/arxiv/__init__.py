"""ArXiv module: Atom-API search/detail + guarded PDF download into the library."""

from backend.modules.arxiv.routes import router
from backend.modules.arxiv.tools import register_arxiv_tools

__all__ = ["register_arxiv_tools", "router"]

"""symdex — the shared symbol/docs embedding index.

One ingestion pipeline, two projections: package symbols (modules/classes/
functions with signatures + docstrings), database schemas, and project docs are
embedded into the `symdex` LanceDB collection for semantic retrieval (the
`symbols.*` agent tools), while package symbols are ALSO projected into the
relational `code_symbols` prefix index so the editor's keystroke-hot completion
path gains doc snippets without an embedding lookup. See docs/modules/symdex.mdx.
"""

from backend.modules.symdex.index import push_symdex_events, symdex_index
from backend.modules.symdex.routes import router
from backend.modules.symdex.tools import register_agent_tools

__all__ = ["push_symdex_events", "register_agent_tools", "router", "symdex_index"]

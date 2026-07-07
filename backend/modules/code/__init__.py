"""Code-intelligence module: an app-owned tree-sitter symbol index (outlines +
cross-repo search) and the shared code-locus bus that wires the editor, outline
pane, `dash.code`, and the agent to one 'what am I looking at'. See
docs/modules/code.mdx."""

from backend.modules.code.index import code_index
from backend.modules.code.locus import (
    current_locus,
    handle_code_message,
    push_code_events,
    set_locus_from_backend,
)
from backend.modules.code.routes import router
from backend.modules.code.semantic import semantic_index

__all__ = [
    "code_index",
    "current_locus",
    "handle_code_message",
    "push_code_events",
    "router",
    "semantic_index",
    "set_locus_from_backend",
]

"""Documentation lookup: the server half of the editor/notebook docs popup.

Resolves a symbol through a chain of sources — the live notebook kernel, the
symdex package/stdlib index, and the open web — and hands back rendered-ready
markdown. The `lsp` source is resolved in the frontend, which is where the LSP
client lives. See docs/modules/docs-popup.mdx.
"""

from backend.modules.docs.routes import router

__all__ = ["router"]

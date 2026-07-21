"""MCP (Model Context Protocol) client module.

Connects this node to third-party MCP servers and projects each one into the agent as a
`mcp-<id>` tool group — see `bridge.py` for why that single naming choice is the whole
integration. `transport.py` explains why we ship our own stdio transport instead of the
SDK's. See docs/modules/mcp.mdx.
"""

from backend.modules.mcp.routes import router

__all__ = ["router"]

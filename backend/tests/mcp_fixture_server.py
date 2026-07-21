"""A tiny stdio MCP server used as a fixture by test_mcp.py.

Run as a subprocess by the integration tests, so the transport, the session manager and
the bridge are exercised against a real MCP handshake rather than a mock. It declares
one read-only tool and one write tool so the permission mapping can be asserted.
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("fixture", instructions="Fixture server used by the test suite.")


@mcp.tool(annotations={"readOnlyHint": True})
def peek(key: str) -> str:
    """Read a value. Read-only."""
    return f"value:{key}"


@mcp.tool()
def poke(key: str, value: str) -> str:
    """Write a value. Has side effects."""
    return f"wrote {key}={value}"


@mcp.tool()
def boom() -> str:
    """Always fails, for the error path."""
    raise RuntimeError("intentional failure")


if __name__ == "__main__":
    mcp.run(transport="stdio")

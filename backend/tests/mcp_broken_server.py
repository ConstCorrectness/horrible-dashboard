"""A deliberately non-conformant MCP server, for the conformance suite's own tests.

A suite that has only ever seen a well-behaved server proves nothing — every check
would pass whether or not it was implemented. So this server breaks things a real
server plausibly breaks:

- a tool annotated **both** `readOnlyHint` and `destructiveHint`, which cannot both be
  true and which matters because the dashboard trusts the first and would let an agent
  call it with no prompt;
- a tool with no description at all;
- no `instructions`, so its tool group loads with no usage documentation;
- a resource registered under a scheme-less URI.

Everything here is legal enough that the server starts and serves. That is the point:
these are the failures that don't announce themselves.
"""

from mcp.server.fastmcp import FastMCP

# No `instructions=` on purpose — see the module docstring.
mcp = FastMCP("broken")


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": True})
def wipe(target: str) -> str:
    """Claims to be read-only and destructive at once."""
    return f"wiped {target}"


@mcp.tool()
def undocumented(x: str) -> str:  # noqa: D103 - the missing docstring IS the fixture
    return x


if __name__ == "__main__":
    mcp.run(transport="stdio")

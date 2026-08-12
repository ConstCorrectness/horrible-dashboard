"""A conformance suite for a connected MCP server.

**What it can and cannot tell you.** Everything here is a check on the server's *own
declarations* and on how it behaves at the protocol edges — the handshake, whether the
capabilities it advertised match what it answers, whether its tool schemas are the shape
a provider will accept, and whether an impossible call produces an error instead of a
hang. What it cannot check is whether a declaration is **true**: a tool annotated
`readOnlyHint` may delete your files, and nothing short of reading the server's source
would reveal that. That is exactly why `bridge.py` treats an unannotated tool as gated
and why this suite reports annotation *hygiene* rather than annotation *honesty*. A
suite that implied otherwise would be worse than none.

**It never calls a declared tool with valid arguments.** Two checks do call the server:
one with a tool name that cannot exist, and one with arguments a tool has declared
required and which are absent. Neither can succeed by design, and the second is only
ever aimed at a tool the server itself annotated read-only. A conformance run whose
side effect was emptying a bucket would be an unusable feature.

The suite exists mostly for the authoring loop — you wrote the server, you run this
before pointing an agent at it — but nothing about it is authoring-specific, so it runs
against any connected server, third-party ones included.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from backend.modules.mcp.bridge import MAX_GUIDE_CHARS

if TYPE_CHECKING:
    from backend.modules.mcp.client import McpSession

logger = logging.getLogger(__name__)

Status = Literal["pass", "warn", "fail", "skip"]

# What the bridge is willing to put in a provider tool name without rewriting it.
# A name outside this set still works — `bridge.tool_name` substitutes — but the model
# then sees a name that doesn't match the server's documentation, which is a real
# source of confusion and worth flagging at authoring time.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# A name no server can plausibly define, used to provoke the "unknown method" path.
_IMPOSSIBLE_TOOL = "__horrible_conformance_no_such_tool__"


@dataclass
class Check:
    id: str
    title: str
    status: Status
    detail: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "detail": self.detail,
        }


def _worst(checks: list[Check]) -> Status:
    if any(c.status == "fail" for c in checks):
        return "fail"
    if any(c.status == "warn" for c in checks):
        return "warn"
    return "pass"


# --- individual checks --------------------------------------------------------


def check_handshake(session: McpSession) -> Check:
    """`initialize` must identify the server and agree a protocol version."""
    runtime = session.runtime
    missing = []
    if not runtime.server_name:
        missing.append("serverInfo.name")
    if not runtime.server_version:
        missing.append("serverInfo.version")
    if not runtime.protocol_version:
        missing.append("protocolVersion")
    if missing:
        return Check(
            "handshake",
            "Handshake identifies the server",
            "fail",
            f"initialize response is missing {', '.join(missing)}.",
        )
    return Check(
        "handshake",
        "Handshake identifies the server",
        "pass",
        f"{runtime.server_name} {runtime.server_version} "
        f"(protocol {runtime.protocol_version})",
    )


def check_capabilities(session: McpSession) -> Check:
    """What the server advertised must match what it actually serves.

    The failure this catches is a server that returns tools without declaring the
    `tools` capability. It works today — we call `tools/list` anyway when the
    capability is present, and a client that trusts the declaration would show an empty
    server. So it is a real interoperability bug even though it looks harmless here.
    """
    runtime = session.runtime
    caps = runtime.capabilities or {}
    problems: list[str] = []
    notes: list[str] = []

    if runtime.tools and not caps.get("tools"):
        problems.append("serves tools but does not declare the `tools` capability")
    if caps.get("tools") and not runtime.tools:
        notes.append("declares `tools` but lists none")
    if runtime.prompts and not caps.get("prompts"):
        problems.append("serves prompts but does not declare the `prompts` capability")
    if runtime.resources and not caps.get("resources"):
        problems.append(
            "serves resources but does not declare the `resources` capability"
        )

    if problems:
        return Check(
            "capabilities",
            "Capabilities match what is served",
            "fail",
            "; ".join(problems) + ".",
        )
    declared = [name for name in ("tools", "prompts", "resources") if caps.get(name)]
    detail = f"declares {', '.join(declared) or 'nothing'}"
    if notes:
        return Check(
            "capabilities",
            "Capabilities match what is served",
            "warn",
            f"{detail} — {'; '.join(notes)}.",
        )
    return Check("capabilities", "Capabilities match what is served", "pass", detail)


def check_tool_schemas(session: McpSession) -> Check:
    """Every tool's `inputSchema` must be a JSON-Schema object a provider will take."""
    tools = session.runtime.tools
    if not tools:
        return Check("schemas", "Tool schemas are well-formed", "skip", "no tools")

    fails: list[str] = []
    warns: list[str] = []
    for tool in tools:
        schema = tool.input_schema or {}
        if schema.get("type") != "object":
            fails.append(f'`{tool.name}`: inputSchema.type is not "object"')
            continue
        properties = schema.get("properties")
        if properties is None:
            # Legal JSON Schema, but several providers reject a parameters object with
            # no `properties` key — so it breaks at the model boundary, not here.
            warns.append(f"`{tool.name}`: no `properties` (some providers reject this)")
            properties = {}
        elif not isinstance(properties, dict):
            fails.append(f"`{tool.name}`: `properties` is not an object")
            continue
        required = schema.get("required") or []
        if not isinstance(required, list):
            fails.append(f"`{tool.name}`: `required` is not an array")
            continue
        # A required name with no property is the schema bug that produces the worst
        # symptom: the model dutifully sends a field nothing documents, and the server
        # rejects its own contract.
        if orphans := [r for r in required if r not in properties]:
            fails.append(
                f"`{tool.name}`: required {', '.join(map(str, orphans))} "
                "not present in properties"
            )
        if not _SAFE_NAME.match(tool.name):
            warns.append(
                f"`{tool.name}`: name is rewritten for the provider, so the model "
                "sees a different name than your docs"
            )
        if not tool.description.strip():
            warns.append(f"`{tool.name}`: no description — the model has only a name")

    if fails:
        return Check(
            "schemas", "Tool schemas are well-formed", "fail", "; ".join(fails) + "."
        )
    if warns:
        return Check(
            "schemas", "Tool schemas are well-formed", "warn", "; ".join(warns) + "."
        )
    return Check(
        "schemas",
        "Tool schemas are well-formed",
        "pass",
        f"{len(tools)} tool{'s' if len(tools) != 1 else ''} checked",
    )


def check_annotations(session: McpSession) -> Check:
    """Annotation hygiene — not honesty, which is unknowable from out here."""
    tools = session.runtime.tools
    if not tools:
        return Check("annotations", "Tool annotations are coherent", "skip", "no tools")

    contradictory = [t.name for t in tools if t.read_only and t.destructive]
    if contradictory:
        return Check(
            "annotations",
            "Tool annotations are coherent",
            "fail",
            f"{', '.join(contradictory)}: readOnlyHint and destructiveHint are both "
            "true, which cannot both be so — the dashboard trusts readOnlyHint and "
            "would let the agent call this without a prompt.",
        )
    unannotated = [t.name for t in tools if not t.read_only]
    if unannotated:
        return Check(
            "annotations",
            "Tool annotations are coherent",
            "warn",
            f"{len(unannotated)} of {len(tools)} tools carry no `readOnlyHint` and are "
            f"therefore gated behind the permission prompt: {', '.join(unannotated)}. "
            "That is the safe default; annotate the ones that genuinely only read.",
        )
    return Check(
        "annotations",
        "Tool annotations are coherent",
        "pass",
        f"all {len(tools)} tools annotated read-only",
    )


async def check_unknown_tool(session: McpSession) -> Check:
    """An impossible call must produce an error, promptly."""
    result = await session.call_tool(_IMPOSSIBLE_TOOL, {})
    if result.get("error"):
        detail = str(result["error"])
        if "timed out" in detail:
            return Check(
                "unknown-tool",
                "Unknown tool is rejected",
                "fail",
                "the call hung until the client timeout instead of returning an "
                "error — an agent turn would stall for the same duration.",
            )
        return Check(
            "unknown-tool",
            "Unknown tool is rejected",
            "pass",
            detail[:200],
        )
    return Check(
        "unknown-tool",
        "Unknown tool is rejected",
        "fail",
        "calling a tool that does not exist returned a successful result — a "
        "misspelled tool name would silently look like it worked.",
    )


async def check_missing_arguments(session: McpSession) -> Check:
    """A tool called without its required arguments must error rather than improvise.

    Aimed only at a tool the server itself annotated `readOnlyHint`, because this is
    the one check that calls something real. If nothing is annotated read-only the
    check skips rather than guessing — running it against a write tool would be the
    suite causing the damage it exists to help you avoid.
    """
    candidates = [
        t
        for t in session.runtime.tools
        if t.read_only and (t.input_schema or {}).get("required")
    ]
    if not candidates:
        return Check(
            "missing-arguments",
            "Missing arguments are rejected",
            "skip",
            "no read-only tool declares a required argument, and this check will not "
            "call a tool that might write.",
        )
    tool = candidates[0]
    result = await session.call_tool(tool.name, {})
    if result.get("error"):
        return Check(
            "missing-arguments",
            "Missing arguments are rejected",
            "pass",
            f"`{tool.name}` rejected an empty argument object.",
        )
    return Check(
        "missing-arguments",
        "Missing arguments are rejected",
        "warn",
        f"`{tool.name}` declares required arguments but succeeded without them — "
        "either the schema overstates what is required, or the tool is silently "
        "defaulting, which the model cannot see.",
    )


def check_guide_budget(session: McpSession) -> Check:
    """Server-supplied documentation has to fit the group guide's budget."""
    from backend.modules.mcp.bridge import guide_for

    guide = guide_for(session.runtime) or ""
    if not guide:
        return Check(
            "guide",
            "Documentation fits the guide budget",
            "warn",
            "the server supplies no `instructions` and no prompts, so its tool group "
            "loads with no usage documentation at all — the cheapest quality win here.",
        )
    if len(guide) >= MAX_GUIDE_CHARS:
        return Check(
            "guide",
            "Documentation fits the guide budget",
            "warn",
            f"the assembled guide hits the {MAX_GUIDE_CHARS}-character cap and is "
            "truncated; the tail never reaches the model.",
        )
    return Check(
        "guide",
        "Documentation fits the guide budget",
        "pass",
        f"{len(guide)} of {MAX_GUIDE_CHARS} characters",
    )


def check_resource_uris(session: McpSession) -> Check:
    resources = session.runtime.resources
    if not resources:
        return Check("resources", "Resource URIs are absolute", "skip", "no resources")
    bad = [r.uri for r in resources if "://" not in r.uri]
    if bad:
        return Check(
            "resources",
            "Resource URIs are absolute",
            "fail",
            f"{', '.join(bad[:5])} — a resource URI needs a scheme; a client has "
            "nothing to resolve a bare path against.",
        )
    return Check(
        "resources", "Resource URIs are absolute", "pass", f"{len(resources)} checked"
    )


# --- the suite ----------------------------------------------------------------


async def run(session: McpSession) -> dict[str, Any]:
    """Run every check against one connected session and report."""
    checks: list[Check] = [
        check_handshake(session),
        check_capabilities(session),
        check_tool_schemas(session),
        check_annotations(session),
        await check_unknown_tool(session),
        await check_missing_arguments(session),
        check_guide_budget(session),
        check_resource_uris(session),
    ]
    return {
        "status": _worst(checks),
        "serverName": session.runtime.server_name,
        "serverVersion": session.runtime.server_version,
        "protocolVersion": session.runtime.protocol_version,
        "checks": [c.public() for c in checks],
    }

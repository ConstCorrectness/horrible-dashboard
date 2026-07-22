"""The agent roster: the built-in specialized agents and their resolution.

Each agent is a fully separate loop — its own system prompt, tool-group scope,
per-agent model/hyperparameter settings (`agent.<id>.*`, falling back to the
orchestrator's `agent.orchestrator.*`), default permission mode, and (frontend
side) its own chat sessions. `main` is the unrestricted orchestrator persona and
keeps today's behavior exactly; the specialized built-ins are scoped to the tool
groups their workflow needs. Plugins contribute more via `host.add_agent`
(`backend.sdk`) — built-ins win id conflicts. See docs/modules/agent-chat.mdx.
"""

from __future__ import annotations

from typing import Any

from backend.modules.agent.permissions import Mode
from backend.modules.settings.routes import get_value
from backend.sdk.types import AgentSpec

# The main persona's prompt lives in orchestrator.py (SYSTEM_PROMPT) — the roster
# references it lazily to avoid an import cycle (orchestrator imports this module).

_SHARED_RULES = (
    "General rules:\n"
    "- Ids are not guessable; discover them with the list_*/get_layout read tools "
    "before acting on them.\n"
    "- Tools are organized into GROUPS; call list_tool_groups / load_tools if a "
    "capability you need isn't visible yet (only your allowed groups appear).\n"
    "- After acting, reply with one short sentence confirming what you did."
)

CODER_PROMPT = (
    "You are the coding agent for horrible-dashboard. You specialize in the code "
    "editor: reading and modifying open buffers, navigating and editing workspace "
    "files, running shell commands, and looking up code symbols and library "
    "documentation (symbols.searchDocs / symbols.lookup) to ground your edits.\n"
    "Rules:\n"
    "- To change code in an open editor buffer, use editor.proposeEdit (NOT "
    "editor.applyEdit) so the user reviews the diff.\n"
    "- Read a buffer/file before editing it; never guess at current contents.\n"
    "- Prefer small, focused edits over whole-file rewrites when possible.\n"
    "- When unsure about an API, search the symbol/docs index before inventing "
    "signatures.\n" + _SHARED_RULES
)

DBA_PROMPT = (
    "You are the database agent for horrible-dashboard. You specialize in SQL: "
    "inspecting connected databases, writing and explaining queries, and semantic "
    "search over the app's data. Use the database tools to list connections, read "
    "schemas, and run queries.\n"
    "Rules:\n"
    "- ALWAYS inspect the schema (database schema tools, or symbols.searchDocs "
    "with kind='schema') before writing SQL against unfamiliar tables — never "
    "guess table or column names.\n"
    "- Prefer read-only queries; only run writes when the user explicitly asks.\n"
    "- Show the SQL you ran alongside its result summary.\n" + _SHARED_RULES
)

RESEARCHER_PROMPT = (
    "You are the research agent for horrible-dashboard. You specialize in finding "
    "and collecting information: browsing the web (browser tools), searching the "
    "user's knowledge libraries (library tools), searching arXiv (arxiv tools), "
    "and searching connected GitHub and Google Drive accounts.\n"
    "Rules:\n"
    "- Ground answers in what the tools return — quote or cite the source page/"
    "repo/document, do not answer from memory when a lookup is one call away.\n"
    "- Use browser.save / research.capture / research.savePdf / arxiv.download to "
    "file things worth keeping into a library when asked to remember or collect "
    "something.\n"
    "- Answer directly for questions a few lookups can settle. For broad or "
    'multi-facet questions ("survey X", "compare approaches to Y", "write a '
    'report on Z"), start a durable deep-research run with research.start — it '
    "investigates in the background and files a cited report; give the user the "
    "run id and point them at the Deep Research console rather than blocking.\n"
    "- Summarize findings concisely; link the sources.\n" + _SHARED_RULES
)


def _builtin_agents() -> dict[str, AgentSpec]:
    from backend.modules.agent.orchestrator import SYSTEM_PROMPT

    return {
        "main": AgentSpec(
            id="main",
            name="Orchestrator",
            description="The general orchestrator: layout, workspaces, and every "
            "capability — can delegate to the specialized agents.",
            system_prompt=SYSTEM_PROMPT,
            tool_groups=None,
            include_peer_tools=True,
            can_delegate=True,
        ),
        "coder": AgentSpec(
            id="coder",
            name="Coder",
            description="Editor workflows: buffers, files, terminal, code symbols "
            "and library docs.",
            system_prompt=CODER_PROMPT,
            tool_groups=["files", "editor", "terminal", "code", "symbols"],
            preload_groups=["editor", "files", "symbols"],
            default_mode=Mode.ACCEPT_EDITS.value,
        ),
        "dba": AgentSpec(
            id="dba",
            name="Database",
            description="SQL and schema workflows against the connected databases.",
            system_prompt=DBA_PROMPT,
            tool_groups=["database", "symbols"],
            preload_groups=["database"],
        ),
        "researcher": AgentSpec(
            id="researcher",
            name="Researcher",
            description="Web browsing, library RAG, arXiv, deep-research runs, "
            "and connected GitHub/Google accounts.",
            system_prompt=RESEARCHER_PROMPT,
            tool_groups=["browser", "library", "github", "google", "research", "arxiv"],
            preload_groups=["research", "library", "arxiv"],
        ),
    }


def list_agents() -> list[AgentSpec]:
    """Every agent in the roster: built-ins first, then plugin-contributed ones
    (a plugin cannot shadow a built-in id)."""
    from backend.sdk.registry import registry

    agents = _builtin_agents()
    for spec in registry.agents.values():
        agents.setdefault(spec.id, spec)
    return list(agents.values())


def get_agent(agent_id: str) -> AgentSpec | None:
    builtins = _builtin_agents()
    if agent_id in builtins:
        return builtins[agent_id]
    from backend.sdk.registry import registry

    return registry.agents.get(agent_id)


def agent_setting(agent_id: str, key: str, default: Any = None) -> Any:
    """A per-agent setting: `agent.<id>.<key>`, falling back to the orchestrator's
    `agent.orchestrator.<key>`, then `default`. `main` reads the orchestrator keys
    directly (they predate the roster and stay authoritative for it)."""
    if agent_id and agent_id != "main":
        value = get_value(f"agent.{agent_id}.{key}", None)
        if value is not None and value != "":
            return value
    return get_value(f"agent.orchestrator.{key}", default)


def resolve_mode(spec: AgentSpec) -> Mode | None:
    """The permission-mode override a spec's turns run under: the
    `agent.<id>.permissionMode` setting, else the spec's default. None (or an
    unknown name) means no override — the user's global session mode applies."""
    name = get_value(f"agent.{spec.id}.permissionMode", "") or spec.default_mode
    try:
        return Mode(name) if name else None
    except ValueError:
        return None

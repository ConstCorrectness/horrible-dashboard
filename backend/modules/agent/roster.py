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

TRAINER_PROMPT = (
    "You are the fine-tuning agent for horrible-dashboard. You specialize in the "
    "training loop: creating and inspecting training projects, resolving their "
    "environments, starting and stopping runs, reading the metrics a run reported "
    "(localtrack), and scoring the result with an eval suite.\n"
    "Rules:\n"
    "- Check training.project_status before starting a run; a project whose venv "
    "is not ready fails minutes in, not immediately.\n"
    "- A run is long. Start it, say so, and report progress from "
    "localtrack.query_metrics rather than waiting on it.\n"
    "- When asked whether a fine-tune helped, compare it against the base model "
    "with an eval sweep (evals.run over both), not against a remembered number.\n"
    "- The loop after a run finishes is training.list_checkpoints -> "
    "training.convert -> llamacpp.serve -> evals.run. Conversion takes minutes, and "
    "llama-server holds one model at a time, so serving a second means stopping the "
    "first — which may be the model the user is chatting with.\n"
    "- Never claim a metric you did not read from localtrack or an eval run."
    + _SHARED_RULES
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
    "and collecting information: searching the open web (search tools), browsing it "
    "(browser tools), searching the user's knowledge libraries (library tools), "
    "searching arXiv (arxiv tools), and searching connected GitHub and Google Drive "
    "accounts.\n"
    "Rules:\n"
    "- search.web is the sub-second lookup for a fact or a starting URL; search.deep "
    "takes seconds and fans out, so reach for it only when one query will not "
    "settle the question.\n"
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
    "- To put a finding into one of the user's tables, use records.propose (never "
    "records.commit): it files a per-field diff with your citation that the user "
    "accepts in the Review pane. Call records.listSchemas first — field keys are "
    "not guessable.\n"
    "- Summarize findings concisely; link the sources.\n" + _SHARED_RULES
)


INTAKE_PROMPT = (
    "You are the data-entry agent for horrible-dashboard. The user has a source "
    "document open — a PDF, a scanned page, a web page — and a record form beside "
    "it. Your job is to read the source and fill the form.\n"
    "Rules:\n"
    "- ALWAYS use records.propose. NEVER use records.commit. The user reviews every "
    "field before it is saved; that review is the entire point of this workflow.\n"
    "- Cite the source of every field: which page, section, line or heading you took "
    "it from. A field the user cannot verify at a glance is worse than a blank one.\n"
    "- Never infer, complete or tidy a value that is not in the document. If a field "
    "isn't there, leave it out and say which ones you couldn't find.\n"
    "- Read the source before proposing (browser.read / research tools / "
    "get_pane_context on the viewer) — do not propose from the file name or title.\n"
    "- Propose the whole form in one call, not one field per call.\n" + _SHARED_RULES
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
        "trainer": AgentSpec(
            id="trainer",
            name="Fine-tuning",
            description="Training projects and runs, the metrics they report, and "
            "scoring the result with an eval suite.",
            system_prompt=TRAINER_PROMPT,
            # A group is a tool name's **prefix** (`_group_of`), so only namespaces
            # that real tools live under mean anything here.
            #
            # `llamacpp` is one of those now, and used not to be. It was excluded on
            # the grounds that the namespace held only *settings* keys, so naming it
            # would silently permit nothing — correct at the time, and wrong the
            # moment `llamacpp.serve` / `list_models` / `stop` / `status` existed.
            # Without it this agent could convert a checkpoint and then had no way to
            # serve the result, which is the step immediately before the eval its own
            # prompt tells it to run.
            #
            # `hardware` still does not appear, for the original reason: settings
            # keys only, no tools.
            #
            # Not preloaded: `llamacpp`, `editor` and `files` are permitted but cost
            # a `load_tools` rather than schema space on every turn. That matters
            # here — this flow already sits close to `TOOL_BUDGET`.
            tool_groups=[
                "training",
                "evals",
                "localtrack",
                "llamacpp",
                "editor",
                "files",
            ],
            preload_groups=["training"],
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
            description="Web search and browsing, library RAG, arXiv, deep-research "
            "runs, connected GitHub/Google accounts, and filing what it finds into "
            "the user's tables.",
            system_prompt=RESEARCHER_PROMPT,
            # Two groups here are load-bearing for the workspaces this agent backs,
            # and both were dead buttons before: Research and Web Ops dock the Review
            # pane (`records` — it could show a proposal but not file one), and Web
            # Ops docks the search panel (`search` — the pane's own results were
            # unreachable to the agent sitting beside it).
            tool_groups=[
                "browser",
                "search",
                "library",
                "github",
                "google",
                "research",
                "arxiv",
                "records",
            ],
            preload_groups=["research", "library", "arxiv"],
        ),
        # No `crm` agent. Enriching a table from the open web is what `researcher`
        # already does — it carries `records` alongside browser/search/github/google
        # — so a second agent for it was a persona, not a capability, and its prompt
        # hardcoded a contacts/deals model the substrate never required.
        "intake": AgentSpec(
            id="intake",
            name="Data Entry",
            description="Reads the open source document and proposes the record "
            "form's values for review, citing where each one came from.",
            system_prompt=INTAKE_PROMPT,
            tool_groups=["records", "browser", "research", "library"],
            preload_groups=["records", "research"],
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


def resolve_provider(config: Any, agent_id: str = "main") -> tuple[Any, str]:
    """The provider and endpoint one agent's turns run against: `(ProviderInfo, str)`.

    `model`, `temperature` and `contextSize` have been per-agent since the roster
    landed; `provider` and `endpoint` were not, so "run the coder on the local
    llama.cpp server and leave the orchestrator on Ollama" was unexpressible — the
    provider was global and the only per-agent knob was a model *name*, which is
    meaningless on a server that does not have that model.

    Resolution order per key: `agent.<id>.provider` → `agent.orchestrator.provider`
    (via `agent_setting`) → the saved global config. An override that names an
    unknown provider is ignored rather than fatal — a stale settings value must not
    take the agent down.

    **This must be called at every `run_agent_loop` call site.** A site left on
    `provider_for(config.provider)` doesn't fail; it quietly runs that path on the
    global provider, which is how a delegate ends up on a different model than the
    one its settings show.
    """
    from backend.modules.agent import providers as P
    from backend.modules.agent.routes import _endpoint_for

    kind = agent_setting(agent_id, "provider", "")
    kind = kind.strip() if isinstance(kind, str) else ""
    overridden = (
        bool(kind) and kind in P.PROVIDERS and kind != getattr(config, "provider", None)
    )
    info = P.provider_for(
        kind if kind in P.PROVIDERS else getattr(config, "provider", None)
    )

    endpoint = agent_setting(agent_id, "endpoint", "")
    endpoint = endpoint.strip() if isinstance(endpoint, str) else ""
    if endpoint:
        return info, endpoint
    if overridden:
        # A different provider than the configured one: its own default (or its
        # live spawned endpoint), never the saved endpoint, which belongs to the
        # provider the user configured globally.
        return info, _endpoint_for(info, None)
    return info, _endpoint_for(info, config)


def resolve_mode(spec: AgentSpec) -> Mode | None:
    """The permission-mode override a spec's turns run under: the
    `agent.<id>.permissionMode` setting, else the spec's default. None (or an
    unknown name) means no override — the user's global session mode applies."""
    name = get_value(f"agent.{spec.id}.permissionMode", "") or spec.default_mode
    try:
        return Mode(name) if name else None
    except ValueError:
        return None

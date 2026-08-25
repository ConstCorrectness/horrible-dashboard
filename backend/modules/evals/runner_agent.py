"""The in-node runner: a real orchestrator turn with a fake browser on the end.

This is the design decision the module stands on, so it is worth being explicit
about what is *not* happening here. There is no second tool-calling loop, no
reimplementation of tool selection, and no hand-built prompt. A case runs through
`run_agent_loop` — the same function a chat turn and a flow's Agent node run
through — because the thing under test is not "can a model emit JSON", it is
**can this model pick the right tool out of the catalog this app actually shows
it**. That catalog is assembled by `_select_tools` from progressive disclosure,
group guides, the preload heuristics and `TOOL_BUDGET`, and a harness that built
its own tool list would be measuring a catalog nobody ever ships.

## The seam

`_call_frontend_tool` registers a future in `conn.pending[call_id]` and *then*
awaits `conn.send_json`. So a connection object whose `send_json` resolves that
future is a complete stand-in for a browser, with no monkeypatching of orchestrator
internals and nothing to keep in sync. `OfflineConnection` does exactly that, and
records every call on the way past — it is aliased here as `EvalConnection`, which
is the name it earned.

## Why tools are simulated, never executed

A case supplies `fixtures`: what each tool returns. Letting the real tool run would
make a suite destructive (`files.delete` is in the catalog), unrepeatable (the
result would depend on what is open), and slow. What is being graded is the
*choice*, and the fixture is what lets the conversation continue past it so a
multi-step case can be graded at all.

## Why the permission mode is forced

A gated tool prompts, and a prompt in a headless run either hangs or is denied —
either way the case would score the local machine's saved permission rules rather
than the model. So the run forces `AUTONOMOUS` and auto-approves, and records that
a gate fired. An eval whose result depends on who ran it is not an eval.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from backend.modules.agent.offline_conn import OfflineConnection
from backend.modules.evals.models import EvalCase, ToolCall

logger = logging.getLogger(__name__)

#: A case that has not finished in this long is a failure worth seeing rather than
#: a sweep that never ends. Generous: a small model on CPU is genuinely slow, and a
#: false timeout reads as "the model cannot do this" — the worst kind of wrong
#: result, because it is indistinguishable from a real one.
CASE_TIMEOUT_S = 300.0


#: The browser stand-in. It used to be defined here; it now lives in
#: `agent/offline_conn.py` because agentpedia's fork needs the identical object,
#: and agentpedia importing this module's internals would break the rule that
#: modules do not reach into each other. The name is kept because it is what the
#: evals suite and the export path call it.
EvalConnection = OfflineConnection


def _tools_for_case(
    conn: Any, case: EvalCase, spec: Any
) -> tuple[list[dict], set[str] | None]:
    """The tool list and the active-group set a case starts with.

    Returns `(tools, active_groups)`. `active_groups` is `None` for the non-
    progressive modes, which is what tells `run_agent_loop` to use the fixed list
    with direct dispatch instead of recomputing per round.
    """
    from backend.modules.agent.orchestrator import (
        _all_dynamic_tools,
        _group_of,
        _select_tools,
    )

    mode = case.expose.mode
    preload = set(case.expose.preload)

    if mode == "progressive":
        # The shipped path: a small core plus the meta-tools, with `preload` as the
        # head start a roster agent's `preload_groups` gives it.
        return _select_tools(conn, preload, spec), preload

    if mode == "explicit":
        # Only the named groups. `active_groups=None` so the model cannot load its
        # way out of the restriction — the point is to isolate one capability.
        tools = [
            t
            for t in _all_dynamic_tools(conn, spec)
            if _group_of(t["function"]["name"]) in preload
        ]
        return tools, None

    # `all`: every group at once, still capped by TOOL_BUDGET, which is precisely
    # the comparison that says whether progressive disclosure earns its keep.
    groups = {_group_of(t["function"]["name"]) for t in _all_dynamic_tools(conn, spec)}
    return _select_tools(conn, groups, spec), None


async def run_case(
    case: EvalCase,
    agent_tools: list[dict[str, Any]],
    *,
    provider: Any,
    endpoint: str,
    model: str,
    temperature: float = 0.0,
    system: str = "",
    agent_id: str = "main",
) -> Any:
    """Put one case to one model and grade what it did.

    `temperature` defaults to 0: a sweep is a measurement, and a suite whose score
    moves by three points between identical runs cannot tell a fine-tune from
    sampling noise. A case that wants variance can ask for it.
    """
    from backend.modules.agent.orchestrator import (
        _active_editor_message,
        _guides_message,
        _skills_message,
        _workspace_context_message,
        run_agent_loop,
    )
    from backend.modules.agent.permissions import Mode
    from backend.modules.agent.roster import get_agent
    from backend.modules.evals.graders import result_for

    spec = get_agent(agent_id)
    conn = EvalConnection(agent_tools, case.fixtures)
    turn_id = uuid.uuid4().hex[:12]

    tools, active_groups = _tools_for_case(conn, case, spec)
    offered = len(tools)

    # Assembled the way `run_agent_turn` assembles a chat turn, not simplified. The
    # group guides in particular are most of what makes progressive disclosure
    # work — a preloaded group never triggers `load_tools`, so this system message
    # is the only place its usage notes reach the model. A harness that omitted
    # them would measure a model that had never been told how the tools behave and
    # report it as the model being bad at tool use.
    #
    # Several system messages, deliberately: that is the production shape, and the
    # provider seam is what flattens them for a strict chat template.
    skills_msg = _skills_message()
    guides_msg = _guides_message(active_groups) if active_groups else None
    workspace_msg = _workspace_context_message(case.context or None)
    editor_msg = _active_editor_message(case.context or None)

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system or spec.system_prompt},
        *([skills_msg] if skills_msg else []),
        *([guides_msg] if guides_msg else []),
        *case.history,
        *([workspace_msg] if workspace_msg else []),
        *([editor_msg] if editor_msg else []),
        {"role": "user", "content": case.prompt},
    ]

    async def emit(reasoning: str, content: str) -> None:
        if reasoning:
            conn.reasoning.append(reasoning)

    started = time.monotonic()
    answer = ""
    error = ""
    try:
        answer = await asyncio.wait_for(
            run_agent_loop(
                conn,
                turn_id,
                messages,
                tools,
                provider,
                endpoint,
                model,
                emit,
                temperature=temperature,
                active_groups=active_groups,
                spec=spec,
                # Forced, not inherited: see the module docstring. A case must not
                # score the permission rules of whoever happens to run it.
                mode_override=Mode.AUTONOMOUS,
            ),
            timeout=CASE_TIMEOUT_S,
        )
    except TimeoutError:
        error = f"case timed out after {CASE_TIMEOUT_S:.0f}s"
    except Exception as exc:  # provider errors, malformed responses, anything
        # Recorded on the row rather than raised: one unreachable model must not
        # abandon a sweep across five others, and "this model errored on this case"
        # is itself a result worth having.
        logger.warning("evals: case %s failed against %s: %s", case.id, model, exc)
        error = f"{type(exc).__name__}: {exc}"

    duration_ms = (time.monotonic() - started) * 1000.0

    # Rounds are counted from the assistant turns the loop appended, which is the
    # only record of how many times the model was asked. A model that needed four
    # rounds to reach a tool another found in one has not passed the same way.
    rounds = sum(1 for m in messages if m.get("role") == "assistant")
    groups_loaded = (
        sorted(active_groups - set(case.expose.preload)) if active_groups else []
    )

    result = result_for(
        case,
        # `OfflineConnection` records plain `CallRecord`s — it is in the agent
        # module and must not import an evals model. The grader's shape is this
        # module's business, so the conversion is here.
        [ToolCall(name=c.name, arguments=c.arguments) for c in conn.calls],
        answer,
        rounds=rounds,
        tools_offered=offered,
        groups_loaded=groups_loaded,
        duration_ms=duration_ms,
        turn_id=turn_id,
    )
    if error:
        # An errored case is a failure whatever the grader thought of an empty
        # call list — most obviously for `no_call`, which an exception would
        # otherwise score as a pass because no tool was called.
        result.passed = False
        result.error = error
        result.detail = error
    return result

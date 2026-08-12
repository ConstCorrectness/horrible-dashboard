"""Local roster delegation: the `agent.delegate` backend tool.

The main orchestrator hands a task to a specialized agent (coder/dba/researcher
or a plugin agent). Unlike `agent.ask_peer` (a tool-less turn on a remote node),
the sub-agent runs HERE, on the same browser connection — its tool calls relay to
the same frontend and pass the same permission gate (under the sub-agent's own
mode) — with a fresh sub-turn id, so its stream stays out of the parent chat's
turn-scoped subscription. Progress deltas surface as `delegate_token` events
tagged with the parent turn. The caller receives the sub-agent's final answer as
an ordinary tool result. Delegation is one level deep by design: specialized
agents don't see the delegate tool (spec.can_delegate), so a delegate can't
delegate. See docs/modules/agent-chat.mdx.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

import httpx

from backend.modules.agent import roster
from backend.modules.agent.routes import _load_config
from backend.modules.ws import WsConnection

logger = logging.getLogger(__name__)

# A delegated turn is a whole tool-calling loop (model rounds + tool calls + maybe
# permission prompts), so it gets a far longer leash than a single tool call.
DELEGATE_TIMEOUT_S = 300.0


async def run_delegate(
    conn: WsConnection, parent_turn_id: str, agent_id: str, prompt: str
) -> dict[str, Any]:
    """Run `prompt` as a full turn of the specialized agent `agent_id`; return
    `{agent, answer}` or `{error}` as the delegate tool's result."""
    from backend.modules.agent import orchestrator

    if not agent_id or not prompt:
        return {"error": "agent.delegate needs agentId and prompt"}
    spec = roster.get_agent(agent_id)
    if spec is None:
        known = [a.id for a in roster.list_agents() if a.id != "main"]
        return {"error": f"unknown agent '{agent_id}' (available: {', '.join(known)})"}
    if spec.id == "main" or spec.can_delegate:
        # No self/loop delegation: the target must be a leaf specialist.
        return {"error": f"cannot delegate to '{agent_id}'"}
    config = _load_config()
    if config is None:
        return {"error": "agent not configured"}
    # The delegate's own provider, not the caller's: a specialist pinned to the
    # node's llama.cpp server must still land there when `main` hands it work.
    info, endpoint = roster.resolve_provider(config, spec.id)
    model = orchestrator._orchestrator_model(config.model, spec.id)
    active_groups = set(spec.preload_groups)
    guides_msg = orchestrator._guides_message(active_groups)
    # A delegate gets the same skill catalog the main agent does. Withholding it would
    # mean "review this like I taught you" works when the user asks directly and
    # silently doesn't when the orchestrator hands the same task to `coder` — and the
    # scope check in `use_skill` already stops a skill widening a specialist's reach.
    skills_msg = orchestrator._skills_message()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": spec.system_prompt},
        *([skills_msg] if skills_msg else []),
        *([guides_msg] if guides_msg else []),
        {"role": "user", "content": prompt},
    ]
    sub_turn_id = f"{parent_turn_id}:{spec.id}:{uuid.uuid4().hex[:6]}"

    async def emit(reasoning: str, content: str) -> None:
        # Surface the sub-agent's answer stream to the UI under the PARENT turn so
        # the chat can render nested progress; reasoning stays server-side.
        if content:
            await conn.send_json(
                orchestrator._evt(
                    "delegate_token",
                    {
                        "turnId": parent_turn_id,
                        "agentId": spec.id,
                        "delta": content,
                    },
                )
            )

    try:
        text = await asyncio.wait_for(
            orchestrator.run_agent_loop(
                conn,
                sub_turn_id,
                messages,
                [],  # progressive disclosure recomputes per round
                info,
                endpoint,
                model,
                emit,
                temperature=orchestrator._tool_temperature(spec.id),
                context_size=orchestrator._tool_context_size(spec.id),
                max_tokens=orchestrator._tool_max_tokens(spec.id),
                top_p=orchestrator._tool_top_p(spec.id),
                active_groups=active_groups,
                spec=spec,
                mode_override=roster.resolve_mode(spec),
                # Links this sub-turn to the turn that delegated it, so the
                # interpretability pane can show the handoff as a tree rather than
                # as unrelated siblings.
                parent_turn_id=parent_turn_id,
            ),
            timeout=DELEGATE_TIMEOUT_S,
        )
    except TimeoutError:
        return {"error": f"agent '{agent_id}' timed out"}
    except httpx.HTTPError as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    return {"agent": spec.id, "answer": text}

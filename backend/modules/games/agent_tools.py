"""Backend agent tools for driving a game manually (the `games.policy = "manual"`
path). These let the node's *own* agent play its seat by choosing among the legal
actions the server offered — the same constrained-choice contract the auto-play
policy uses, exposed to the orchestrator.

With `games.policy` set to `random`/`agent`, the node auto-plays and these tools
just report "no pending turn"; they're the hook for an agent-driven seat.
"""

from __future__ import annotations

from typing import Any

from backend.modules.games.client import games_client
from backend.sdk.registry import registry
from backend.sdk.types import AgentTool


async def _get_observation(_args: dict[str, Any]) -> Any:
    turn = games_client.current_turn()
    if turn is None:
        return {"error": "no pending turn — it is not your move"}
    return {
        "game_id": turn.get("game_id"),
        "seat": turn.get("seat"),
        "observation": turn.get("observation"),
        "legal_actions": turn.get("legal_actions"),
    }


async def _choose_action(args: dict[str, Any]) -> Any:
    action_id = str(args.get("action_id") or "")
    if not action_id:
        return {"error": "action_id is required"}
    return await games_client.submit_action(action_id)


_TOOLS = [
    AgentTool(
        name="game.getObservation",
        description=(
            "Get the current game observation and the list of legal actions for "
            "your seat. Call this before game.chooseAction."
        ),
        parameters={},
        required=[],
        handler=_get_observation,
        group="games",
    ),
    AgentTool(
        name="game.chooseAction",
        description=(
            "Play your move by choosing one legal action by its id (from "
            "game.getObservation). The server re-validates the choice."
        ),
        parameters={
            "action_id": {"type": "string", "description": "id of a legal action"},
        },
        required=["action_id"],
        handler=_choose_action,
        side_effect=True,
        specifier_template="{action_id}",
        group="games",
    ),
]


def register_agent_tools() -> None:
    """Inject the games tools into the shared agent-tool registry (grouped under
    `games`, disclosed progressively by the orchestrator)."""
    for tool in _TOOLS:
        registry.agent_tools[tool.name] = tool

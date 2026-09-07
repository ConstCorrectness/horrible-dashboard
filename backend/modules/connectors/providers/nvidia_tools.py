"""Agent tools for the NVIDIA connector (group `nvidia`).

The group name must equal the connector id — the orchestrator groups tools by their
name *prefix* and `AgentTool.group` does not name the group, so a mismatch silently
splits these off from the connector's blurb and guide.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.modules.connectors.providers import nvidia
from backend.sdk.registry import registry
from backend.sdk.types import AgentTool

logger = logging.getLogger(__name__)


async def _search_models(args: dict[str, Any]) -> dict[str, Any]:
    try:
        found = await nvidia.search_catalog(
            str(args.get("query") or ""), int(args.get("limit") or 20)
        )
    except nvidia.NvidiaError as exc:
        return {"error": str(exc)}
    return {"models": found}


async def _list_endpoints(_args: dict[str, Any]) -> dict[str, Any]:
    """What this key can actually call, and how to point the app at it."""
    try:
        models = await nvidia.list_models()
    except nvidia.NvidiaError as exc:
        return {"error": str(exc)}
    return {
        "endpoint": nvidia.NIM_BASE,
        "models": models,
        "note": (
            "NIM speaks the OpenAI chat API, so these work anywhere this app takes "
            "an OpenAI-compatible endpoint — the chat provider, an eval target, or "
            "the datasets builder's synthesize step."
        ),
    }


TOOLS: list[AgentTool] = [
    AgentTool(
        name="nvidia.searchModels",
        description="Search the NVIDIA NGC catalog for models and resources.",
        handler=_search_models,
        parameters={
            "query": {"type": "string", "description": "Search text"},
            "limit": {"type": "integer", "description": "Max results (default 20)"},
        },
        required=["query"],
        group="nvidia",
    ),
    AgentTool(
        name="nvidia.nimEndpoints",
        description="List the NVIDIA NIM models this account can call, with the "
        "OpenAI-compatible endpoint to reach them at.",
        handler=_list_endpoints,
        parameters={},
        required=[],
        group="nvidia",
    ),
]


def register_agent_tools() -> None:
    for tool in TOOLS:
        registry.agent_tools[tool.name] = tool

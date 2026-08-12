"""Skills: reusable instructions in Anthropic's SKILL.md format.

The module's public surface is its router; the agent side is reached through
`backend.modules.skills.agent` by the orchestrator. See docs/modules/skills.mdx.
"""

from backend.modules.skills.routes import router

__all__ = ["router"]

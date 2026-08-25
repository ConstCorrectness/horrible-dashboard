"""Agentpedia — one agent turn, steppable.

An `agent_turns` snapshot says what the model was shown. A trajectory says what it
did. The telemetry ring says what went over the wire. All three existed; nothing
put them beside each other, which is what this module is.

It owns no store. See docs/modules/agentpedia.mdx.
"""

from backend.modules.agentpedia.routes import router

__all__ = ["router"]

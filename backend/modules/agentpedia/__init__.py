"""Agentpedia — one agent turn, steppable.

An `agent_turns` snapshot says what the model was shown. A trajectory says what it
did. The telemetry ring says what went over the wire. All three existed; nothing
put them beside each other, which is what this module is.

It owns one table and no more: the fork edge (`store.py`), which records that one
turn is a counterfactual of another. Everything else it shows is joined at read
time from the module that already owns it. See docs/modules/agentpedia.mdx.
"""

from backend.modules.agentpedia.agent_tools import register_agent_tools
from backend.modules.agentpedia.routes import router

__all__ = ["register_agent_tools", "router"]

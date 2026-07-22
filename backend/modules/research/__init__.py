"""Research module: page capture, PDF pipeline, Obsidian export, deep research."""

from backend.modules.research.agent_tools import register_research_tools
from backend.modules.research.routes import router

__all__ = ["register_research_tools", "router"]

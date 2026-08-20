"""Evaluation and benchmarking: measuring what a model does with this app's tools.

See docs/modules/evals.mdx.
"""

from backend.modules.evals.agent_tools import register_agent_tools
from backend.modules.evals.routes import router

__all__ = ["register_agent_tools", "router"]

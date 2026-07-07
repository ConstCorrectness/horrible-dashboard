"""Git provenance module: blame → commit → the agent conversation that wrote a line,
plus a review view over agent-authored commits. See docs/modules/git.mdx."""

from backend.modules.git.routes import router

__all__ = ["router"]

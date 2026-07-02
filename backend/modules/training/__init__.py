"""Training module: notebook-driven neural-network training.

Projects (Kaggle/HF/Gym via the pluggable provider layer) live in per-directory
roots with their own uv venv; notebooks execute on Jupyter kernels spawned from
those venvs; metrics/frames/model graphs stream to the UI over the `training`
`/ws` channel. See docs/modules/training.mdx.
"""

from backend.modules.training.agent_tools import register_agent_tools
from backend.modules.training.routes import router
from backend.modules.training.stream import subscribe_conn as subscribe_training_conn

__all__ = ["register_agent_tools", "router", "subscribe_training_conn"]

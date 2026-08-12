"""One capability probe driving defaults everywhere.

The app runs on integrated graphics and on high-end NVIDIA, and until now every
heavy surface guessed independently and guessed "laptop". This module is the one
place that asks what the machine is, and the one place that turns that answer
into the numbers `llama-server`, the tracer and the training surface use.

Its standing rule is the suite's: **never assume a GPU; never hide one that
exists** — and, third, never render "we could not ask" as "there is none". See
docs/modules/hardware.mdx.
"""

from backend.modules.hardware.probe import Defaults, Profile, defaults, get_profile
from backend.modules.hardware.routes import router

__all__ = ["Defaults", "Profile", "defaults", "get_profile", "router"]

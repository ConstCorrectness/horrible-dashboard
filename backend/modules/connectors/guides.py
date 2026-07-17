"""Loader for connector guides — the SKILL.md-style docs the agent gets when it loads
a connector's tool group.

A guide is the difference between a tool list and knowing how to use it: search
qualifier syntax, which argument combinations are useless, what the provider calls a
thing. It's disclosed progressively (only once the group is loaded) so it costs
nothing on turns that never touch the connector.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_GUIDE_DIR = Path(__file__).parent / "guides"


@lru_cache(maxsize=None)
def load_guide(name: str) -> str | None:
    """The text of `guides/<name>.md`, or None if there isn't one.

    Cached: guides ship with the code and don't change at runtime.
    """
    path = _GUIDE_DIR / f"{name}.md"
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        logger.warning("no connector guide at %s", path)
        return None


def guide_loader(name: str):
    """A callable for `Connector.guide`, so the file is read on first use rather than
    at import time."""
    return lambda: load_guide(name)

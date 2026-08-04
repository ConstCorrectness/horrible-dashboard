"""Loader for **module** tool-group guides — the SKILL.md-style docs a built-in group
hands the model when it is loaded.

The second tier of progressive disclosure already existed, but only connectors and MCP
servers could fill it: `_group_guide` consulted `Connector.resolve_guide()` and the MCP
bridge and nothing else, so `layout`, `files`, `editor`, `database`, `games` — every
built-in group — had a one-line blurb and no guide at all. That is the gap this closes.

A guide is the difference between a tool list and knowing how to use it: which
argument is an instanceId rather than a view id, which combinations are useless, what
the module calls a thing. It costs nothing on turns that never load the group, which
is exactly why detail that would be too expensive in the system prompt can live here.

Guides are plain markdown in `guides/<group>.md`, so they review and diff like code.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_GUIDE_DIR = Path(__file__).parent / "guides"


@lru_cache(maxsize=None)
def module_guide(group: str) -> str | None:
    """The text of `guides/<group>.md`, or None when the group ships no guide.

    Cached: guides ship with the code and don't change at runtime. Unlike the
    connector loader this does **not** warn on a miss — most groups legitimately have
    no guide, and this is consulted for every group that gets loaded.
    """
    if not group or "/" in group or "\\" in group or group.startswith("."):
        return None
    path = _GUIDE_DIR / f"{group}.md"
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None

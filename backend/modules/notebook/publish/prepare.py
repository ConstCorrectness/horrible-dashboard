"""The copy that gets published. The user's notebook is never touched."""

from __future__ import annotations

import copy
import re
from typing import Any

#: Where ipywidgets stores its saved state. Meaningless off-app (nothing renders
#: it) and it can be large, so a published copy never carries it.
WIDGET_STATE_KEY = "widgets"


def prepare(nb: Any, *, strip_outputs: bool) -> Any:
    """A deep copy ready to leave this machine."""
    out = copy.deepcopy(nb)
    out.metadata.pop(WIDGET_STATE_KEY, None)
    if strip_outputs:
        for cell in out.cells:
            if cell.get("cell_type") == "code":
                cell["outputs"] = []
                cell["execution_count"] = None
    return out


_HEADING = re.compile(r"^\s*#\s+(.+?)\s*$", re.MULTILINE)


def title_of(nb: Any, fallback: str) -> str:
    """The first `# Heading` in a markdown cell, else the file name."""
    for cell in nb.cells:
        if cell.get("cell_type") != "markdown":
            continue
        source = cell.get("source") or ""
        if isinstance(source, list):
            source = "".join(source)
        match = _HEADING.search(source)
        if match:
            return match.group(1)[:120]
    return fallback

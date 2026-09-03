"""The mode registry.

One place that knows every mode's id, so the REST enum, the console, the agent
tool and the server browser all describe the same set and cannot drift.

`catalog()` is served at `GET /api/hassault/modes` for the reason `/weapons` and
`/items` are served: a client with its own copy of the list is a client that
offers a mode this server does not have, or hides one it does.
"""

from __future__ import annotations

from typing import Any

from . import objectives
from .base import GameMode, Goal
from .deathmatch import Deathmatch

__all__ = [
    "DEFAULT_MODE",
    "Deathmatch",
    "GameMode",
    "Goal",
    "build",
    "catalog",
    "is_mode",
    "objectives",
]

#: What a room is when nobody said. Free-for-all deathmatch — the behaviour every
#: caller had before modes existed, so an un-migrated caller changes nothing.
DEFAULT_MODE = "dm"


def _builders() -> dict[str, Any]:
    return {
        "dm": lambda: Deathmatch(teams=False),
        "tdm": lambda: Deathmatch(teams=True),
    }


def is_mode(mode_id: str) -> bool:
    return mode_id in _builders()


def build(mode_id: str | None = None) -> GameMode:
    """A mode by id.

    Raises on an unknown id rather than falling back to deathmatch. A silent
    fallback would mean a request for a mode this build does not have opens a
    room that looks fine and plays as something else — and the client would
    render whatever the welcome said, so nothing would ever report the
    substitution.
    """
    mode_id = mode_id or DEFAULT_MODE
    builders = _builders()
    if mode_id not in builders:
        raise ValueError(f"unknown game mode {mode_id!r}")
    return builders[mode_id]()


def catalog() -> list[dict[str, Any]]:
    """Every mode, for the REST surface and the menus."""
    out = []
    for mode_id in _builders():
        mode = build(mode_id)
        out.append(
            {
                "id": mode.id,
                "name": mode.name,
                "scoreLabel": mode.score_label,
                "teams": getattr(mode, "teams", False),
            }
        )
    return out

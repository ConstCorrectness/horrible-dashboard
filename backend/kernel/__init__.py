"""`backend.kernel` — the host surface a module's Python half may import.

A module package (`modules/<id>/py`) imports from exactly two places in this repo:
`backend.sdk` (the contract) and this package (the host's services). Never
`backend.modules.<other>`: anything another module owns is reached through a typed
service (`host.require("<contract>@<major>")`). That rule is what lets a module move to
its own repository and come back as a git submodule with no code change; it is
checked by scripts/module-boundaries.mjs.

Keep this small. Everything exported here is a promise to every module.
"""

from __future__ import annotations

from typing import Any

from backend.kernel.module_host import HorribleModule, ModuleError, ModuleHost
from backend.modules import ytdl  # yt-dlp, loaded lazily on first use
from backend.modules.ranged_file import ranged_file_response
from backend.modules.ws import broadcast_event
from backend.sdk.types import AgentTool


def get_setting(key: str, default: Any = None) -> Any:
    """A node setting (the same store the settings page edits)."""
    from backend.modules.settings.routes import get_value

    return get_value(key, default)


def set_setting(key: str, value: Any) -> None:
    from backend.modules.settings.routes import set_value

    set_value(key, value)


def app_db_path():  # noqa: ANN201 — a Path
    """The node's shared SQLite app database, its directory created. Modules that keep
    a table or two there (rather than a database of their own) open this."""
    from backend.modules.database.app_db import ensure_app_db_dir

    return ensure_app_db_dir()


def data_dir():  # noqa: ANN201 — re-export of backend.paths.data_dir
    """The node's data directory. Prefer `host.data_dir` (a directory of your own)."""
    from backend import paths

    return paths.data_dir()


__all__ = [
    "AgentTool",
    "HorribleModule",
    "app_db_path",
    "ModuleError",
    "ModuleHost",
    "broadcast_event",
    "data_dir",
    "get_setting",
    "ranged_file_response",
    "set_setting",
    "ytdl",
]

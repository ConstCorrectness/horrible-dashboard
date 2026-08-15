"""Where this node keeps its files — one answer, resolved the same way everywhere.

Every module used to spell this out inline, as ``Path(os.environ.get(
"HORRIBLE_DATA_DIR", ".data"))``. That default is **relative to the cwd**, which is
set by whichever launcher started the backend: ``pnpm dev`` runs it from the repo,
the Tauri shell runs it from ``find_repo_root()``, a packaged app would run it from
wherever the shortcut points. So "the data dir" silently meant a different directory
per launcher, and a user who installed a 300 MB llama.cpp build could open the app
the next day to find nothing installed — the same class of bug as ``.env`` only
being loaded by one launcher.

The rule here is deliberately not "always use the OS directory":

1. ``HORRIBLE_DATA_DIR`` (and the sibling ``HORRIBLE_*_DIR`` overrides) always wins.
   Docker and Fly set it to ``/data``; tests point it at a tmpdir.
2. **A git checkout keeps its data in the tree** — ``<repo>/.data``, ``<repo>/logs``.
   Not because a checkout is special, but because a developer's node identity,
   settings, workspaces and traces already live there; silently relocating them
   would look exactly like data loss. The repo is found from *this file's* location,
   never from the cwd, which is the whole point.
3. Otherwise the per-OS convention below.

Nothing here creates directories. Resolution is pure and re-read on every call, so
a test that monkeypatches ``HORRIBLE_DATA_DIR`` between calls is answered honestly.
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

#: The Windows/macOS spelling. Those platforms put a display name in the path;
#: Linux uses the lowercase form under XDG.
APP_NAME = "HorribleDashboard"
APP_NAME_UNIX = "horrible-dashboard"


@lru_cache(maxsize=1)
def repo_root() -> Path | None:
    """The checkout this backend was loaded from, or `None` if it isn't one.

    Anchored on ``backend/paths.py``'s own location — the same anchor
    ``backend/__init__.py`` uses for ``.env``, and for the same reason: the cwd is
    a property of the launcher, not of the install.

    ``.git`` is tested with ``exists()`` rather than ``is_dir()`` because in a git
    worktree it is a *file* pointing at the real git dir.
    """
    root = Path(__file__).resolve().parent.parent
    if (root / "pyproject.toml").is_file() and (root / ".git").exists():
        return root
    return None


def _home() -> Path:
    return Path.home()


def _xdg(var: str, fallback: str) -> Path:
    # An XDG variable is only honoured when absolute, per the spec — a relative
    # value is defined to be ignored rather than resolved against the cwd.
    raw = os.environ.get(var, "")
    base = Path(raw) if raw and Path(raw).is_absolute() else _home() / fallback
    return base / APP_NAME_UNIX


def _windows_local() -> Path:
    # LOCALAPPDATA, never APPDATA: roaming profiles are copied to the domain
    # server at logon/logoff, and this directory holds multi-gigabyte GGUFs and
    # llama.cpp builds. Tauri's `app_data_dir` picks roaming; we deliberately
    # differ, because the size of what we store makes it the wrong shelf.
    raw = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    base = Path(raw) if raw else _home() / "AppData" / "Local"
    return base / APP_NAME


def _override(var: str) -> Path | None:
    raw = os.environ.get(var, "").strip()
    # Relative values are resolved against the cwd, which is what an operator
    # writing `HORRIBLE_DATA_DIR=.data` in a shell means. Absolute is the norm.
    return Path(raw) if raw else None


def data_dir() -> Path:
    """Everything the node owns and cannot regenerate: identity keys, settings,
    workspaces, `app.db`, `secrets.db`, LanceDB, libraries, llama.cpp builds and
    GGUFs, traces, karaoke media, plugins."""
    if override := _override("HORRIBLE_DATA_DIR"):
        return override
    if root := repo_root():
        return root / ".data"
    if sys.platform == "win32":
        return _windows_local()
    if sys.platform == "darwin":
        return _home() / "Library" / "Application Support" / APP_NAME
    return _xdg("XDG_DATA_HOME", ".local/share")


def config_dir() -> Path:
    """User configuration that is *not* the app's own state.

    Only the secrets master key lives here today, and it lives here precisely
    because it must not be inside `data_dir()`.

    This is the one root that is **the same on every platform**, and the deviation
    is deliberate. Its requirement is stronger than the convention: the key must
    never sit inside the data dir *on any platform or in a checkout*, and the
    per-OS config locations do not satisfy that — `%LOCALAPPDATA%\\<app>\\config`
    and `Application Support/<app>/config` are both **children** of this app's data
    dir, one directory from the database they decrypt and inside the same tree that
    gets zipped, backed up or screen-shared. `~/.horrible` is a sibling of nothing
    we store, is the path every existing install already holds a key at (moving it
    would strand their encrypted credentials), and is a form Windows users already
    have a dozen of — `~/.ssh`, `~/.aws`, `~/.kaggle`, `~/.ollama`.
    """
    if override := _override("HORRIBLE_CONFIG_DIR"):
        return override
    return _home() / ".horrible"


def cache_dir() -> Path:
    """Regenerable bytes: anything that can be re-downloaded or recomputed.

    Deleting this must never lose user data. Nothing is routed here yet — the
    existing caches (geoip, the browser profile) sit under `data_dir()` and moving
    them would relocate live files for no gain — but a *new* cache belongs here and
    not in a `cache/` folder inside the data dir.
    """
    if override := _override("HORRIBLE_CACHE_DIR"):
        return override
    if root := repo_root():
        return root / ".cache"
    if sys.platform == "win32":
        return _windows_local() / "cache"
    if sys.platform == "darwin":
        return _home() / "Library" / "Caches" / APP_NAME
    return _xdg("XDG_CACHE_HOME", ".cache")


def log_dir() -> Path:
    """Where `backend.log` is written.

    In a checkout this stays `<repo>/logs`, which the dev backend's
    ``--reload-exclude "logs/*"`` depends on and which every debugging note in this
    project points at.
    """
    if override := _override("HORRIBLE_LOG_DIR"):
        return override
    if root := repo_root():
        return root / "logs"
    if sys.platform == "win32":
        return _windows_local() / "logs"
    if sys.platform == "darwin":
        return _home() / "Library" / "Logs" / APP_NAME
    # XDG has no log directory; the spec's own guidance is that logs are state.
    return _xdg("XDG_STATE_HOME", ".local/state") / "logs"


def describe() -> dict[str, str]:
    """Every resolved root as `id -> path`. The short form, for `dash` and tests."""
    return {
        "data": str(data_dir()),
        "config": str(config_dir()),
        "cache": str(cache_dir()),
        "logs": str(log_dir()),
        "repo": str(repo_root() or ""),
    }


#: Which roots have a checkout branch. `config` is the exception — see `config_dir`.
_REPO_RELATIVE = {"data", "cache", "logs"}

_ENV_VARS = {
    "data": "HORRIBLE_DATA_DIR",
    "config": "HORRIBLE_CONFIG_DIR",
    "cache": "HORRIBLE_CACHE_DIR",
    "logs": "HORRIBLE_LOG_DIR",
}

_TITLES = {
    "data": "Data",
    "config": "Config",
    "cache": "Cache",
    "logs": "Logs",
}

_NOTES = {
    "data": (
        "Everything this node owns and cannot regenerate: identity keys, settings, "
        "workspaces, databases, libraries, llama.cpp builds and GGUFs, traces, "
        "karaoke media, plugins. An app update never touches it."
    ),
    "config": (
        "The secrets master key, and nothing else. Kept outside the data dir on "
        "purpose — beside the database it decrypts, it would be copied along with "
        "it. That is why this one root is the same on every platform."
    ),
    "cache": (
        "Regenerable bytes. Deleting this must never lose anything; nothing is "
        "routed here yet."
    ),
    "logs": "Where backend.log is written.",
}


def _source(root_id: str) -> str:
    """Which of the three rules produced this root — the answer to "why here?"."""
    if _override(_ENV_VARS[root_id]):
        return "environment"
    if root_id in _REPO_RELATIVE and repo_root():
        return "checkout"
    return "platform"


def describe_roots() -> dict[str, object]:
    """The long form the settings page renders.

    Each root carries **why** it resolved where it did, not just where. "Why is my
    data here" is the actual question — a bare path leaves a user unable to tell an
    environment override from a per-OS default, which is the difference between a
    setting they can change and a property of their install.
    """
    roots = []
    for root_id, path in (
        ("data", data_dir()),
        ("config", config_dir()),
        ("cache", cache_dir()),
        ("logs", log_dir()),
    ):
        resolved = path.expanduser()
        roots.append(
            {
                "id": root_id,
                "title": _TITLES[root_id],
                "path": str(resolved),
                # A root is created on first write, so "not yet" is normal and is
                # not an error — but the pane must not offer to open a directory
                # that isn't there.
                "exists": resolved.is_dir(),
                "source": _source(root_id),
                "envVar": _ENV_VARS[root_id],
                "note": _NOTES[root_id],
            }
        )
    return {"roots": roots, "repo": str(repo_root() or "")}

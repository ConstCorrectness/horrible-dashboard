"""The environment a terminal's shell is spawned with.

The backend runs inside its own venv (`uv run` puts `<repo>/.venv` first on PATH and
sets `VIRTUAL_ENV`), and a shell inherits that. So `python script.py` in a terminal —
which is how the agent runs code, through `terminal.exec` — ran on the **backend's**
interpreter: no torch, no transformers, and a `pip install` there would modify the app
itself. The interpreter meant for user code is the notebook's (the managed venv that
carries the AI stack, or the `notebook.python` override), so a terminal gets that
instead: the backend venv is removed from PATH and the user one put first.

With no notebook interpreter yet (no notebook ever opened), the backend venv is still
removed, and `python` resolves to whatever the machine has.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

from backend.modules.notebook import env as notebook_env

_VENV_ONLY_VARS = ("VIRTUAL_ENV", "VIRTUAL_ENV_PROMPT")

#: "Use the live value" — distinct from `None`, which means "there isn't one".
_LIVE: object = object()


def _scripts_dir(venv: Path) -> Path:
    return venv / ("Scripts" if sys.platform == "win32" else "bin")


def _norm(p: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(p)))


def _backend_venv(environ: Mapping[str, str]) -> Path | None:
    if sys.prefix != sys.base_prefix:
        return Path(sys.prefix)
    venv = environ.get("VIRTUAL_ENV")
    return Path(venv) if venv else None


def shell_env(
    environ: Mapping[str, str] | None = None,
    user_python: Path | None | object = _LIVE,
    backend_venv: Path | None | object = _LIVE,
) -> dict[str, str]:
    """`environ` with the backend venv swapped for the user-code interpreter.

    `user_python`/`backend_venv` default to the live values; tests pass them (or
    `None` for "there isn't one")."""
    env = dict(os.environ if environ is None else environ)
    if user_python is _LIVE:
        user_python = notebook_env.existing_python()
    if backend_venv is _LIVE:
        backend_venv = _backend_venv(env)
    assert user_python is None or isinstance(user_python, Path)
    assert backend_venv is None or isinstance(backend_venv, Path)

    path_key = next((k for k in env if k.upper() == "PATH"), "PATH")
    entries = [e for e in env.get(path_key, "").split(os.pathsep) if e]
    if backend_venv is not None:
        drop = _norm(_scripts_dir(backend_venv))
        entries = [e for e in entries if _norm(e) != drop]
    for var in _VENV_ONLY_VARS:
        env.pop(var, None)

    if user_python is not None:
        bin_dir = user_python.parent
        entries = [e for e in entries if _norm(e) != _norm(bin_dir)]
        entries.insert(0, str(bin_dir))
        # A venv announces itself so prompts and tools (pip, uv) target it; a bare
        # interpreter from the override is only put on PATH.
        if (bin_dir.parent / "pyvenv.cfg").is_file():
            env["VIRTUAL_ENV"] = str(bin_dir.parent)

    env[path_key] = os.pathsep.join(entries)
    return env

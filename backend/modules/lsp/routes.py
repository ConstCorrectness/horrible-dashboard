"""HTTP surface for the editor's Python environment resolution.

The `lsp` WS channel stays a dumb JSON-RPC pipe; this router exposes the *environment*
the editor needs to decide things the browser can't (filesystem + subprocess): which
interpreter basedpyright should analyze against, the project root that anchors one
shared server per project, and the installed versions of the framework packages the
completion layer tracks. Results are cached in `pyenv` (per interpreter / directory),
so opening many files in a project resolves once.
"""

from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter
from pydantic import BaseModel

from backend.modules.lsp import pyenv

router = APIRouter(prefix="/editor", tags=["editor"])


class PythonEnv(BaseModel):
    """The resolved Python environment for a directory: the interpreter to analyze
    with (or None → basedpyright's default), the project root that anchors the shared
    server pool, and the installed framework-package versions (`{dist: version}`)."""

    interpreter: str | None
    root: str
    packages: dict[str, str]


@router.get("/python-env")
async def python_env(path: str = "") -> PythonEnv:
    """Resolve the interpreter, project root, and installed framework versions for a
    file/directory. Offloaded (the interpreter probe shells out) and cached in `pyenv`."""
    start = path or None
    interpreter = await asyncio.to_thread(pyenv.resolve_python_interpreter, start)
    root = await asyncio.to_thread(pyenv.resolve_project_root, start)
    packages = await asyncio.to_thread(pyenv.installed_versions, interpreter)
    return PythonEnv(
        interpreter=interpreter,
        root=root or (start if start and os.path.isdir(start) else ""),
        packages=dict(packages),
    )

"""Notebook kernel manager: opens a generic `.ipynb` under the notebook root on a
kernel spawned from the managed venv. A thin subclass of the shared
`notebook_core.KernelSessionManager` — it only fills the open seams (key + config).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.modules.notebook import env
from backend.modules.settings.routes import get_value
from backend.notebook_core import KernelSession, KernelSessionManager, SessionConfig
from backend.notebook_core import notebooks as _core


def notebook_root() -> Path:
    raw = str(get_value("notebook.root", "~/horrible/notebooks"))
    return Path(raw).expanduser()


def resolve(rel_path: str) -> Path:
    """Escape-guarded absolute path of a notebook under the root."""
    return _core.resolve_path(notebook_root(), rel_path)


class NotebookManager(KernelSessionManager):
    channel = "notebook"
    SessionCls = KernelSession  # plain sessions (no training sentinel)

    def _session_key(self, data: dict[str, Any]) -> str:
        rel = str(data.get("path") or "").strip()
        if not rel:
            raise ValueError("notebook open requires a path")
        # Normalize to forward slashes so the key matches the frontend store id.
        return f"nb:{rel.replace(chr(92), '/')}"

    def _build_config(self, data: dict[str, Any], key: str) -> SessionConfig:
        rel = str(data.get("path") or "").replace("\\", "/")
        abs_path = resolve(rel)
        if not abs_path.is_file():
            raise ValueError(f"notebook not found: {rel}")
        python_executable = env.ensure_python()
        return SessionConfig(
            key=key,
            python_executable=python_executable,
            cwd=str(abs_path.parent),
            notebook_abs_path=abs_path,
            rel_path=rel,
            channel="notebook",
            display_name="notebook",
            default_mode="reactive",
        )

    def _opened_extra(
        self, session: KernelSession, data: dict[str, Any]
    ) -> dict[str, Any]:
        return {"path": session.rel_path}


notebook_manager = NotebookManager()


async def handle_notebook_message(conn: Any, msg: dict[str, Any]) -> None:
    """`notebook` channel entry point."""
    await notebook_manager.handle(conn, msg)

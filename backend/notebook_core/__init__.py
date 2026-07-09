"""Domain-neutral notebook engine shared by the `notebook` and `training` modules.

This package owns everything that is *not* domain-specific about running a Jupyter
notebook: nbformat `.ipynb` IO, the kernel session (spawn/exec/iopub/save on daemon
threads — Windows/uvicorn-`--reload` safe), and the session manager's shared `/ws`
event handling. It is parametrized by a `SessionConfig` (python executable, cwd,
notebook path, ws channel) rather than any module's domain model, so `training`
(project venvs, metrics sentinel) and `notebook` (a generic managed venv) both
consume it without importing each other — the module-isolation rule holds because
`notebook_core` is shared infrastructure, not a feature module.
"""

from backend.notebook_core.config import SessionConfig
from backend.notebook_core.manager import KernelSessionManager
from backend.notebook_core.models import CellModel, NotebookModel
from backend.notebook_core.session import KernelSession

__all__ = [
    "CellModel",
    "KernelSession",
    "KernelSessionManager",
    "NotebookModel",
    "SessionConfig",
]

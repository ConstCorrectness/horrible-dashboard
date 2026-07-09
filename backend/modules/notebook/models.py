"""Pydantic models for the notebook module's REST boundary.

The notebook *document* itself is the shared `NotebookModel` (nbformat-shaped);
these models cover the file catalog and create/mode requests.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

# Re-export the document model so callers can import it from one place.
from backend.notebook_core.models import NotebookModel as NotebookModel

ExecutionMode = Literal["reactive", "classic"]


class NotebookFile(BaseModel):
    """One `.ipynb` under the notebook root."""

    path: str  # relative to the notebook root (forward slashes)
    name: str
    modified: float = 0.0  # mtime epoch seconds


class NotebookListResponse(BaseModel):
    root: str  # absolute notebook root (display)
    files: list[NotebookFile]


class CreateNotebookRequest(BaseModel):
    path: str  # relative path, e.g. "explore.ipynb" or "sub/dir/nb.ipynb"
    mode: ExecutionMode = "reactive"


class SetModeRequest(BaseModel):
    path: str
    mode: ExecutionMode

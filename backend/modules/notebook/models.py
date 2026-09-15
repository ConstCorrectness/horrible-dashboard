"""Pydantic models for the notebook module's REST boundary.

The notebook *document* itself is the shared `NotebookModel` (nbformat-shaped);
these models cover the file catalog and create/mode requests.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

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


# --- publishing ----------------------------------------------------------------

Visibility = Literal["secret", "public"]


class PublishFinding(BaseModel):
    """Something in the prepared copy worth seeing before it leaves this machine."""

    cell: int  # index into the notebook's cells
    cell_id: str
    where: Literal["source", "output"]
    kind: Literal["secret", "path", "traceback", "widget"]
    label: str
    #: Enough to locate it. For a secret, only its first characters — this response
    #: goes to the browser, and a finding list is the kind of thing that gets pasted.
    excerpt: str
    #: True for secrets: publishing waits for an explicit acknowledgement.
    blocking: bool


class PublicationModel(BaseModel):
    """Where one notebook was published, by one target."""

    path: str
    target: str
    remote_id: str = ""
    url: str = ""
    extra_url: str = ""
    visibility: str = "secret"
    published_at: float = 0.0
    detail: dict[str, Any] = Field(default_factory=dict)


class PublishTargetOut(BaseModel):
    id: str
    label: str
    visibilities: list[str]
    available: bool
    #: False where the destination cannot delete (Kaggle): the panel offers Forget.
    can_unpublish: bool = True
    #: What to do when it is not available. Visible text in the panel, not a tooltip.
    reason: str = ""


class PublicationsOut(BaseModel):
    publications: list[PublicationModel] = Field(default_factory=list)
    targets: list[PublishTargetOut] = Field(default_factory=list)


class PreflightIn(BaseModel):
    path: str
    strip_outputs: bool = False


class PreflightOut(BaseModel):
    findings: list[PublishFinding] = Field(default_factory=list)
    blocking: int = 0


class PublishIn(BaseModel):
    path: str
    target: str
    visibility: Visibility = "secret"
    strip_outputs: bool = False
    #: The person saw the blocking findings and chose to publish anyway.
    acknowledged: bool = False


class PublishOut(BaseModel):
    ok: bool
    publication: PublicationModel | None = None
    note: str = ""
    error: str = ""
    findings: list[PublishFinding] = Field(default_factory=list)


class UnpublishOut(BaseModel):
    ok: bool
    error: str = ""


class EnvLibrariesOut(BaseModel):
    """The notebook venv's libraries (`notebook.python.packages`).

    `ready` — everything configured is installed. `idle` — some are missing and no
    install is running (one starts on the next kernel open, or via
    `POST /notebook/env/install`). `installing`. `failed` — see `error`, retryable.
    `unmanaged` — the kernel runs on a `notebook.python` override, which this app
    never installs into.
    """

    state: Literal["ready", "idle", "installing", "failed", "unmanaged"]
    missing: list[str] = Field(default_factory=list)
    installing: list[str] = Field(default_factory=list)
    #: The latest line of installer output, for a live progress readout.
    line: str = ""
    error: str = ""
    #: The index PyTorch comes from, when torch is missing. Empty means PyPI.
    torch_index: str = ""


class EnvStatusOut(BaseModel):
    ready: bool
    libraries: EnvLibrariesOut


class SavedHtmlOut(BaseModel):
    #: Relative to the notebook root.
    path: str

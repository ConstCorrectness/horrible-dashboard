"""The contract every publish target implements."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from backend.modules.notebook.models import PublicationModel


class PublishError(RuntimeError):
    """A failure worth showing the person verbatim — with what to do about it."""


@dataclass
class PublishContext:
    #: Relative to the notebook root, forward slashes. The record's key.
    rel_path: str
    #: The file name without `.ipynb`.
    stem: str
    title: str
    #: The **prepared copy** (`prepare.py`), never the user's own document.
    notebook: Any
    visibility: str
    #: What this target last published for this notebook, so it can update in place.
    previous: PublicationModel | None
    #: Renders `notebook` to standalone HTML. Blocking; only targets that need HTML
    #: call it, so the others never pay for nbconvert.
    render_html: Callable[[], str]


@dataclass
class PublishResult:
    remote_id: str
    #: The link to hand out.
    url: str
    #: A second useful link (the gist page behind an nbviewer render, say).
    extra_url: str = ""
    #: Something the person should know right now ("Pages takes a minute").
    note: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


class PublishTarget(Protocol):
    id: str
    label: str
    #: The visibilities this target can honour. Pages has only `public`: offering
    #: "secret" there would be a promise GitHub does not keep.
    visibilities: tuple[str, ...]
    #: False where the destination has no delete API (Kaggle): the panel then offers
    #: *Forget*, which drops the record and leaves the remote copy alone.
    can_unpublish: bool

    def availability(self) -> tuple[bool, str]:
        """Whether publishing can work right now, and if not, what to do. Cheap: no
        network, because the panel asks on every open."""
        ...

    async def publish(self, ctx: PublishContext) -> PublishResult: ...

    async def unpublish(self, record: PublicationModel) -> None: ...

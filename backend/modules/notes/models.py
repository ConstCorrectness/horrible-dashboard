from __future__ import annotations

from pydantic import BaseModel

# Note ids are generated hex; the `note:` buffer URI is `note:<id>`.
NOTE_ID_PATTERN = r"^[A-Za-z0-9]+$"


class Note(BaseModel):
    """A backend-owned note. `revision` bumps on every content/title change and is
    the basis for optimistic-concurrency conflict detection on save."""

    id: str
    title: str
    content: str
    revision: int
    updated_at: float


class NoteMeta(BaseModel):
    """Lightweight note summary for lists and search (no full content)."""

    id: str
    title: str
    revision: int
    updated_at: float
    snippet: str | None = None


class NotesState(BaseModel):
    notes: list[Note] = []


class CreateNote(BaseModel):
    title: str = "Untitled"
    content: str = ""


class UpdateNote(BaseModel):
    """Partial update. `base_revision` is the revision the editor loaded; if it no
    longer matches the stored note the save is a conflict (`409`)."""

    title: str | None = None
    content: str | None = None
    base_revision: int

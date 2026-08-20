"""Note storage: CRUD + search over backend-owned notes.

Notes are backend data, identical in both layouts (the editor opens them as
`note:<id>` buffers). Saves use optimistic concurrency: the editor sends the
`base_revision` it loaded, and a save against a stale revision is a `409` carrying
the current note so the client can reconcile (autosave and manual save share this
path). See docs/modules/editor.md.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException
from fastapi import Path as PathParam

from backend import jsonstore, paths
from backend.modules.notes.models import (
    NOTE_ID_PATTERN,
    CreateNote,
    Note,
    NoteMeta,
    NotesState,
    UpdateNote,
)

router = APIRouter(prefix="/notes", tags=["notes"])

NoteId = Annotated[str, PathParam(pattern=NOTE_ID_PATTERN)]

SNIPPET_LEN = 120


def _state_path() -> Path:
    return paths.data_dir() / "notes.json"


def _read() -> NotesState:
    text = jsonstore.read_text(_state_path())
    if text is None:
        return NotesState()
    try:
        return NotesState.model_validate_json(text)
    except ValueError:
        return NotesState()


def _write(state: NotesState) -> None:
    jsonstore.write_text(_state_path(), state.model_dump_json())


def _find(state: NotesState, note_id: str) -> Note | None:
    return next((n for n in state.notes if n.id == note_id), None)


def _meta(note: Note, snippet: str | None = None) -> NoteMeta:
    return NoteMeta(
        id=note.id,
        title=note.title,
        revision=note.revision,
        updated_at=note.updated_at,
        snippet=snippet,
    )


@router.get("", response_model=list[NoteMeta])
def list_notes() -> list[NoteMeta]:
    state = _read()
    ordered = sorted(state.notes, key=lambda n: n.updated_at, reverse=True)
    return [_meta(n) for n in ordered]


@router.get("/search", response_model=list[NoteMeta])
def search_notes(q: str) -> list[NoteMeta]:
    needle = q.strip().lower()
    if not needle:
        return []
    state = _read()
    results: list[NoteMeta] = []
    for note in sorted(state.notes, key=lambda n: n.updated_at, reverse=True):
        haystack = note.content.lower()
        if needle in note.title.lower() or needle in haystack:
            idx = haystack.find(needle)
            snippet = note.content[max(0, idx) : max(0, idx) + SNIPPET_LEN] or None
            results.append(_meta(note, snippet))
    return results


@router.post("", response_model=Note)
@jsonstore.serialized(_state_path)
def create_note(body: CreateNote) -> Note:
    note = Note(
        id=uuid.uuid4().hex[:12],
        title=body.title,
        content=body.content,
        revision=1,
        updated_at=time.time(),
    )
    state = _read()
    state.notes.append(note)
    _write(state)
    return note


@router.get("/{note_id}", response_model=Note)
def get_note(note_id: NoteId) -> Note:
    note = _find(_read(), note_id)
    if note is None:
        raise HTTPException(status_code=404, detail="note not found")
    return note


@router.put("/{note_id}", response_model=Note)
@jsonstore.serialized(_state_path)
def update_note(note_id: NoteId, body: UpdateNote) -> Note:
    state = _read()
    note = _find(state, note_id)
    if note is None:
        raise HTTPException(status_code=404, detail="note not found")
    if body.base_revision != note.revision:
        # Stale write — return the current note so the client can reconcile.
        raise HTTPException(
            status_code=409,
            detail={"message": "revision conflict", "current": note.model_dump()},
        )
    if body.title is not None:
        note.title = body.title
    if body.content is not None:
        note.content = body.content
    note.revision += 1
    note.updated_at = time.time()
    _write(state)
    return note


@router.delete("/{note_id}", response_model=NotesState)
@jsonstore.serialized(_state_path)
def delete_note(note_id: NoteId) -> NotesState:
    state = _read()
    state.notes = [n for n in state.notes if n.id != note_id]
    _write(state)
    return state

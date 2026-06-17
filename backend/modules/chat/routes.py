"""Chat sessions: persisted agent-chat transcripts.

Mirrors the workspace module's file-backed JSON store — the list endpoint returns
metadata only (no messages) to stay light, and a per-id GET returns the full
transcript. The chat widget auto-saves each completed turn here so conversations
survive reloads and pane unmounts. See docs/modules/agent-chat.md.
"""

import os
import time
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException
from fastapi import Path as PathParam

from backend.modules.chat.models import (
    CHAT_ID_PATTERN,
    ActiveRequest,
    ChatSession,
    ChatSessionMeta,
    ChatSessionsList,
    ChatSessionsState,
    CreateSession,
    UpsertSession,
)

router = APIRouter(prefix="/chat", tags=["chat"])

SessionId = Annotated[str, PathParam(pattern=CHAT_ID_PATTERN)]


def _state_path() -> Path:
    return Path(os.environ.get("HORRIBLE_DATA_DIR", ".data")) / "chat-sessions.json"


def _read() -> ChatSessionsState:
    path = _state_path()
    if not path.is_file():
        return ChatSessionsState()
    try:
        return ChatSessionsState.model_validate_json(path.read_text())
    except ValueError:
        return ChatSessionsState()


def _write(state: ChatSessionsState) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(state.model_dump_json())


def _find(state: ChatSessionsState, sid: str) -> ChatSession | None:
    return next((s for s in state.sessions if s.id == sid), None)


def _list(state: ChatSessionsState) -> ChatSessionsList:
    return ChatSessionsList(
        active=state.active,
        sessions=[
            ChatSessionMeta(id=s.id, title=s.title, updated=s.updated)
            for s in state.sessions
        ],
    )


@router.get("/sessions", response_model=ChatSessionsList)
def list_sessions() -> ChatSessionsList:
    return _list(_read())


@router.post("/sessions", response_model=ChatSession)
def create_session(body: CreateSession) -> ChatSession:
    state = _read()
    now = time.time()
    session = ChatSession(
        id=uuid.uuid4().hex[:8],
        title=body.title or "New chat",
        messages=[],
        created=now,
        updated=now,
    )
    state.sessions.append(session)
    state.active = session.id
    _write(state)
    return session


@router.put("/sessions/active", response_model=ChatSessionsList)
def set_active(body: ActiveRequest) -> ChatSessionsList:
    state = _read()
    if _find(state, body.id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown session '{body.id}'")
    state.active = body.id
    _write(state)
    return _list(state)


@router.get("/sessions/{sid}", response_model=ChatSession)
def get_session(sid: SessionId) -> ChatSession:
    session = _find(_read(), sid)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Unknown session '{sid}'")
    return session


@router.put("/sessions/{sid}", response_model=ChatSession)
def upsert_session(sid: SessionId, body: UpsertSession) -> ChatSession:
    state = _read()
    existing = _find(state, sid)
    now = time.time()
    if existing is not None:
        # Apply only provided fields so saving messages doesn't wipe the title.
        if "title" in body.model_fields_set and body.title is not None:
            existing.title = body.title
        if "messages" in body.model_fields_set and body.messages is not None:
            existing.messages = body.messages
        existing.updated = now
        session = existing
    else:
        session = ChatSession(
            id=sid,
            title=body.title or "New chat",
            messages=body.messages or [],
            created=now,
            updated=now,
        )
        state.sessions.append(session)
    if state.active is None:
        state.active = session.id
    _write(state)
    return session


@router.delete("/sessions/{sid}", response_model=ChatSessionsList)
def delete_session(sid: SessionId) -> ChatSessionsList:
    state = _read()
    state.sessions = [s for s in state.sessions if s.id != sid]
    if state.active == sid:
        state.active = state.sessions[0].id if state.sessions else None
    _write(state)
    return _list(state)

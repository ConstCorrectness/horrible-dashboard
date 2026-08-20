"""Chat sessions: persisted agent-chat transcripts.

Mirrors the workspace module's file-backed JSON store — the list endpoint returns
metadata only (no messages) to stay light, and a per-id GET returns the full
transcript. The chat widget auto-saves each completed turn here so conversations
survive reloads and pane unmounts. See docs/modules/agent-chat.md.
"""

import time
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException
from fastapi import Path as PathParam

from backend import jsonstore, paths
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
    return paths.data_dir() / "chat-sessions.json"


def _read() -> ChatSessionsState:
    text = jsonstore.read_text(_state_path())
    if text is None:
        return ChatSessionsState()
    try:
        return ChatSessionsState.model_validate_json(text)
    except ValueError:
        return ChatSessionsState()


def _write(state: ChatSessionsState) -> None:
    jsonstore.write_text(_state_path(), state.model_dump_json())


def _find(state: ChatSessionsState, sid: str) -> ChatSession | None:
    return next((s for s in state.sessions if s.id == sid), None)


def _active_for(state: ChatSessionsState, agent: str) -> str | None:
    """The active session id for one agent. `main` keeps the legacy `active`
    pointer (pre-roster files have only that); other agents use the per-agent map."""
    if agent == "main":
        return state.active_by_agent.get("main") or state.active
    return state.active_by_agent.get(agent)


def _set_active(state: ChatSessionsState, session: ChatSession) -> None:
    state.active_by_agent[session.agent_id] = session.id
    if session.agent_id == "main":
        state.active = session.id


def _list(state: ChatSessionsState, agent: str | None = None) -> ChatSessionsList:
    sessions = [s for s in state.sessions if agent is None or s.agent_id == agent]
    return ChatSessionsList(
        active=_active_for(state, agent) if agent is not None else state.active,
        sessions=[
            ChatSessionMeta(
                id=s.id, title=s.title, agent_id=s.agent_id, updated=s.updated
            )
            for s in sessions
        ],
    )


@router.get("/sessions", response_model=ChatSessionsList)
def list_sessions(agent: str | None = None) -> ChatSessionsList:
    """All sessions, or one agent's with `?agent=<id>` (then `active` is that
    agent's active session)."""
    return _list(_read(), agent)


@router.post("/sessions", response_model=ChatSession)
@jsonstore.serialized(_state_path)
def create_session(body: CreateSession) -> ChatSession:
    state = _read()
    now = time.time()
    session = ChatSession(
        id=uuid.uuid4().hex[:8],
        title=body.title or "New chat",
        agent_id=body.agent_id,
        messages=[],
        created=now,
        updated=now,
    )
    state.sessions.append(session)
    _set_active(state, session)
    _write(state)
    return session


@router.put("/sessions/active", response_model=ChatSessionsList)
@jsonstore.serialized(_state_path)
def set_active(body: ActiveRequest) -> ChatSessionsList:
    state = _read()
    session = _find(state, body.id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Unknown session '{body.id}'")
    _set_active(state, session)
    _write(state)
    return _list(state, session.agent_id)


@router.get("/sessions/{sid}", response_model=ChatSession)
def get_session(sid: SessionId) -> ChatSession:
    session = _find(_read(), sid)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Unknown session '{sid}'")
    return session


@router.put("/sessions/{sid}", response_model=ChatSession)
@jsonstore.serialized(_state_path)
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
            agent_id=body.agent_id or "main",
            messages=body.messages or [],
            created=now,
            updated=now,
        )
        state.sessions.append(session)
    if state.active is None and session.agent_id == "main":
        state.active = session.id
    state.active_by_agent.setdefault(session.agent_id, session.id)
    _write(state)
    return session


@router.delete("/sessions/{sid}", response_model=ChatSessionsList)
@jsonstore.serialized(_state_path)
def delete_session(sid: SessionId) -> ChatSessionsList:
    state = _read()
    deleted = _find(state, sid)
    state.sessions = [s for s in state.sessions if s.id != sid]
    if state.active == sid:
        state.active = next(
            (s.id for s in state.sessions if s.agent_id == "main"), None
        )
    for agent, active_id in list(state.active_by_agent.items()):
        if active_id == sid:
            replacement = next(
                (s.id for s in state.sessions if s.agent_id == agent), None
            )
            if replacement is None:
                del state.active_by_agent[agent]
            else:
                state.active_by_agent[agent] = replacement
    _write(state)
    return _list(state, deleted.agent_id if deleted else None)

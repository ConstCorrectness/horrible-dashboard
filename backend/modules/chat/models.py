from pydantic import BaseModel

# Session ids are generated hex; constrain the path param like workspaces do.
CHAT_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"


class ChatMessage(BaseModel):
    """One turn in a chat session. `reasoning` holds the model's streamed thinking
    (when any); `actions` the human-readable notes for tools the agent ran."""

    role: str  # 'user' | 'assistant'
    content: str = ""
    reasoning: str = ""
    actions: list[str] = []


class ChatSession(BaseModel):
    """A named, persisted conversation transcript. `agent_id` names the roster
    agent the conversation belongs to ("main" = the orchestrator) — additive with
    a default so pre-roster session files still validate."""

    id: str
    title: str
    agent_id: str = "main"
    messages: list[ChatMessage] = []
    created: float
    updated: float


class ChatSessionMeta(BaseModel):
    """Lightweight session entry for the list endpoint (no messages)."""

    id: str
    title: str
    agent_id: str = "main"
    updated: float


class ChatSessionsState(BaseModel):
    """The whole stored collection (with messages) plus the active selection.
    `active` stays the legacy pointer (and remains authoritative for "main");
    `active_by_agent` tracks each specialized agent's active session."""

    active: str | None = None
    active_by_agent: dict[str, str] = {}
    sessions: list[ChatSession] = []


class ChatSessionsList(BaseModel):
    """The list view returned to the client: metadata only, keeps it light. When
    the list is filtered to one agent, `active` is that agent's active session."""

    active: str | None = None
    sessions: list[ChatSessionMeta] = []


class CreateSession(BaseModel):
    title: str | None = None
    agent_id: str = "main"


class UpsertSession(BaseModel):
    """Partial update: only fields present are applied (`model_fields_set`), so
    saving messages never clobbers the title and vice-versa. `agent_id` only
    matters when the PUT creates the session (it is never re-assigned)."""

    title: str | None = None
    agent_id: str | None = None
    messages: list[ChatMessage] | None = None


class ActiveRequest(BaseModel):
    id: str

from pydantic import BaseModel

DEFAULT_OLLAMA_ENDPOINT = "http://localhost:11434"


class AgentConfig(BaseModel):
    """Persisted local-model configuration for the backend agent."""

    model: str
    endpoint: str = DEFAULT_OLLAMA_ENDPOINT


class AgentStatus(BaseModel):
    ollama_reachable: bool
    configured: bool
    model: str | None = None
    endpoint: str
    available_models: list[str] = []


class ChatRequest(BaseModel):
    prompt: str


class PullRequest(BaseModel):
    model: str

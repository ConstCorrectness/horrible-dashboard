from typing import Any

from pydantic import BaseModel, model_validator

from backend.modules.agent.providers import DEFAULT_PROVIDER, provider_for

# Re-exported for back-compat with callers importing the constant from here.
DEFAULT_OLLAMA_ENDPOINT = provider_for("ollama").default_endpoint


class AgentConfig(BaseModel):
    """Persisted local-model configuration for the backend agent."""

    model: str
    provider: str = DEFAULT_PROVIDER
    endpoint: str | None = None

    @model_validator(mode="after")
    def _default_endpoint(self) -> "AgentConfig":
        # Fill the endpoint from the provider default when the caller omits it, so
        # picking a provider is enough to get a working endpoint.
        if not self.endpoint:
            self.endpoint = provider_for(self.provider).default_endpoint
        return self


class DetectedProvider(BaseModel):
    """One auto-detected provider, surfaced to onboarding."""

    kind: str
    label: str
    endpoint: str
    reachable: bool
    models: list[str] = []
    can_pull: bool
    can_spawn: bool
    install_url: str


class AgentStatus(BaseModel):
    configured: bool
    provider: str | None = None  # configured provider kind
    model: str | None = None
    endpoint: str  # active (configured) provider endpoint
    reachable: bool = False  # is the active provider reachable
    available_models: list[str] = []  # models on the active provider
    providers: list[DetectedProvider] = []  # everything we probed, for onboarding
    vllm: dict[str, Any] = {}  # vLLM spawn lifecycle status


class ChatRequest(BaseModel):
    prompt: str
    temperature: float = 0.2
    max_tokens: int = 64
    system: str | None = None


class CompleteRequest(BaseModel):
    """A fill-in completion request for the editor's inline autosuggest: the text
    before the cursor (`prefix`) and after it (`suffix`). Optionally grounded with
    LSP context — the completion candidates in scope (`completions`) and the
    type/signature at the cursor (`hover`) — so the model suggests code that
    resolves instead of hallucinating symbols."""

    prefix: str
    suffix: str = ""
    language: str | None = None
    completions: list[str] = []
    hover: str | None = None


class RosterAgent(BaseModel):
    """One roster entry for the UI: identity + scope, never the system prompt."""

    id: str
    name: str
    description: str
    # None = unrestricted (the main orchestrator).
    tool_groups: list[str] | None = None
    default_mode: str = ""


class RosterResponse(BaseModel):
    agents: list[RosterAgent] = []


class PullRequest(BaseModel):
    model: str


class VllmSpawnRequest(BaseModel):
    model: str
    port: int | None = None

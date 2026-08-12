"""Pydantic models for the MCP module's API boundary.

Note what is absent: no response model ever carries a server's bearer token. A token is
write-only from the browser's perspective — submitted through `ServerInput.token`,
stored encrypted, and reported thereafter only as the boolean `hasToken`.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Transport = Literal["stdio", "http", "sse"]


class ServerInput(BaseModel):
    """A server definition submitted from the UI."""

    id: str = Field(description="Unique id; becomes the `mcp-<id>` tool group.")
    name: str = ""
    transport: Transport = "stdio"
    # stdio
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None
    # http / sse
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    # Write-only. Stored in the encrypted secrets store, never echoed back.
    token: str | None = None
    # Write-only too, and for the same reason: a discovered server routinely wants an
    # API key in its environment, and `env` is persisted in plaintext. Values submitted
    # here go to the encrypted store; only their names are kept in the config.
    secretEnvValues: dict[str, str] = Field(default_factory=dict)
    secretEnv: list[str] = Field(default_factory=list)
    enabled: bool = True
    # Provenance, supplied by whichever surface added it: Discover sends `registry`
    # because that server is a third party's code. Unrecognized values are coerced to
    # `manual` on save rather than trusted — see `config.ORIGINS`.
    origin: Literal["manual", "registry", "authored"] = "manual"


class ToolSummary(BaseModel):
    name: str
    description: str = ""
    readOnly: bool = False
    destructive: bool = False
    # The server's own JSON Schema for this tool's arguments. Present so the pane can
    # generate an invoke form from it; a response model that omitted it would leave
    # the browser with `undefined` and no error anywhere.
    inputSchema: dict[str, Any] = Field(default_factory=dict)


class PromptSummary(BaseModel):
    name: str
    description: str = ""


class ResourceSummary(BaseModel):
    uri: str
    name: str = ""
    description: str = ""


class ServerStatus(BaseModel):
    """A server's config (minus secrets) plus its live connection state."""

    id: str
    name: str = ""
    transport: Transport = "stdio"
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None
    url: str = ""
    enabled: bool = True

    group: str
    state: Literal["stopped", "starting", "ready", "error"]
    error: str | None = None
    serverName: str = ""
    serverVersion: str = ""
    protocolVersion: str = ""
    # Whose code this is. The pane says so before running anything: `registry` is a
    # third party's, `authored` is the user's own project, `manual` is a command they
    # typed. See `author.py` on why this is a label and not a gate.
    origin: Literal["manual", "registry", "authored"] = "manual"
    # The authoring project that owns this server's files, if any.
    project: str = ""
    hasToken: bool = False
    target: dict[str, Any] = Field(default_factory=dict)
    tools: list[ToolSummary] = Field(default_factory=list)
    prompts: list[PromptSummary] = Field(default_factory=list)
    resources: list[ResourceSummary] = Field(default_factory=list)
    # Names only — the values live encrypted. `missingSecretEnv` is what makes "this
    # server is configured but has no key yet" a visible state instead of a start
    # failure the user has to decode from the server's own error text.
    secretEnv: list[str] = Field(default_factory=list)
    missingSecretEnv: list[str] = Field(default_factory=list)


class ServerListResponse(BaseModel):
    servers: list[ServerStatus]


class WireMessageModel(BaseModel):
    at: float
    direction: Literal["in", "out"]
    method: str = ""
    id: str = ""
    payload: str = ""
    truncated: bool = False


class TranscriptResponse(BaseModel):
    messages: list[WireMessageModel] = Field(default_factory=list)


class ToolCostModel(BaseModel):
    name: str
    tokens: int


class CostResponse(BaseModel):
    """What a server costs the model once its group is loaded."""

    tools: list[ToolCostModel] = Field(default_factory=list)
    toolTokens: int = 0
    guideTokens: int = 0
    totalTokens: int = 0
    # False means these are chars/4 estimates. The pane must say so.
    exact: bool = False
    tokenizer: str = ""
    agents: list[dict[str, Any]] = Field(default_factory=list)


class EnvVarModel(BaseModel):
    name: str
    description: str = ""
    required: bool = False
    secret: bool = False
    default: str = ""


class InstallOptionModel(BaseModel):
    kind: Literal["package", "remote"]
    label: str
    transport: Transport = "stdio"
    command: str = ""
    args: list[str] = Field(default_factory=list)
    url: str = ""
    env: list[EnvVarModel] = Field(default_factory=list)
    unsupported: str = ""


class CatalogEntryModel(BaseModel):
    name: str
    title: str = ""
    description: str = ""
    version: str = ""
    repository: str = ""
    source: Literal["registry", "curated"] = "registry"
    note: str = ""
    suggestedId: str = ""
    installs: list[InstallOptionModel] = Field(default_factory=list)


class DiscoverResponse(BaseModel):
    entries: list[CatalogEntryModel] = Field(default_factory=list)
    # True when the live registry answered. False means the list is the shipped
    # overlay alone — a different thing from "nothing matched".
    registryOnline: bool = True


class ProbeResponse(BaseModel):
    """What a candidate server actually is, connected once and thrown away."""

    ok: bool
    error: str | None = None
    serverName: str = ""
    serverVersion: str = ""
    instructions: str = ""
    tools: list[ToolSummary] = Field(default_factory=list)
    prompts: list[PromptSummary] = Field(default_factory=list)
    resources: list[ResourceSummary] = Field(default_factory=list)
    messages: list[WireMessageModel] = Field(default_factory=list)


class ExportStatus(BaseModel):
    """State of the MCP server this node exports. Carries `hasToken`, never the token."""

    enabled: bool
    mountPath: str
    enableEnv: str
    hasToken: bool
    exposeContent: bool


class ExportTokenResponse(BaseModel):
    """Returned only from the explicit reveal/rotate endpoint."""

    token: str
    mountPath: str


class ReadResourceRequest(BaseModel):
    uri: str


class CallToolRequest(BaseModel):
    """A hand-invocation from the pane — the same call the agent would make."""

    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class CallToolResponse(BaseModel):
    """One tool result, plus how long it took.

    `elapsedMs` is here because latency is a property of the tool the pane can measure
    and the agent cannot report: a tool that takes nine seconds is a tool that will
    make every turn using it feel broken, and that is invisible in a result body.
    """

    content: str = ""
    structured: Any = None
    attachments: list[str] = Field(default_factory=list)
    error: str | None = None
    elapsedMs: int = 0


class ConformanceCheckModel(BaseModel):
    id: str
    title: str
    status: Literal["pass", "warn", "fail", "skip"]
    detail: str = ""


class ConformanceResponse(BaseModel):
    status: Literal["pass", "warn", "fail", "skip"]
    serverName: str = ""
    serverVersion: str = ""
    protocolVersion: str = ""
    checks: list[ConformanceCheckModel] = Field(default_factory=list)


class ProjectInput(BaseModel):
    """A request to scaffold a new server project."""

    id: str
    title: str = ""
    template: Literal["python", "node"] = "python"


class ProjectModel(BaseModel):
    id: str
    title: str = ""
    template: Literal["python", "node"] = "python"
    state: Literal["new", "provisioning", "ready", "error"] = "new"
    error: str = ""
    root: str = ""
    entry: str = ""
    # False when the source is still on disk but no server points at it — the state a
    # Remove-that-kept-the-files leaves behind. See `author.Project.registered`.
    registered: bool = True
    files: list[str] = Field(default_factory=list)
    log: list[str] = Field(default_factory=list)


class ProjectListResponse(BaseModel):
    projects: list[ProjectModel] = Field(default_factory=list)
    # False means the pane must say what to install rather than showing a create
    # button that scaffolds a project nothing can provision.
    hasUv: bool = True
    hasNpm: bool = True


class FileWriteRequest(BaseModel):
    path: str
    text: str
    # Whether saving should restart the running server. Defaulted on: an edited
    # `server.py` that doesn't restart leaves the pane listing tools that no longer
    # exist, which is a worse lie than a slow save.
    restart: bool = True


class FileResponse(BaseModel):
    path: str
    text: str = ""
    # Present on a write: whether the server was restarted, and what happened if it
    # failed to come back. A syntax error you just saved shows up here.
    restarted: bool = False
    restartError: str | None = None


class ResourceContentResponse(BaseModel):
    contents: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None

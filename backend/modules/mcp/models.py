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
    enabled: bool = True


class ToolSummary(BaseModel):
    name: str
    description: str = ""
    readOnly: bool = False
    destructive: bool = False


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
    hasToken: bool = False
    target: dict[str, Any] = Field(default_factory=dict)
    tools: list[ToolSummary] = Field(default_factory=list)
    prompts: list[PromptSummary] = Field(default_factory=list)
    resources: list[ResourceSummary] = Field(default_factory=list)


class ServerListResponse(BaseModel):
    servers: list[ServerStatus]


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


class ResourceContentResponse(BaseModel):
    contents: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None

"""Pydantic wire models for the Agent Client Protocol (ACP).

Spec reference: https://agentclientprotocol.com/protocol/overview
JSON-RPC 2.0 messages exchanged between the dashboard (client) and an external
agent subprocess (server).
"""

from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field


class AcpClientInfo(BaseModel):
    name: str = "horrible-dashboard"
    version: str = "0.3.1"


class FsCapabilities(BaseModel):
    readTextFile: bool = True
    writeTextFile: bool = True


class ClientCapabilities(BaseModel):
    fs: FsCapabilities = Field(default_factory=FsCapabilities)
    terminal: bool = True


class InitializeParams(BaseModel):
    protocolVersion: int = 1
    clientInfo: AcpClientInfo = Field(default_factory=AcpClientInfo)
    capabilities: ClientCapabilities = Field(default_factory=ClientCapabilities)


class InitializeResult(BaseModel):
    protocolVersion: int = 1
    agentInfo: dict[str, Any] = Field(default_factory=dict)
    capabilities: dict[str, Any] = Field(default_factory=dict)


# Aliases
AcpInitializeParams = InitializeParams
AcpInitializeResult = InitializeResult


class AcpRequest(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: int | str
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


class AcpResponse(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: int | str
    result: Any = None
    error: dict[str, Any] | None = None


class AcpNotification(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


AcpClientRequest = AcpRequest
AcpClientResponse = AcpResponse


class SessionNewParams(BaseModel):
    cwd: str
    mcpServers: list[dict[str, Any]] = Field(default_factory=list)


class SessionNewResult(BaseModel):
    sessionId: str


class PromptContentPart(BaseModel):
    type: Literal["text", "image", "resource"] = "text"
    text: str = ""


class SessionPromptParams(BaseModel):
    sessionId: str
    prompt: list[PromptContentPart] = Field(default_factory=list)


AcpSessionPromptParams = SessionPromptParams


class SessionPromptResult(BaseModel):
    stopReason: str = "end_turn"


class SessionCancelParams(BaseModel):
    sessionId: str


class FsReadParams(BaseModel):
    path: str


class FsWriteParams(BaseModel):
    path: str
    content: str


class TerminalCreateParams(BaseModel):
    command: str
    args: list[str] = Field(default_factory=list)
    cwd: str | None = None
    env: dict[str, str] = Field(default_factory=dict)


class PermissionRequestParams(BaseModel):
    sessionId: str
    requestId: str
    toolCall: dict[str, Any] = Field(default_factory=dict)


class PermissionRequestResult(BaseModel):
    outcome: Literal["accepted", "rejected"] = "accepted"


class SessionUpdateAgentMessageChunk(BaseModel):
    sessionUpdate: Literal["agent_message_chunk"] = "agent_message_chunk"
    delta: str


class AcpSessionUpdateNotification(AcpNotification):
    method: Literal["session/update"] = "session/update"

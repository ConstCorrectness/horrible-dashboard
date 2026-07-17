"""API models for the connectors surface.

Nothing here ever carries a token: the browser learns *that* an account is connected
and which scopes were granted, never the credential itself.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ConnectorKind = Literal["oauth", "api-key", "custom"]


class ScopeModel(BaseModel):
    """One granted permission, in the user's words."""

    id: str
    label: str
    description: str = ""


class AccountModel(BaseModel):
    """The connected identity, for display only."""

    id: str
    label: str
    avatar_url: str | None = None


class ConnectorModel(BaseModel):
    """A connector plus its current state — one home-page tile."""

    id: str
    label: str
    kind: ConnectorKind
    icon: str
    blurb: str
    connected: bool
    account: AccountModel | None = None
    scopes: list[ScopeModel] = Field(default_factory=list)
    granted_scopes: list[str] = Field(default_factory=list)
    # Set when a connection exists but is unusable (revoked token, unreadable
    # credential). Distinct from `connected=False`, which means "never connected".
    error: str | None = None


class ConnectorListModel(BaseModel):
    connectors: list[ConnectorModel] = Field(default_factory=list)


class FieldModel(BaseModel):
    """One input a `form` step asks for."""

    name: str
    label: str = ""
    # Renders masked and is never echoed back.
    secret: bool = False
    placeholder: str = ""


class StepModel(BaseModel):
    """One step of a connect flow.

    A single shape covers all three connector kinds — which is the point: `custom` is
    just a `form` step that may return another `form` step.
    """

    # None once the flow reaches a terminal state (connected / pending / error).
    step: Literal["device", "redirect", "form"] | None = None
    # step="device"
    user_code: str | None = None
    verification_uri: str | None = None
    interval: float | None = None
    expires_in: float | None = None
    # step="redirect"
    authorize_url: str | None = None
    # step="form"
    fields: list[FieldModel] = Field(default_factory=list)
    # terminal
    connected: bool = False
    account: AccountModel | None = None
    pending: bool = False
    error: str | None = None

    @classmethod
    def from_result(cls, data: dict[str, Any]) -> StepModel:
        """Build from the loose dict a `Connector` callback returns."""
        account = data.get("account")
        return cls(
            step=data.get("step"),
            user_code=data.get("user_code"),
            verification_uri=data.get("verification_uri"),
            interval=data.get("interval"),
            expires_in=data.get("expires_in"),
            authorize_url=data.get("authorize_url"),
            fields=[FieldModel(**f) for f in (data.get("fields") or [])],
            connected=bool(data.get("connected")),
            account=AccountModel(**account) if account else None,
            pending=bool(data.get("pending")),
            error=data.get("error"),
        )


class SubmitRequest(BaseModel):
    """Values for a `form` step. Free-form because each connector names its own
    fields (an api-key connector wants `api_key`; Clubhouse wants `phone`/`code`)."""

    values: dict[str, str] = Field(default_factory=dict)


class ConnectRequest(BaseModel):
    """Options for `begin`. Empty for most connectors; present so a caller can pass
    connector-specific hints without a new route."""

    options: dict[str, Any] = Field(default_factory=dict)

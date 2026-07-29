"""Pydantic models for the node games module's HTTP surface."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class GameInfo(BaseModel):
    id: str
    name: str
    min_players: int
    max_players: int
    # How a seat decides + how to present it (see GameSpec in games_engine/base.py).
    decision_class: str = "policy"
    default_policy: str = "random"
    allowed_policies: list[str] = ["random", "agent", "manual", "bot"]
    obs_kind: str = "json"
    pacing: str = "turn"


class ToolDefModel(BaseModel):
    """One custom tool in a player's harness: a real function they author."""

    name: str
    description: str = ""
    code: str = ""  # must define `run(args, obs)`
    parameters: dict[str, Any] = {}
    required: list[str] = []


class LoadoutModel(BaseModel):
    """A player's agent for a game: an optional `my_agent(obs, config)` entrypoint over
    the harness (strategy context + custom tools + the model that drives it; None model =
    borrow the agent module's configured model). Empty `agent_code` = the default agent
    (context + tools drive the model)."""

    game_id: str
    context: str = ""
    tools: list[ToolDefModel] = []
    model: dict[str, Any] | None = None
    agent_code: str = ""


class LoadoutVersionInfo(BaseModel):
    """One saved harness version (the progression loop's unit of iteration)."""

    id: str
    label: str
    created_at: float
    active: bool
    model: dict[str, Any] | None = None


class SaveVersionRequest(BaseModel):
    label: str = ""
    loadout: LoadoutModel


class ActivateVersionRequest(BaseModel):
    version_id: str


class SetKeyRequest(BaseModel):
    """Write-only: the value goes into the node's key store and never comes back."""

    value: str


class TestToolRequest(BaseModel):
    """Dry-run a single tool body against a sample observation, for the editor."""

    code: str
    args: dict[str, Any] = {}
    obs: dict[str, Any] = {}


class TestToolResponse(BaseModel):
    ok: bool
    result: Any = None
    error: str | None = None


class ToolDiagnostic(BaseModel):
    """Whether one tool of a loadout is usable: name rule + compilation."""

    name: str
    ok: bool
    error: str | None = None


class ValidateLoadoutResponse(BaseModel):
    ok: bool  # every tool ok AND the agent entrypoint compiles
    tools: list[ToolDiagnostic] = []
    # None when the loadout uses the default agent (empty agent_code); otherwise the
    # compile error for the `my_agent(obs, config)` entrypoint, or None if it's fine.
    agent_error: str | None = None


class DryRunRequest(BaseModel):
    """Run the FULL agent loop (context + all tools + real model) against a
    sample observation for a game — no match, no random fallback."""

    game_id: str  # must be an engine game (the sample-observation source)
    loadout: LoadoutModel  # the panel's current draft (unsaved edits included)
    seed: int = 0


class DryRunStep(BaseModel):
    """One traced reasoning step (same kinds AgentPolicy emits live)."""

    kind: str  # assistant | tool_result | chose
    t_ms: float
    content: str | None = None
    tool_calls: list[dict[str, Any]] = []
    name: str | None = None
    result: str | None = None
    action_id: str | None = None


class DryRunResponse(BaseModel):
    ok: bool
    error: str | None = None
    observation: dict[str, Any] = {}
    legal_actions: list[dict[str, Any]] = []
    compile_errors: dict[str, str] = {}
    steps: list[DryRunStep] = []
    chosen: str | None = None
    rounds_used: int = 0
    total_ms: float = 0.0


class SampleObservationResponse(BaseModel):
    """A realistic opening position for a game — the Build panel's observation
    inspector. Just the engine's per-seat observation + legal actions; no loadout,
    no model, no agent run (unlike DryRunResponse), so it's cheap to resample."""

    ok: bool
    error: str | None = None
    game_id: str
    observation: dict[str, Any] = {}
    legal_actions: list[dict[str, Any]] = []


class GamesStatus(BaseModel):
    """Whether this node is connected to a game server, and as whom."""

    connected: bool
    account_id: str | None = None
    signed_in: bool = False  # holds a server-issued JWT (vs the dev token)
    display_name: str | None = None
    # The globally unique callsign (the game server's `handle`). None means signed
    # out *or* signed in without one yet — HorribleAssault treats the latter as
    # "not enlisted" and asks for one before letting you play.
    callsign: str | None = None
    server_url: str
    policy: str
    games: list[GameInfo] = []


class DevicePollRequest(BaseModel):
    device_code: str


class LocalSignupRequest(BaseModel):
    """Email+password signup. Never logged — see `_REDACT_BODY_PREFIXES`."""

    email: str
    password: str
    callsign: str = ""


class LocalLoginRequest(BaseModel):
    email: str
    password: str


class SetCallsignRequest(BaseModel):
    callsign: str

"""Pydantic models for the node games module's HTTP surface."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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


class LlmHarnessModel(BaseModel):
    """A player's **LLM agent** for a game: an optional `my_agent(obs, config)`
    entrypoint over the harness (strategy context + custom tools + the model that
    drives it; None model = borrow the agent module's configured model). Empty
    `agent_code` = the default agent (context + tools drive the model)."""

    # `extra="forbid"` is what makes the split real on the wire: without it Pydantic
    # silently drops unknown fields, so a body sending `bot_code` to the LLM arm
    # would be accepted and the policy quietly lost. A cross-kind field is a
    # confused client, and it should hear about it.
    model_config = ConfigDict(extra="forbid")

    kind: Literal["llm"] = "llm"
    game_id: str
    context: str = ""
    tools: list[ToolDefModel] = []
    model: dict[str, Any] | None = None
    agent_code: str = ""


class CodedHarnessModel(BaseModel):
    """A player's **coded agent** for a game: one Python policy, no model. It carries
    no context/tools/model fields at all — a request that sends them is rejected by
    the discriminator rather than quietly having them dropped."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["coded"] = "coded"
    game_id: str
    bot_code: str = ""


# The wire type for "a player's harness". It is a **discriminated union**, so the
# body a client sends must declare which harness it is and may only carry that
# harness's fields. Note this must be the declared `response_model` on every route
# that returns a harness: a `response_model` filters unknown fields silently, so
# declaring one arm alone would make the other arm's fields vanish on the way out
# with no error anywhere.
HarnessModel = Annotated[
    LlmHarnessModel | CodedHarnessModel, Field(discriminator="kind")
]

# The old name, kept because the LLM harness is what every pre-split caller meant.
LoadoutModel = LlmHarnessModel


class LoadoutVersionInfo(BaseModel):
    """One saved harness version (the progression loop's unit of iteration)."""

    id: str
    label: str
    created_at: float
    active: bool
    model: dict[str, Any] | None = None


class SaveVersionRequest(BaseModel):
    label: str = ""
    loadout: HarnessModel


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


# ---- the RL environment (Train section) --------------------------------------


class TrainingCapability(BaseModel):
    """What kind of training a game supports, so the Train UI can shape itself per
    game instead of offering one loop for everything. Mirrors `TrainingSpec` in
    backend/games_engine/env_adapter.py, which is the source of truth."""

    self_play: bool = True
    default_episodes: int = 200
    max_episodes: int = 5_000
    in_app_optimizer: bool = False
    hint: str = ""


class EnvInfoResponse(BaseModel):
    """A game's RL environment, or the honest absence of one.

    `has_env` is False for every `reasoner` game — their action is a payload (a
    patch, an answer), not a point in a space — and the Train section branches on
    it rather than showing a broken runner."""

    game_id: str
    has_env: bool
    reason: str | None = None
    observation_space: str | None = None
    n_actions: int | None = None
    training: TrainingCapability | None = None


class TrainRunRequest(BaseModel):
    game_id: str
    # The bot code to run. Sent explicitly (rather than read from the saved
    # loadout) so the Train section can exercise unsaved edits.
    code: str
    # "random" or "bot:<tier>".
    opponent: str = "bot:bronze"
    episodes: int = 100
    seed: int = 0


class TrainRunResponse(BaseModel):
    ok: bool
    error: str | None = None
    shape: str = ""
    episodes: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    illegal: int = 0
    truncated: int = 0
    mean_reward: float = 0.0
    curve: list[float] = Field(default_factory=list)
    elapsed_ms: int = 0
    stopped_early: bool = False
    sample: dict[str, Any] | None = None

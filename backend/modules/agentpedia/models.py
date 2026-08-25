"""Shapes for the agentpedia stepper.

Agentpedia **owns no store**. Every field here is joined from somewhere that
already records it:

* `agent_turns` (interpretability) — what the model was **shown**
* the `IoEvent` ring (telemetry) — what actually went over the **wire**
* `traj_runs`/`traj_steps` (trajectories) — what the agent **did**

So the models are views, and they reuse the source modules' own models wherever a
shape already exists (`RoundSnapshot` here is the identical object the
interpretability route serves). Redefining it would guarantee the two drift, and a
stepper showing a stale idea of a round is worse than one showing none.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.modules.interpretability.models import RoundSnapshot

#: Whether the wire half of a round can be shown, and if not, why not. Three states
#: rather than a bool, for the reason the hardware probe reports three: "the ring
#: has aged this turn out" and "this turn ran before anything recorded the wire" are
#: different facts, and rendering both as an empty list invites reading either as
#: "the agent made no requests".
WireStatus = Literal["live", "aged_out", "unrecorded"]


class WireEvent(BaseModel):
    """One request the loop made, as the telemetry ring recorded it.

    Bodies are the ring's own captures — already size-capped, and already redacted
    for the paths `instrument.py` refuses to read. Nothing is re-truncated here: a
    clipped provider request is exactly the thing a reader came looking for.
    """

    id: int
    ts: float
    source: str
    method: str
    target: str
    status: int | None = None
    duration_ms: float | None = None
    request_bytes: int | None = None
    response_bytes: int | None = None
    request_body: str | None = None
    response_body: str | None = None
    error: str | None = None


class DidStep(BaseModel):
    """One thing the agent did in this round.

    `gated` is kept separate from `ok` because "the harness blocked it" and "the
    tool failed" look identical in a success flag and are completely different
    findings — the trajectories store makes that distinction and it would be a
    shame to flatten it on the way here.
    """

    seq: int
    kind: str
    name: str | None = None
    ok: bool | None = None
    gated: bool = False
    duration_ms: int | None = None
    tokens: int | None = None
    error: str | None = None
    content: str | None = None
    args: Any = None
    result: Any = None


class FlattenReport(BaseModel):
    """What `providers.normalize_system_messages` does to this round on the way out.

    The context pane shows the system tier **pre-flatten** — several separate
    messages, which is how the orchestrator assembles it and how the recorder tells
    the parts apart. It is not what the provider received. Every strict Jinja
    template rejects a second system message, so the real request carries one
    leading system message and no others, and a reader comparing the pane against a
    captured request body would otherwise conclude one of them was lying.
    """

    messages_in: int = 0
    messages_out: int = 0
    #: Labels of the blocks folded into the single leading system message, in order.
    merged: list[str] = Field(default_factory=list)


class RoundCost(BaseModel):
    message_tokens: int = 0
    tool_tokens: int = 0
    total_tokens: int = 0
    #: Share of the model's real context window, when the window is known. None
    #: rather than a guess — see `window.context_length`.
    window: int | None = None
    window_pct: float | None = None
    wall_ms: float | None = None


class RoundView(BaseModel):
    """One round, in the four columns the stepper scrubs through."""

    round: int
    shown: RoundSnapshot
    wire: list[WireEvent] = Field(default_factory=list)
    did: list[DidStep] = Field(default_factory=list)
    cost: RoundCost = RoundCost()
    flatten: FlattenReport = FlattenReport()


class RunLink(BaseModel):
    """The trajectory run for this turn, when capture was on. Absent is the normal
    case — trajectory capture is off by default and dataset-scoped."""

    id: str
    dataset_id: str = ""
    status: str = ""
    outcome: str | None = None
    goal: str = ""
    steps: int = 0
    harness: str | None = None
    duration_ms: int | None = None


class TurnView(BaseModel):
    """One turn, everything joined."""

    turn_id: str
    parent_turn_id: str | None = None
    agent_id: str = "main"
    agent_name: str = ""
    kind: str = "local"
    peer_id: str | None = None
    model: str = ""
    provider: str = ""
    started_at: float = 0.0
    exact: bool = True
    tokenizer_repo: str | None = None
    tokenizer_source: str = "none"
    requested_num_ctx: int | None = None
    model_context_length: int | None = None
    temperature: float | None = None
    rounds: list[RoundView] = Field(default_factory=list)
    run: RunLink | None = None
    wire_status: WireStatus = "unrecorded"


class TurnIndexEntry(BaseModel):
    """A row of the Runs timeline: the shown half and the did half of one turn,
    summarized. Never carries context blocks — content is fetched one turn at a
    time, the same boundary the history route draws."""

    turn_id: str
    parent_turn_id: str | None = None
    agent_id: str = "main"
    agent_name: str = ""
    kind: str = "local"
    model: str = ""
    provider: str = ""
    started_at: float = 0.0
    rounds: int = 0
    total_tokens: int = 0
    run: RunLink | None = None


class TurnIndexResponse(BaseModel):
    turns: list[TurnIndexEntry] = Field(default_factory=list)
    #: True when trajectory capture is on for some dataset — the difference between
    #: "this turn did nothing" and "nothing was recording".
    capture_on: bool = False

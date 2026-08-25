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


# ── Forks ────────────────────────────────────────────────────────────────────
#
# The second half of the one idea this project is built on: a trace and an agent
# run are both a *recorded computation you can fork with an edit and diff*. The
# lens forks a trace by swapping a token; this forks a turn by changing what the
# model was given. Same vocabulary — `derived_from`, `edits`, `diff` — one
# altitude up.

#: What a fork changes. Eight ops in three shapes: message edits (`set_system`,
#: `edit_message`, `truncate_history`), catalog edits (`drop_tool`, `drop_group`)
#: and provider edits (`set_model`, `set_provider`, `set_temperature`).
ForkOp = Literal[
    "drop_tool",
    "drop_group",
    "set_system",
    "edit_message",
    "set_model",
    "set_provider",
    "set_temperature",
    "truncate_history",
]


class ForkEdit(BaseModel):
    """One change, as an op plus whichever field it uses.

    Deliberately one flat model rather than a tagged union: the ops share almost
    every field, the browser builds them from one form, and a union would turn a
    mistyped op into a schema failure instead of the plain message the pane can
    show beside the edit.
    """

    op: ForkOp
    #: `drop_tool` (a tool name), `drop_group` (a group), `set_model`,
    #: `set_provider`.
    name: str | None = None
    #: `set_system`, `edit_message`.
    content: str | None = None
    #: `edit_message` — an index into the round's message list, which is the same
    #: order the Shown column renders.
    index: int | None = None
    #: `set_temperature`.
    value: float | None = None
    #: `truncate_history` — how many history messages to keep, newest first.
    keep: int | None = None


class RebuildReport(BaseModel):
    """How faithfully the parent turn's context could be reconstructed.

    An honest fork answers this before it answers anything else. The stored
    snapshot clips block previews at 4000 characters, so a long prompt cannot
    always be reproduced byte for byte, and a fork that ran a truncated prompt
    would answer differently *for a reason that is not the edit* — the worst
    failure available here, because it is indistinguishable from a finding.
    """

    messages: int = 0
    #: True when nothing was lost: no clipped block survived into the rebuild and
    #: every tool result found the call it answers.
    exact: bool = True
    #: Labels of blocks that went into the fork truncated.
    clipped: list[str] = Field(default_factory=list)
    #: Labels of blocks restored to their full text from a live source (the agent's
    #: current system prompt, matched against the recorded preview).
    restored: list[str] = Field(default_factory=list)
    #: Assistant tool calls recovered from the block text the recorder folded them
    #: into. See rebuild.py.
    tool_calls_recovered: int = 0
    #: Tool results with no assistant call to pair against — reported rather than
    #: given an invented id.
    unlinked_tool_results: int = 0
    applied: list[str] = Field(default_factory=list)
    #: Edits that matched nothing. Loud, because "the tool I dropped changed
    #: nothing" and "the tool name I misspelled changed nothing" are the same
    #: sentence with the meaning removed.
    rejected: list[str] = Field(default_factory=list)


class ToolDrift(BaseModel):
    """The gap between the catalog the parent turn was offered and the one the fork
    can offer.

    The snapshot records tool *names* and token costs, not schemas, so a fork's
    catalog is rebuilt from the live registry for the round's active groups. That
    is the right call — a schema pinned from a snapshot would be a fork of a tool
    that no longer exists — but it means a fork run after a plugin was installed or
    a pane was closed is not a clean comparison, and it says so.
    """

    added: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    #: Names removed by this fork's `drop_tool` / `drop_group` edits.
    denied: list[str] = Field(default_factory=list)


class ForkRequest(BaseModel):
    turn_id: str
    #: Which round to branch at. The fork replays that round's context — so
    #: `from_round: 0` re-runs the whole turn, and a later round re-runs from
    #: partway through, with the earlier rounds' calls and results already in hand.
    from_round: int = 0
    edits: list[ForkEdit] = Field(default_factory=list)
    #: Run the tools for real. Off by default and it must stay that way: a replay
    #: is something you do to a turn that already happened, and the turn that
    #: already happened may have sent an email.
    live: bool = False
    #: What each tool returns while `live` is false. A tool with no fixture returns
    #: a bland success — an error would make the model's next move a reaction to a
    #: broken tool rather than to the task.
    fixtures: dict[str, Any] = Field(default_factory=dict)


class ForkRecord(BaseModel):
    """One counterfactual, as agentpedia stores it.

    Only the *edge* is stored — which turn this came from, what was changed, and
    how it went. The fork's rounds, wire and steps are recorded by the ordinary
    machinery under its own `turn_id`, so it opens in the stepper like any other
    turn and nothing is duplicated.
    """

    fork_turn_id: str
    parent_turn_id: str
    from_round: int = 0
    created_at: float = 0.0
    edits: list[ForkEdit] = Field(default_factory=list)
    live: bool = False
    status: str = "complete"  # complete | failed
    error: str | None = None
    answer: str = ""
    model: str = ""
    provider: str = ""
    rebuild: RebuildReport = RebuildReport()
    drift: ToolDrift = ToolDrift()
    #: The tools the fork called, in order. The full record is on the fork's own
    #: turn; this is what a listing needs.
    calls: list[str] = Field(default_factory=list)


class ForkListResponse(BaseModel):
    forks: list[ForkRecord] = Field(default_factory=list)


class ForkPreview(BaseModel):
    """What a fork *would* run, without running it.

    A fork costs a real model turn, and the interesting question is often answered
    before it starts — does the parent's context rebuild cleanly, and which tools
    would this drop. Cheap enough to render live as the edits are typed.
    """

    turn_id: str
    from_round: int = 0
    messages: list[dict[str, Any]] = Field(default_factory=list)
    rebuild: RebuildReport = RebuildReport()
    drift: ToolDrift = ToolDrift()
    tools: list[str] = Field(default_factory=list)
    model: str = ""
    provider: str = ""
    temperature: float | None = None


class SideDiff(BaseModel):
    """One side of a fork diff."""

    turn_id: str
    model: str = ""
    provider: str = ""
    rounds: int = 0
    total_tokens: int = 0
    tools_offered: int = 0
    calls: list[str] = Field(default_factory=list)
    #: The decision at the branch round: what this side reached for first once it
    #: had its context. The single most useful cell in the whole diff.
    decision: list[str] = Field(default_factory=list)
    answer: str = ""


class ForkDiff(BaseModel):
    """Parent beside fork.

    Reports what the plan asked for and nothing more: the tools offered, the
    decision at the branch round, the final answer, and the token cost. Diffing two
    answers word by word is a job for the reader; the point of this object is that
    the *harness* difference and the *behaviour* difference sit on one screen.
    """

    fork: ForkRecord
    a: SideDiff
    b: SideDiff
    #: Tools offered to the parent but not the fork, and the reverse.
    tools_removed: list[str] = Field(default_factory=list)
    tools_added: list[str] = Field(default_factory=list)
    #: True when the two made the same first move at the branch round. The headline.
    same_decision: bool = False
    token_delta: int = 0

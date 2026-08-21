"""The trajectory contract: what a run is, what a step is, and what a harness is.

These shapes are the whole API of the module, so the decisions behind them are
stated here once rather than rediscovered in five files.

**A step is one decision, and an action carries its own observation.** A tool call
and its result are *one* `action` step (`args` + `result`), never two. Split them
and every downstream query has to re-pair them by name and ordinal — which is
correct right up until a round calls the same tool twice, and then it is silently
wrong in a way no test notices.

**The harness is content-addressed.** A run points at a `fingerprint`, not at a
copy of its system prompt. Without that join key, "did my prompt change help?" is
not a hard question, it is an unanswerable one: there is nothing to group by. It
also means a 4 KB system prompt is stored once per harness rather than once per run.

**Grading is late-bound.** A run finishes without knowing whether it was any good,
so `outcome` and `reward` are nullable and judgments land in `TrajectoryLabel`
rows carrying *who said so*. A step is never mutated to record a verdict — that is
what makes an export reproducible from `(dataset, filter)` months later.

**A run with no actions is still a run.** "The agent answered instead of acting" is
the most valuable failure signal there is; the evals module's `no_call` grade
exists for exactly this reason and it generalizes past tool-calling.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

#: A dataset id is a slug — it appears in URLs and in export filenames.
DATASET_ID_PATTERN = r"^[a-z][a-z0-9_-]{0,63}$"

#: Where a run came from. `local` is this node's own orchestrator loop; `external`
#: is a third-party agent pushing through the Python SDK; `imported` is a file
#: someone else's framework wrote.
TrajectorySource = Literal["local", "evals", "games", "peer", "external", "imported"]

#: A run's lifecycle. Only `running` is mutable — everything else is sealed, and
#: the store refuses to append a step to a sealed run.
RunStatus = Literal["running", "complete", "failed", "abandoned"]

#: Late-bound verdict. `unknown` is deliberately distinct from NULL: NULL means
#: nobody has looked, `unknown` means somebody looked and could not tell.
Outcome = Literal["success", "failure", "partial", "unknown"]

#: What a step is.
#:
#: - `message` — prose from a role (the user's ask, the assistant's answer).
#: - `action`  — a tool call *and* the result it returned. The unit of learning.
#: - `thought` — reasoning tokens, when the provider exposes them separately.
#: - `observation` — state handed to the agent that it did not ask for (a game's
#:   obs, an environment tick). Distinct from an action's `result`, which was
#:   requested.
#: - `reward` — a scalar arriving mid-run (RL-shaped sources; games).
#: - `error` — the run hit something that was not a tool failure.
StepKind = Literal["message", "action", "thought", "observation", "reward", "error"]

#: Who produced a `message` step.
StepRole = Literal["system", "user", "assistant", "tool"]

#: Where a label came from. The point of recording it is that a human thumbs-down
#: and an LLM critic's guess are not the same evidence and must not average
#: together silently.
LabelSource = Literal["human", "grader", "agent-critic", "downstream", "import"]


class Harness(BaseModel):
    """Everything that decides how an agent behaves, minus the task itself.

    Two runs share a fingerprint exactly when they were run by the same agent
    under the same prompt, the same tools and the same sampling settings — which
    is the condition under which comparing their outcomes means anything.
    """

    fingerprint: str
    agent_id: str = ""
    model: str = ""
    provider: str = ""
    system_prompt: str = ""
    tool_names: list[str] = []
    tool_schemas: dict[str, Any] = {}
    #: Sampling and exposure: temperature, top_p, max_tokens, context_size,
    #: expose mode. A dict rather than columns because providers keep adding knobs.
    params: dict[str, Any] = {}
    first_seen: float = 0.0
    last_seen: float = 0.0
    run_count: int = 0
    #: A short human label — `agent_id @ model`, or the caller's own name. Only
    #: for display; the fingerprint is the identity.
    label: str = ""


class TrajectoryStep(BaseModel):
    """One step of a run.

    `args`/`result` are JSON-encoded on the way to SQLite and decoded here. Either
    may instead be a `blob:<relpath>` reference when the payload was too big to
    inline — see `store.STEP_PAYLOAD_MAX`. They are never truncated: a clipped
    tool result is precisely the thing you went looking for in the postmortem.
    """

    seq: int
    kind: StepKind
    round: int = 0
    role: StepRole | None = None
    #: Tool name, for `action`. The column every cross-run aggregate groups by.
    name: str | None = None
    args: Any = None
    result: Any = None
    #: Whether the action succeeded. None for non-action steps.
    ok: bool | None = None
    #: Prose, for `message` / `thought` / `error`.
    content: str | None = None
    tokens: int | None = None
    duration_ms: int | None = None
    #: The call hit the agent permission gate. Worth its own column because "the
    #: harness blocked it" and "the tool failed" look identical in `ok` and are
    #: completely different findings.
    gated: bool = False
    error: str | None = None
    ts: float = 0.0


class TrajectoryLabel(BaseModel):
    """A judgment about a run, or about one step of it.

    Additive and sourced: re-grading a run adds a row, it never rewrites one.
    """

    id: str = ""
    run_id: str = ""
    #: None labels the whole run.
    step_seq: int | None = None
    key: str
    value: str = ""
    score: float | None = None
    source: LabelSource = "human"
    rationale: str = ""
    created_at: float = 0.0


class TrajectoryRun(BaseModel):
    """One agent run. Steps are fetched separately unless asked for."""

    id: str
    dataset_id: str
    source: TrajectorySource
    #: The caller's own id for this run. Ingest is idempotent on
    #: `(dataset_id, external_id)`, so a retried SDK batch cannot duplicate a run.
    external_id: str | None = None
    #: The join key into `agent_turns` (interpretability). That table holds what
    #: the model was *shown*; this one holds what it *did*. Two halves of one turn.
    turn_id: str | None = None
    parent_run_id: str | None = None
    harness: str | None = None
    agent_id: str = ""
    agent_name: str = ""
    model: str = ""
    provider: str = ""
    #: The task, as one line. What semantic search matches against.
    goal: str = ""
    status: RunStatus = "running"
    outcome: Outcome | None = None
    reward: float | None = None
    steps: int = 0
    rounds: int = 0
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    started_at: float = 0.0
    finished_at: float | None = None
    duration_ms: int | None = None
    error: str = ""
    #: Provenance for `peer` and `imported` runs.
    node_id: str = ""
    person_id: str = ""
    #: Anything the source wanted to keep, including `raw` — the untranslated
    #: frames an importer normalized from, so a lossy normalizer can be fixed
    #: later without re-collecting the data.
    meta: dict[str, Any] = {}


class TrajectoryDetail(TrajectoryRun):
    """A run with its steps and labels — what the detail pane renders."""

    step_list: list[TrajectoryStep] = []
    labels: list[TrajectoryLabel] = []
    harness_detail: Harness | None = None


class Dataset(BaseModel):
    """A named collection of runs — the unit you capture into, export, and search.

    `capture` is the opt-in switch: nothing from the local orchestrator is recorded
    until some dataset has it set. Default off, and off means the recorder is a
    dict lookup that returns None.
    """

    id: str = Field(pattern=DATASET_ID_PATTERN)
    name: str
    description: str = ""
    source_kind: TrajectorySource = "local"
    capture: bool = False
    tags: list[str] = []
    schema_version: int = 1
    created_at: float = 0.0
    updated_at: float = 0.0
    run_count: int = 0


class CreateDataset(BaseModel):
    id: str = Field(pattern=DATASET_ID_PATTERN)
    name: str
    description: str = ""
    source_kind: TrajectorySource = "local"
    capture: bool = False
    tags: list[str] = []


class UpdateDataset(BaseModel):
    """Partial update. Omitted fields are left alone."""

    name: str | None = None
    description: str | None = None
    capture: bool | None = None
    tags: list[str] | None = None


class StepWrite(BaseModel):
    """One step as it arrives at `/ingest`. `seq` is assigned by the store when
    omitted, so a streaming client does not have to track it."""

    kind: StepKind = "action"
    seq: int | None = None
    round: int = 0
    role: StepRole | None = None
    name: str | None = None
    args: Any = None
    result: Any = None
    ok: bool | None = None
    content: str | None = None
    tokens: int | None = None
    duration_ms: int | None = None
    gated: bool = False
    error: str | None = None
    ts: float | None = None


class HarnessWrite(BaseModel):
    """A harness as supplied by an ingesting client. The fingerprint is computed
    server-side from the content — never accepted from the caller, or two clients
    hashing slightly differently would split one harness into two and quietly
    make every comparison across them empty."""

    agent_id: str = ""
    model: str = ""
    provider: str = ""
    system_prompt: str = ""
    tool_names: list[str] = []
    tool_schemas: dict[str, Any] = {}
    params: dict[str, Any] = {}
    label: str = ""


class TrajectoryWrite(BaseModel):
    """One run as it arrives at `/ingest`. The single shape every source
    normalizes to — the SDK, every importer, and each internal adapter."""

    dataset_id: str
    source: TrajectorySource = "external"
    external_id: str | None = None
    run_id: str | None = None
    turn_id: str | None = None
    parent_run_id: str | None = None
    harness: HarnessWrite | None = None
    agent_id: str = ""
    agent_name: str = ""
    model: str = ""
    provider: str = ""
    goal: str = ""
    status: RunStatus = "complete"
    outcome: Outcome | None = None
    reward: float | None = None
    rounds: int = 0
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    started_at: float | None = None
    finished_at: float | None = None
    error: str = ""
    node_id: str = ""
    person_id: str = ""
    meta: dict[str, Any] = {}
    step_list: list[StepWrite] = []
    labels: list[TrajectoryLabel] = []


class IngestRequest(BaseModel):
    runs: list[TrajectoryWrite] = []


class IngestResponse(BaseModel):
    #: Run ids in the order supplied, so a client can correlate.
    run_ids: list[str] = []
    created: int = 0
    #: Runs that matched an existing `(dataset_id, external_id)` and were updated
    #: in place rather than duplicated.
    merged: int = 0


class RunListResponse(BaseModel):
    runs: list[TrajectoryRun] = []
    total: int = 0


class DatasetListResponse(BaseModel):
    datasets: list[Dataset] = []


class HarnessListResponse(BaseModel):
    harnesses: list[Harness] = []


class LabelWrite(BaseModel):
    step_seq: int | None = None
    key: str
    value: str = ""
    score: float | None = None
    source: LabelSource = "human"
    rationale: str = ""

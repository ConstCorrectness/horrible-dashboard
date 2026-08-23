"""The eval contract: what a case is, what a result is, and what a sweep is.

The shapes here are the whole API of the module, so a few decisions are worth
stating once rather than rediscovering them in five files.

**A case names its own tool exposure.** `expose` is part of the case, not of the
run, because "can this model pick `open_pane` out of 38 tools" and "can it pick it
out of 6" are different questions and a suite is allowed to ask both. It is also
what makes a result comparable across models: if exposure came from whatever the
node's settings happened to be, two runs of the same suite would not be measuring
the same thing.

**Grading is declared per case, not per suite.** A suite that can only grade one
way pushes you into writing separate suites for the same capability, and then the
scoreboard cannot add them up.

**`no_call` is a first-class grade.** The interesting failure of a small model is
not picking the wrong tool, it is calling a tool when it should have answered — and
a harness that only checks "was the expected call made" scores that as a pass on
every other case and never sees it.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: How a case's expected calls are compared with what the model actually did.
#:
#: `judge` is in the vocabulary because a *result* row from an older run can carry
#: it, but it is not something a case may ask for — see `Expect.grade` below.
Grade = Literal["exact", "name_only", "subset", "sequence", "no_call", "judge"]

#: Grades a case may actually select. `judge` needs a provider and `graders` is
#: deliberately pure, so nothing routes it and it always returns False.
AUTHORABLE_GRADES = ("exact", "name_only", "subset", "sequence", "no_call")

#: What kind of thing a case measures, which decides which runner takes it.
CaseType = Literal["tool_call", "agent_task", "generative", "hf_benchmark"]

#: Which tools the model is shown.
#:
#: - `progressive` — what ships: a small core plus `list_tool_groups`/`load_tools`,
#:   with groups disclosed on demand. The default, because it is the thing under
#:   test most of the time.
#: - `all` — the whole catalog at once, capped by `TOOL_BUDGET`. The comparison
#:   that says whether progressive disclosure is helping or hurting.
#: - `explicit` — only the named groups, nothing else. For isolating one capability
#:   from the noise of the rest.
ExposeMode = Literal["progressive", "all", "explicit"]


class _Strict(BaseModel):
    """A case model that rejects fields it does not know.

    Pydantic's default is to *ignore* an unknown key, which for hand-authored
    JSONL is the worst possible behaviour: `target_regexp` instead of
    `target_regex` silently does nothing, the run scores zero, and the obvious
    conclusion is that the feature is broken rather than that the key is misspelled.
    This was not hypothetical — a case written against a newer schema than the
    running backend had two fields dropped on the way in, and the only symptom was
    a benchmark scoring 0.000.

    The cost is that an older backend refuses a suite written for a newer one. That
    is the right trade here: a loud refusal naming the field beats a silent
    misgrade, and `SuiteFormatError` already reports the line number.
    """

    model_config = ConfigDict(extra="forbid")


class ToolCall(_Strict):
    """One expected or observed call."""

    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Expose(_Strict):
    """Which tools this case shows the model."""

    mode: ExposeMode = "progressive"
    #: Groups loaded before the first round. Under `explicit` this *is* the
    #: catalog; under `progressive` it is a head start, the same one
    #: `preload_groups` gives a roster agent.
    preload: list[str] = Field(default_factory=list)


class Expect(_Strict):
    """What a correct answer looks like."""

    grade: Grade = "subset"
    calls: list[ToolCall] = Field(default_factory=list)
    #: `judge` only: what the judge is asked to check.
    rubric: str = ""
    #: `hf_benchmark` only: the metric name the project runner reports.
    metric: str = ""

    @field_validator("grade")
    @classmethod
    def _authorable(cls, grade: Grade) -> Grade:
        """Refuse a grade nothing can score.

        `judge` is declared but unrouted, so a case selecting it fails every time
        it runs — and the failure reads as the *model* getting it wrong, which is
        the one thing this module exists not to do. The frontend's picker already
        omits it; this closes the two paths that bypass the picker, an
        agent-authored case and a hand-edited `.jsonl`, and it fails where a
        mistake is cheap rather than twenty minutes into a sweep.
        """
        if grade not in AUTHORABLE_GRADES:
            raise ValueError(
                f"grade {grade!r} is declared but not scored by anything; "
                f"use one of {', '.join(AUTHORABLE_GRADES)}"
            )
        return grade


class HfBenchmark(_Strict):
    """A Hugging Face dataset run as one case, by the project-venv runner.

    Why this is a block on the case rather than a suite type: a suite is allowed to
    mix "can it pick the right tool" with "how does it do on GSM8K", and forcing
    them into separate suites would mean the scoreboard could not add them up.

    The whole block is a *description of a job*, not code. The runner materialises
    it into a script inside the project venv, which is where `datasets` and
    `evaluate` live — they need torch-class dependencies that must never become
    core deps of the backend.
    """

    #: Hub dataset id, e.g. `gsm8k` or `openai/openai_humaneval`.
    dataset: str
    #: The dataset config/subset name, when it has one (`gsm8k` needs `main`).
    config: str = ""
    #: A `datasets` split expression. Slicing is the norm here rather than an
    #: afterthought — a benchmark you cannot run a hundred rows of is a benchmark
    #: you run once and never again.
    split: str = "test[:50]"
    #: How a row becomes a prompt. `{column}` placeholders are filled from the row.
    input_template: str = "{question}"
    #: The column holding the reference answer.
    target_column: str = "answer"
    #: Pull the gradeable answer out of the reference before comparing.
    #:
    #: Not a nicety: most reasoning datasets store the worked solution in the
    #: answer column and mark the final answer with a separator. GSM8K's is
    #: `"...She makes 9 * 2 = $18 every day\n#### 18"`, so grading against the
    #: column as-is asks whether the model reproduced the dataset's own prose —
    #: which no model does, and the run scores zero for a reason that has nothing
    #: to do with the model. `#### (.+)` fixes that.
    #:
    #: The first capturing group wins; no match leaves the value untouched, so a
    #: pattern that stops matching degrades to "compare the whole thing" rather
    #: than to empty.
    target_regex: str = ""
    #: The same, applied to the model's reply. Use it when the model is expected to
    #: reason out loud and only the last figure counts — `(-?[\d.,]+)\s*$` is the
    #: usual one.
    prediction_regex: str = ""
    #: An `evaluate` metric id, or `exact_match` / `contains` which the harness
    #: implements itself so the common case needs no extra dependency.
    metric: str = "exact_match"
    #: Rows to run, after the split expression. Belt and braces: a split of
    #: `test` on a large dataset is a very long afternoon.
    limit: int = 50
    #: The score at or above which the case passes. A benchmark yields ONE case
    #: result, so it needs a pass mark to be a case at all.
    threshold: float = 0.5
    #: Prepended as the system message for every row. Benchmarks usually want a
    #: terse instruction ("Answer with the number only") that has nothing to do
    #: with the dashboard's orchestrator prompt.
    system: str = ""


class EvalCase(_Strict):
    """One question put to a model."""

    id: str
    type: CaseType = "tool_call"
    prompt: str
    #: Extra turns prepended before `prompt`, for a case that only makes sense
    #: mid-conversation ("do it again, but in the other pane").
    history: list[dict[str, Any]] = Field(default_factory=list)
    #: Workspace/editor context the frontend would normally attach to a turn.
    #: Supplied by the case so a result does not depend on what happened to be
    #: open on the machine that ran it.
    context: dict[str, Any] = Field(default_factory=dict)
    expose: Expose = Field(default_factory=Expose)
    expect: Expect = Field(default_factory=Expect)
    #: Required when `type` is `hf_benchmark`, ignored otherwise. Its presence is
    #: what routes a case to the project-venv runner instead of the in-node one.
    benchmark: HfBenchmark | None = None
    #: What each tool returns when called, keyed by tool name. A case is a
    #: *simulation*: the point is what the model chooses, and letting a real
    #: `files.write` run would make the suite destructive and unrepeatable.
    #: A tool with no fixture returns `{"ok": true}`.
    fixtures: dict[str, Any] = Field(default_factory=dict)
    #: Free-form labels for slicing the scoreboard — "layout", "negative",
    #: "multi-step".
    tags: list[str] = Field(default_factory=list)
    #: Why this case exists. Not decoration: a failing case whose intent nobody
    #: recorded gets deleted rather than fixed.
    note: str = ""

    def content_hash(self) -> str:
        """A hash of everything that decides whether an answer is right.

        The leaderboard's whole problem in one field. Two runs of "the same suite"
        are only comparable if the *questions* did not change in between — and case
        ids are stable across an edit, so `layout-open-terminal` before and after
        someone corrected its expectation are the same id and a different question.
        Comparing them shows a fine-tune "fixing" a case that was actually fixed by
        editing the exam.

        The `spec_id` precedent from the hitbox module: a content hash cannot be
        forgotten the way a hand-maintained revision can. `note` and `tags` are
        excluded because rewording why a case exists does not change what it asks.
        """
        import hashlib
        import json as _json

        payload = _json.dumps(
            {
                "type": self.type,
                "prompt": self.prompt,
                "history": self.history,
                "context": self.context,
                "expose": self.expose.model_dump(),
                "expect": self.expect.model_dump(),
                "fixtures": self.fixtures,
                "benchmark": self.benchmark.model_dump() if self.benchmark else None,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:12]


class EvalSuite(BaseModel):
    """A named set of cases, backed by a `.jsonl` file on disk."""

    id: str
    name: str
    description: str = ""
    path: str
    case_count: int = 0
    tags: list[str] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    #: `user` for one you made, `bundled` for one that ships with the repo. The
    #: two resolve into one list and cannot shadow each other — a bundled id is
    #: prefixed, a user id is 12 hex characters.
    source: Literal["user", "bundled"] = "user"
    #: Bundled suites are read-only: writing to one would edit a file in the repo,
    #: which you would lose on the next pull. Fork it and own the copy instead.
    read_only: bool = False


class CaseResult(BaseModel):
    """What one model did with one case."""

    case_id: str
    passed: bool
    grade: Grade
    #: One line saying *why*, in the terms the grade uses. The scoreboard shows
    #: this before anything else — a pass rate you cannot explain is not
    #: actionable.
    detail: str = ""
    expected: list[ToolCall] = Field(default_factory=list)
    actual: list[ToolCall] = Field(default_factory=list)
    answer: str = ""
    #: Rounds the loop took. A model that needed four rounds to reach the tool a
    #: better one found in one has not really passed the same way.
    rounds: int = 0
    #: How many tools were in front of the model on the final round, and how many
    #: the budget dropped. The whole point of the exposure question.
    tools_offered: int = 0
    tools_dropped: list[str] = Field(default_factory=list)
    #: Groups the model loaded for itself under progressive disclosure.
    groups_loaded: list[str] = Field(default_factory=list)
    duration_ms: float = 0.0
    error: str = ""
    #: The turn id, which is how the interpretability recorder's snapshot for this
    #: case is found — the exact prompt and tool schemas that went out.
    turn_id: str = ""
    #: The case's `content_hash` at the moment it was run. Empty on rows written
    #: before this existed, which the leaderboard reports as "cannot tell" rather
    #: than assuming they match.
    case_hash: str = ""


class RunTarget(BaseModel):
    """One model in a sweep.

    Provider *and* endpoint, not just a model name: a name means nothing on a
    server that does not have that model, and the same name on Ollama and on a
    llama.cpp build is two different builds of two different quantisations.
    """

    provider: str = ""
    endpoint: str = ""
    model: str
    #: For `llamacpp`: the GGUF this target scores. The server holds one model at a
    #: time, so naming the *file* rather than an alias is what lets a sweep load the
    #: right weights — and it is the whole of "eval the checkpoint I just converted",
    #: which used to be unexpressible because the picker offered builds, not models.
    model_path: str = ""
    #: What the scoreboard column is called. Defaults to the model name; set it
    #: when you are comparing a base against its own fine-tune, where the model
    #: names are unhelpfully similar.
    label: str = ""
    temperature: float | None = None


class EvalRun(BaseModel):
    """One suite against one model."""

    id: str
    suite_id: str
    label: str
    provider: str = ""
    endpoint: str = ""
    model: str = ""
    status: Literal["queued", "running", "done", "failed", "cancelled"] = "queued"
    #: Cases attempted and cases passed. Kept as counters rather than derived on
    #: read so a run that died halfway still reports what it managed.
    total: int = 0
    passed: int = 0
    completed: int = 0
    started_at: str = ""
    finished_at: str = ""
    error: str = ""
    #: The localtrack run this sweep reported aggregates to, when there is one.
    localtrack_run_id: str = ""
    #: The tool catalog this run actually saw — enabled skills and connected MCP
    #: servers, hashed by content (see `evals/fingerprint.py`). Empty on rows
    #: written before this existed, which Compare reports as "cannot tell" rather
    #: than as agreement.
    harness_hash: str = ""
    #: The harness itself, so a differing hash can say *what* differed.
    harness_json: str = ""


class StartRunRequest(BaseModel):
    suite_id: str
    targets: list[RunTarget]
    #: Cases to run; empty means all of them. Re-running one failing case against
    #: six models is the loop you actually spend time in.
    case_ids: list[str] = Field(default_factory=list)
    #: Report aggregates to localtrack under this project.
    localtrack_project: str = ""


class RunListResponse(BaseModel):
    runs: list[EvalRun]


class SuiteListResponse(BaseModel):
    suites: list[EvalSuite]


class ResultListResponse(BaseModel):
    run: EvalRun
    results: list[CaseResult]


class ExportRequest(BaseModel):
    """Turn results into a supervised fine-tuning dataset.

    `mode` decides which cases contribute, and the two modes are genuinely
    different jobs:

    - `correct` — the trajectories this run got **right**. Distillation: run a
      strong model, export what it did, train a small one on it.
    - `repair` — the ideal trajectory for cases this run got **wrong**, synthesised
      from the case's own `expect`. Targeted correction of a known weakness.

    `both` is the usual choice once you have a reference run to draw answers from.
    """

    run_id: str
    mode: Literal["correct", "repair", "both"] = "both"
    #: A run whose *answers* are borrowed when repairing a case that expects no
    #: tool call. A `no_call` case has no answer text in it — the correct output is
    #: prose — so without a reference run those cases cannot be repaired and are
    #: reported as skipped rather than invented.
    reference_run_id: str = ""
    #: Where to write. Relative paths land under `$HORRIBLE_DATA_DIR/evals/exports/`.
    out: str = ""


class ExportResponse(BaseModel):
    path: str
    examples: int
    #: Counted separately because they answer different questions: how much of this
    #: is the model already being right, versus how much is correction.
    correct: int = 0
    repaired: int = 0
    #: Cases that could not be turned into an example, and why. Never silent: an
    #: exporter that quietly drops a third of a suite produces a dataset whose
    #: coverage nobody can account for.
    skipped: list[str] = Field(default_factory=list)


class BenchmarkRunRequest(BaseModel):
    """Run the `hf_benchmark` cases of a suite in a training project's venv."""

    suite_id: str
    #: Existing training project to run in. Blank creates one named after the suite
    #: — the venv is the expensive part and reusing it across sweeps is the point.
    project_id: str = ""
    case_ids: list[str] = Field(default_factory=list)
    #: Where the model under test is served. Defaults to the node's own provider.
    endpoint: str = ""
    model: str = ""

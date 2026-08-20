"""`evals.*` agent tools.

Grouped under `evals`, with the name prefix and the declared `group=` identical —
a mismatched group costs a blurb, but an *omitted* one charges the tool's schema to
every round of every agent whether or not the turn has anything to do with evals.

These **do** include authoring (`createSuite`, `addCase`, `removeCase`, `fork`).
An earlier version withheld them on the grounds that the thing under test should
not write its own exam — which was too broad. Drafting twenty cases from a set of
tool descriptions is exactly the tedious work worth delegating; what matters is
that a case is a *claim about correct behaviour* and that you review the claim. The
suite is a `.jsonl` in your data dir, so reviewing means reading a diff.

Two guards make that safe rather than merely convenient. A bundled suite cannot be
written to at all (fork it), and `addCase` refuses a duplicate id rather than
silently overwriting a case you wrote — the agent's failure mode here is
enthusiasm, not malice.
"""

from __future__ import annotations

from typing import Any

from backend.sdk.registry import registry
from backend.sdk.types import AgentTool


async def _list_suites(**_: Any) -> dict[str, Any]:
    from backend.modules.evals import store

    return {
        "suites": [
            {
                "id": s.id,
                "name": s.name,
                "cases": s.case_count,
                "tags": s.tags,
                # A bundled suite cannot be edited, so saying so here saves the
                # model a failed write and a wasted round.
                "source": s.source,
                "readOnly": s.read_only,
            }
            for s in store.list_suites()
        ]
    }


async def _run(
    suite_id: str = "", model: str = "", provider: str = "", **_: Any
) -> dict[str, Any]:
    from backend.modules.evals.models import RunTarget
    from backend.modules.evals.routes import _live_agent_tools
    from backend.modules.evals import sweep

    if not suite_id:
        return {"error": "suite_id is required; call evals.listSuites first"}
    tools = _live_agent_tools()
    if not tools:
        return {
            "error": (
                "no browser is connected, so the frontend tool catalog is empty and "
                "every UI-shaped case would score zero"
            )
        }
    target = RunTarget(provider=provider, model=model or "", label=model or "this node")
    try:
        key = sweep.start_sweep(suite_id, [target], tools)
    except ValueError as exc:
        return {"error": str(exc)}
    return {
        "started": True,
        "key": key,
        "note": "the sweep runs in the background; call evals.results for the scoreboard",
    }


async def _results(run_id: str = "", suite_id: str = "", **_: Any) -> dict[str, Any]:
    from backend.modules.evals import store

    if not run_id:
        runs = store.list_runs(suite_id or None, limit=10)
        return {
            "runs": [
                {
                    "id": r.id,
                    "label": r.label,
                    "model": r.model,
                    "status": r.status,
                    "passed": r.passed,
                    "completed": r.completed,
                    "total": r.total,
                }
                for r in runs
            ]
        }
    run = store.get_run(run_id)
    if run is None:
        return {"error": f"no run {run_id!r}"}
    results = store.list_results(run_id)
    return {
        "run": {
            "id": run.id,
            "label": run.label,
            "model": run.model,
            "status": run.status,
            "passed": run.passed,
            "total": run.total,
        },
        # Failures only, and with the detail line. A scoreboard of 200 passing rows
        # is the one thing an agent's context does not need; what it is asked next
        # is invariably about the failures.
        "failures": [
            {
                "case": r.case_id,
                "why": r.detail,
                "expected": [c.name for c in r.expected],
            }
            for r in results
            if not r.passed
        ],
    }


# --- authoring ---------------------------------------------------------------


async def _create_suite(
    name: str = "", description: str = "", **_: Any
) -> dict[str, Any]:
    from backend.modules.evals import store

    if not name:
        return {"error": "name is required"}
    suite = store.create_suite(name, description)
    return {"id": suite.id, "name": suite.name, "path": suite.path, "cases": 0}


async def _fork(suite_id: str = "", name: str = "", **_: Any) -> dict[str, Any]:
    from backend.modules.evals import store

    if not suite_id:
        return {"error": "suite_id is required; call evals.listSuites first"}
    try:
        suite = store.fork_suite(suite_id, name)
    except ValueError as exc:
        return {"error": str(exc)}
    return {"id": suite.id, "name": suite.name, "cases": suite.case_count}


async def _add_case(
    suite_id: str = "",
    case: Any = None,
    **_: Any,
) -> dict[str, Any]:
    """Append one case. The whole case arrives as an object, validated here."""
    import json as _json

    from backend.modules.evals import store
    from backend.modules.evals.models import EvalCase

    if not suite_id:
        return {"error": "suite_id is required"}
    if case is None:
        return {"error": "case is required"}
    # Some models hand a JSON *string* for an object-typed argument. Accepting both
    # is cheaper than a retry round, and the schema is validated either way.
    if isinstance(case, str):
        try:
            case = _json.loads(case)
        except ValueError as exc:
            return {"error": f"case was a string but not valid JSON: {exc}"}

    suite = store.get_suite(suite_id)
    if suite is None:
        return {"error": f"no suite {suite_id!r}"}
    try:
        parsed = EvalCase.model_validate(case)
    except Exception as exc:  # noqa: BLE001 - the model needs the validation text
        return {"error": f"that case does not validate: {exc}"}

    try:
        existing = store.load_cases(suite)
    except store.SuiteFormatError as exc:
        return {"error": f"the suite file is malformed, fix it first: {exc}"}

    if any(c.id == parsed.id for c in existing):
        # Refused rather than replaced: results are keyed by (run, case), and
        # silently overwriting a case somebody wrote is the one unrecoverable thing
        # an authoring tool can do.
        return {
            "error": (
                f"a case with id {parsed.id!r} already exists in this suite; call "
                "evals.removeCase first if you meant to replace it"
            )
        }

    try:
        store.write_cases(suite, [*existing, parsed])
    except store.ReadOnlySuiteError as exc:
        return {"error": str(exc)}
    return {"added": parsed.id, "cases": len(existing) + 1}


async def _remove_case(
    suite_id: str = "", case_id: str = "", **_: Any
) -> dict[str, Any]:
    from backend.modules.evals import store

    if not suite_id or not case_id:
        return {"error": "suite_id and case_id are both required"}
    suite = store.get_suite(suite_id)
    if suite is None:
        return {"error": f"no suite {suite_id!r}"}
    try:
        existing = store.load_cases(suite)
    except store.SuiteFormatError as exc:
        return {"error": str(exc)}
    remaining = [c for c in existing if c.id != case_id]
    if len(remaining) == len(existing):
        return {"error": f"no case {case_id!r} in this suite"}
    try:
        store.write_cases(suite, remaining)
    except store.ReadOnlySuiteError as exc:
        return {"error": str(exc)}
    return {"removed": case_id, "cases": len(remaining)}


async def _export(
    run_id: str = "", mode: str = "both", reference_run_id: str = "", **_: Any
) -> dict[str, Any]:
    from backend.modules.evals import export
    from backend.modules.evals.routes import _live_agent_tools

    if not run_id:
        return {"error": "run_id is required; call evals.results for recent runs"}
    try:
        result = export.build(
            run_id,
            mode=mode,
            reference_run_id=reference_run_id,
            agent_tools=_live_agent_tools(),
        )
    except ValueError as exc:
        return {"error": str(exc)}
    return {
        "path": result.path,
        "examples": result.examples,
        "correct": result.correct,
        "repaired": result.repaired,
        # Truncated: a run where every case was skipped would otherwise return two
        # hundred near-identical lines into the turn's context.
        "skipped": result.skipped[:10],
        "skippedCount": len(result.skipped),
    }


_TOOLS = [
    AgentTool(
        name="evals.listSuites",
        description=(
            "List the evaluation suites on this node, with how many cases each "
            "holds. Call this first — every other evals tool needs a suite id."
        ),
        handler=_list_suites,
        group="evals",
    ),
    AgentTool(
        name="evals.run",
        description=(
            "Start an evaluation sweep of a suite against a model. Returns "
            "immediately: the sweep runs in the background and can take minutes, so "
            "read the scoreboard with evals.results rather than waiting."
        ),
        handler=_run,
        parameters={
            "suite_id": {
                "type": "string",
                "description": "Suite to run, from evals.listSuites.",
            },
            "model": {
                "type": "string",
                "description": "Model name. Omit to use this node's configured model.",
            },
            "provider": {
                "type": "string",
                "description": (
                    "Provider id (ollama, lmstudio, llamacpp...). Omit to use this "
                    "node's configured provider."
                ),
            },
        },
        required=["suite_id"],
        side_effect=True,
        group="evals",
    ),
    AgentTool(
        name="evals.createSuite",
        description=(
            "Create a new, empty evaluation suite to add cases to. Returns its id "
            "and the path of the .jsonl file behind it."
        ),
        handler=_create_suite,
        parameters={
            "name": {"type": "string", "description": "What to call the suite."},
            "description": {
                "type": "string",
                "description": "One line on what it covers.",
            },
        },
        required=["name"],
        side_effect=True,
        group="evals",
    ),
    AgentTool(
        name="evals.fork",
        description=(
            "Copy a suite into a new one the user owns and can edit. This is how a "
            "BUNDLED suite (one that ships with the app, id starting 'bundled:') "
            "becomes editable — those cannot be written to directly."
        ),
        handler=_fork,
        parameters={
            "suite_id": {"type": "string", "description": "Suite to copy."},
            "name": {"type": "string", "description": "Name for the copy. Optional."},
        },
        required=["suite_id"],
        side_effect=True,
        group="evals",
    ),
    AgentTool(
        name="evals.addCase",
        description=(
            "Append one case to a suite. `case` is the whole case object: "
            "{id, prompt, expose:{mode,preload}, expect:{grade,calls}, fixtures, "
            "tags, note}. Grades are exact | name_only | subset | sequence | "
            "no_call. Use no_call, with no expected calls, for a case the model "
            "should ANSWER rather than act on. Before writing what a case expects, "
            "read that tool's own description — an expectation that contradicts it "
            "grades the case author, not the model."
        ),
        handler=_add_case,
        parameters={
            "suite_id": {"type": "string", "description": "Suite to append to."},
            "case": {"type": "object", "description": "The case object."},
        },
        required=["suite_id", "case"],
        side_effect=True,
        group="evals",
    ),
    AgentTool(
        name="evals.removeCase",
        description="Delete one case from a suite by its id.",
        handler=_remove_case,
        parameters={
            "suite_id": {"type": "string", "description": "Suite to edit."},
            "case_id": {"type": "string", "description": "Case to remove."},
        },
        required=["suite_id", "case_id"],
        side_effect=True,
        group="evals",
    ),
    AgentTool(
        name="evals.export",
        description=(
            "Turn a run's results into a supervised fine-tuning dataset (.jsonl of "
            "messages + tools) for the training module. mode='correct' exports the "
            "trajectories the run got right (distillation from a strong model); "
            "'repair' synthesises the ideal trajectory for cases it got wrong; "
            "'both' does each. Cases expecting a plain answer need a "
            "reference_run_id to borrow the answer text from, and are reported as "
            "skipped otherwise rather than invented."
        ),
        handler=_export,
        parameters={
            "run_id": {"type": "string", "description": "Run to export from."},
            "mode": {
                "type": "string",
                "enum": ["correct", "repair", "both"],
                "description": "Which cases contribute. Default both.",
            },
            "reference_run_id": {
                "type": "string",
                "description": "A stronger model's run of the same suite, to borrow answers from.",
            },
        },
        required=["run_id"],
        side_effect=True,
        group="evals",
    ),
    AgentTool(
        name="evals.results",
        description=(
            "Read evaluation results. With no run_id, the recent runs and their "
            "pass counts; with one, that run's FAILING cases and why each failed."
        ),
        handler=_results,
        parameters={
            "run_id": {"type": "string", "description": "A specific run."},
            "suite_id": {"type": "string", "description": "Filter runs by suite."},
        },
        group="evals",
    ),
]


def register_agent_tools() -> None:
    """Register the group. Called from `backend/app.py` at startup."""

    for tool in _TOOLS:
        registry.agent_tools[tool.name] = tool

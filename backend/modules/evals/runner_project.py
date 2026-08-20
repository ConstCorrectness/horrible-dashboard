"""Runner B: benchmarks that need a Python venv.

Evaluation splits into two things, and pretending one runner covers both is where
this would go wrong.

**Runner A** (`runner_agent`) measures *this node's* tool calling. It has to run
in-process, because the thing under test is the orchestrator loop and the tool
catalog a live browser pushed onto the socket.

**Runner B — here** — runs anything that needs `datasets`, `evaluate` or an
`lm-eval`-style harness. Those pull torch-class dependencies that must never
become core deps of the backend, so the work happens in a **training project's
venv**, installed through `envs.install` (`uv pip install --python`) because a uv
venv has no pip at all and `python -m pip` fails inside every one of them.

Both write to the same `eval_runs` / `eval_results` tables, so a suite that mixes
tool-calling cases with a benchmark still adds up to one scoreboard.

## Why a script and not a notebook

The design note for this module said "a scaffolded notebook". A script is what
actually landed, and the reason is worth recording rather than quietly changing:
a sweep is a **batch job**, and the notebook path is built around an interactive
kernel session tied to a websocket connection (`TrainingKernelManager._open` takes
a `conn`). Driving that headlessly to run twelve rows would mean owning a session
lifecycle for no gain.

The readability the notebook was chosen for is kept: the script lands in the
project as `evals/run_benchmark.py`, is heavily commented, and is an ordinary file
you can open in the editor, edit and run yourself. `envs._run`'s blocking `Popen`
on a thread is reused as-is — it is already the Windows-safe spawn pattern, since
`asyncio.create_subprocess_exec` breaks on the SelectorEventLoop uvicorn uses
under `--reload`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from backend.modules.evals import harness
from backend.modules.evals.models import CaseResult, EvalCase

logger = logging.getLogger(__name__)

#: Per-row HTTP timeout inside the harness. Generous: a small model on CPU
#: genuinely takes tens of seconds a row, and a false timeout reads as "the model
#: got it wrong", which is the worst kind of wrong result.
ROW_TIMEOUT_S = 180

#: Packages the harness needs. `evaluate` is deliberately NOT here — the two
#: metrics most cases use are implemented in the harness itself, and `evaluate`
#: pulls a large tree. It is installed on demand when a case asks for a metric the
#: harness does not implement.
BASE_REQUIREMENTS = ["datasets"]

#: Metrics the harness scores without help.
BUILTIN_METRICS = {"exact_match", "contains"}


def requirements_for(cases: list[EvalCase]) -> list[str]:
    """What this set of benchmark cases needs installed.

    Computed from the cases rather than fixed, so a suite that only ever uses
    `exact_match` never waits for `evaluate` to resolve.
    """
    needed = list(BASE_REQUIREMENTS)
    if any(c.benchmark and c.benchmark.metric not in BUILTIN_METRICS for c in cases):
        needed.append("evaluate")
    return needed


def ensure_project(suite_name: str, project_id: str = "") -> Any:
    """The training project a benchmark runs in, created if need be.

    Reused across sweeps on purpose: the venv is the expensive part, and a fresh
    project per run would mean re-resolving `datasets` every time.
    """
    from backend.modules.training import projects

    if project_id:
        project = projects.get_project(project_id)
        if project is None:
            raise ValueError(f"no training project {project_id!r}")
        return project

    name = f"evals-{suite_name}"[:48]
    for existing in projects.list_projects():
        if existing.name == name:
            return existing
    return projects.create_project(name, [], "3.12")


async def prepare_env(
    project: Any, cases: list[EvalCase], progress: Callable[[str], None]
) -> None:
    """Create the venv if missing and install what these cases need.

    On a thread: every call in `envs` is blocking `subprocess` by design, and
    running one on the event loop would stall every websocket on the node for the
    minute or two a `datasets` resolve takes.
    """
    from backend.modules.training import envs, projects

    requirements = requirements_for(cases)

    def work() -> None:
        if not envs.venv_exists(project):
            progress("creating the project venv…")
            envs.create(project, progress)
        envs.install(project, requirements, progress)

    await asyncio.to_thread(work)
    if not project.venv_ready:
        project.venv_ready = True
        projects.update_project(project)


def _job_for(case: EvalCase, endpoint: str, model: str) -> dict[str, Any]:
    bench = case.benchmark
    assert bench is not None  # callers filter on it
    return {
        "dataset": bench.dataset,
        "config": bench.config,
        "split": bench.split,
        "input_template": bench.input_template,
        "target_column": bench.target_column,
        "target_regex": bench.target_regex,
        "prediction_regex": bench.prediction_regex,
        "metric": bench.metric,
        "limit": bench.limit,
        "system": bench.system,
        "endpoint": endpoint,
        "model": model,
        "timeout": ROW_TIMEOUT_S,
    }


def script_path(project: Any, case: EvalCase) -> Path:
    """Where this case's harness lands. One file per case, named after it, so a
    suite of five benchmarks leaves five readable scripts rather than one that was
    overwritten four times."""
    directory = Path(project.root) / "evals"
    directory.mkdir(parents=True, exist_ok=True)
    # A case id reaches the filesystem here, so it is reduced to a safe stem
    # rather than trusted — an id is user-authored text.
    stem = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in case.id)[:60]
    return directory / f"{stem or 'benchmark'}.py"


def _run_script(
    python: str, script: Path, cwd: str, progress: Callable[[str], None]
) -> dict[str, Any]:
    """Run the harness to completion and return the JSON it printed.

    Blocking — always called through `asyncio.to_thread`. Output is streamed as it
    arrives so a long benchmark shows progress rather than nothing; the result is
    the one line carrying the sentinel.
    """
    proc = subprocess.Popen(
        [python, str(script)],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.stdout is not None
    payload: dict[str, Any] | None = None
    tail: list[str] = []
    for line in proc.stdout:
        stripped = line.rstrip()
        if not stripped:
            continue
        if stripped.startswith(harness.RESULT_SENTINEL):
            try:
                payload = json.loads(stripped[len(harness.RESULT_SENTINEL) :])
            except json.JSONDecodeError as exc:
                payload = {"error": f"harness printed unparseable result: {exc}"}
            continue
        progress(stripped)
        # Keep the tail for the failure message: when a script dies, the last
        # thing it said is almost always the reason, and a bare exit code is not
        # something anyone can act on.
        tail.append(stripped)
        del tail[:-15]

    code = proc.wait()
    if payload is None:
        detail = "\n".join(tail[-6:]) or "no output"
        return {
            "error": f"the harness exited with {code} and printed no result:\n{detail}"
        }
    return payload


async def run_case(
    case: EvalCase,
    project: Any,
    *,
    endpoint: str,
    model: str,
    progress: Callable[[str], None] = lambda _line: None,
) -> CaseResult:
    """Run one `hf_benchmark` case and turn its score into a case result."""
    from backend.modules.training import envs

    bench = case.benchmark
    if bench is None:
        return CaseResult(
            case_id=case.id,
            passed=False,
            grade=case.expect.grade,
            detail="this case has type hf_benchmark but no benchmark block",
            error="missing benchmark block",
        )

    script = script_path(project, case)
    script.write_text(harness.render(_job_for(case, endpoint, model)), encoding="utf-8")
    progress(f"wrote {script}")

    started = time.monotonic()
    payload = await asyncio.to_thread(
        _run_script, str(envs.python_path(project)), script, project.root, progress
    )
    duration_ms = (time.monotonic() - started) * 1000.0

    if payload.get("error"):
        return CaseResult(
            case_id=case.id,
            passed=False,
            grade=case.expect.grade,
            detail=str(payload["error"]),
            error=str(payload["error"]),
            duration_ms=duration_ms,
        )

    score = float(payload.get("score", 0.0))
    rows = int(payload.get("rows", 0))
    errors = int(payload.get("errors", 0))
    passed = score >= bench.threshold

    detail = (
        f"{bench.metric} {score:.3f} over {rows} rows (pass mark {bench.threshold:.2f})"
    )
    if errors:
        # Surfaced rather than folded into the score: "scored 0.4" and "scored 0.4
        # because a fifth of the rows raised" are different results.
        detail += f" — {errors} row(s) errored and scored zero"

    return CaseResult(
        case_id=case.id,
        passed=passed,
        grade=case.expect.grade,
        detail=detail,
        answer=json.dumps(payload.get("samples", [])[:2]),
        rounds=rows,
        duration_ms=duration_ms,
    )

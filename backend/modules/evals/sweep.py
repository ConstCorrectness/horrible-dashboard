"""Running a suite against one or more models, and telling the UI about it.

## Why this does not ride the shared task queue

That queue is serial. A 200-case sweep across six models is tens of minutes of
work, and parking every library ingest and every notebook behind it would make the
eval module something you avoid running — which defeats the point of having it. So
a sweep runs detached under its own semaphore, the same reasoning karaoke downloads
use, and the concurrency limit is per *target* rather than per case: two models
answering at once is two model servers busy, while twenty cases at once against one
Ollama would just queue inside Ollama and report timings that mean nothing.

## Why progress broadcasts rather than replies

A sweep outlives the request that started it and often the pane that was watching.
`broadcast_event` reaches every connection, so closing the results pane and
reopening it does not lose the run, and the agent's `evals.*` tools see the same
state with no pane open at all — the karaoke session rule.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from backend.modules.evals import store
from backend.modules.evals.models import (
    CaseResult,
    EvalCase,
    EvalRun,
    RunTarget,
)
from backend.modules.ws import broadcast_event

logger = logging.getLogger(__name__)

CHANNEL = "evals"

#: Models answered concurrently. Two is deliberate rather than timid: most local
#: setups have one GPU and one model server, so a higher number does not finish
#: sooner — it just makes every per-case duration a measurement of queueing.
_target_semaphore = asyncio.Semaphore(2)

#: Live sweeps, so a second start can be refused and a cancel can find its task.
_running: dict[str, asyncio.Task[Any]] = {}


async def _emit(event: str, data: dict[str, Any]) -> None:
    try:
        await broadcast_event(CHANNEL, event, data)
    except Exception:  # noqa: BLE001
        # Telemetry must never cost a run its results. A pane that missed an update
        # re-reads the run on its next poll; a sweep that died mid-way because
        # nobody was listening is unrecoverable.
        logger.debug("evals: progress broadcast failed", exc_info=True)


def _resolve_target(target: RunTarget) -> tuple[Any, str]:
    """The provider info and endpoint for one target.

    Falls back to the node's configured agent provider when the target names none,
    which is what makes "run this suite against whatever I am using" a one-click
    thing. Resolved per target rather than once per sweep, because comparing the
    orchestrator's provider against a local llama.cpp build is the *normal* sweep.
    """
    from backend.modules.agent.providers import PROVIDERS
    from backend.modules.agent.roster import resolve_provider
    from backend.modules.agent.routes import _load_config

    if target.provider:
        info = PROVIDERS.get(target.provider)
        if info is None:
            raise ValueError(f"unknown provider {target.provider!r}")
        return info, (target.endpoint or info.default_endpoint)

    config = _load_config()
    if config is None:
        raise ValueError("no agent provider is configured on this node")
    info, endpoint = resolve_provider(config, "main")
    return info, (target.endpoint or endpoint)


async def _run_one_target(
    suite_id: str,
    cases: list[EvalCase],
    target: RunTarget,
    agent_tools: list[dict[str, Any]],
    localtrack_project: str,
) -> EvalRun:
    from backend.modules.evals.runner_agent import run_case

    run = store.create_run(
        suite_id=suite_id,
        label=target.label or target.model,
        provider=target.provider,
        endpoint=target.endpoint,
        model=target.model,
        total=len(cases),
    )
    await _emit("run_started", run.model_dump())

    try:
        info, endpoint = _resolve_target(target)
    except Exception as exc:  # noqa: BLE001
        # A target that cannot even be resolved fails as a *run*, not as N failed
        # cases: "your endpoint is wrong" and "this model gets everything wrong"
        # look identical on a scoreboard otherwise.
        store.update_run(run.id, status="failed", error=str(exc), finished_at=_now())
        await _emit("run_failed", {"runId": run.id, "error": str(exc)})
        return store.get_run(run.id) or run

    store.update_run(run.id, status="running")
    tracker = _LocalTrack(localtrack_project, run)

    # A suite may mix tool-calling cases with benchmarks, and the two need
    # different runners — one in-process against the orchestrator, one in a
    # project venv. The venv is prepared once, and only if a benchmark is actually
    # present, so a pure tool-calling sweep never waits for `datasets` to resolve.
    bench_cases = [c for c in cases if c.type == "hf_benchmark"]
    project = None
    if bench_cases:
        try:
            project = await _prepare_benchmarks(suite_id, bench_cases, run.id)
        except Exception as exc:  # noqa: BLE001
            # Recorded rather than raised: the tool-calling half of the suite is
            # still perfectly runnable, and losing it because `uv` is missing
            # would be a poor trade.
            logger.warning("evals: benchmark environment unavailable: %s", exc)
            await _emit(
                "run_note",
                {"runId": run.id, "note": f"benchmark cases will fail: {exc}"},
            )

    async with _target_semaphore:
        for index, case in enumerate(cases):
            result: CaseResult
            if case.type == "hf_benchmark":
                result = await _run_benchmark_case(
                    case, project, endpoint, target.model, run.id
                )
            else:
                result = await run_case(
                    case,
                    agent_tools,
                    provider=info,
                    endpoint=endpoint,
                    model=target.model,
                    temperature=target.temperature
                    if target.temperature is not None
                    else 0.0,
                )
            # Stamped here rather than in the runners: both of them produce a
            # result and neither should have to remember. What it pins is that a
            # later comparison is between the same questions, not just the same
            # case ids.
            result.case_hash = case.content_hash()
            store.save_result(run.id, result)
            tracker.log(index, result)
            await _emit(
                "case_done",
                {
                    "runId": run.id,
                    "index": index,
                    "total": len(cases),
                    "result": result.model_dump(),
                },
            )

    finished = store.get_run(run.id) or run
    store.update_run(run.id, status="done", finished_at=_now())
    tracker.finish(finished)
    finished = store.get_run(run.id) or run
    await _emit("run_done", finished.model_dump())
    return finished


async def _prepare_benchmarks(suite_id: str, cases: list[EvalCase], run_id: str) -> Any:
    """The training project a suite's benchmarks run in, venv ready."""
    from backend.modules.evals import runner_project

    suite = store.get_suite(suite_id)
    project = runner_project.ensure_project(suite.name if suite else suite_id)

    async def progress(line: str) -> None:
        await _emit("bench_progress", {"runId": run_id, "line": line})

    loop = asyncio.get_running_loop()

    def sink(line: str) -> None:
        # `envs` calls this from a worker thread, so the broadcast has to be
        # scheduled back onto the loop rather than awaited here.
        asyncio.run_coroutine_threadsafe(progress(line), loop)

    await runner_project.prepare_env(project, cases, sink)
    return project


async def _run_benchmark_case(
    case: EvalCase, project: Any, endpoint: str, model: str, run_id: str
) -> CaseResult:
    """One benchmark case, or an honest failure if the venv never came up."""
    from backend.modules.evals import runner_project

    if project is None:
        return CaseResult(
            case_id=case.id,
            passed=False,
            grade=case.expect.grade,
            detail="the benchmark environment could not be prepared",
            error="no project venv",
        )

    loop = asyncio.get_running_loop()

    def sink(line: str) -> None:
        asyncio.run_coroutine_threadsafe(
            _emit("bench_progress", {"runId": run_id, "line": line}), loop
        )

    return await runner_project.run_case(
        case, project, endpoint=endpoint, model=model, progress=sink
    )


class _LocalTrack:
    """Mirrors a sweep into localtrack, or does nothing at all.

    Wrapped in swallows throughout: localtrack is a *reporting* destination, and a
    tracking failure must never cost a run the results already in `app.db`. The
    scoreboard reads the database; localtrack is the place you go to compare a base
    model against its own fine-tune over time.
    """

    def __init__(self, project: str, run: EvalRun) -> None:
        self.run_id = ""
        if not project:
            return
        try:
            from backend.modules.localtrack import store as lt

            lt.create_project(project, project)
            created = lt.create_run(
                # `run_id` is positional and first; passing None lets localtrack
                # mint one rather than colliding with our own run ids.
                None,
                project_id=project,
                name=f"{run.label} · {run.suite_id}",
                config={"model": run.model, "suite": run.suite_id},
                tags=["evals", run.suite_id],
            )
            self.run_id = created.id
            store.update_run(run.id, localtrack_run_id=created.id)
        except Exception:  # noqa: BLE001
            logger.debug("evals: localtrack unavailable for this sweep", exc_info=True)

    def log(self, step: int, result: CaseResult) -> None:
        if not self.run_id:
            return
        try:
            from backend.modules.localtrack import store as lt
            from backend.modules.localtrack.models import MetricLogItem

            # One item carrying every metric for this step: `MetricLogItem` holds a
            # `metrics` dict, so a case is one entry rather than three.
            lt.ingest_metrics(
                [
                    MetricLogItem(
                        run_id=self.run_id,
                        step=step,
                        metrics={
                            "passed": float(result.passed),
                            "duration_ms": result.duration_ms,
                            "rounds": float(result.rounds),
                            "tools_offered": float(result.tools_offered),
                        },
                    )
                ]
            )
        except Exception:  # noqa: BLE001
            logger.debug("evals: metric ingest failed", exc_info=True)

    def finish(self, run: EvalRun) -> None:
        if not self.run_id:
            return
        try:
            from backend.modules.localtrack import store as lt

            rate = (run.passed / run.completed) if run.completed else 0.0
            lt.update_run(
                self.run_id,
                status="finished",
                summary={"pass_rate": rate, "passed": run.passed, "total": run.total},
            )
        except Exception:  # noqa: BLE001
            logger.debug("evals: localtrack finish failed", exc_info=True)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


async def run_sweep(
    suite_id: str,
    targets: list[RunTarget],
    agent_tools: list[dict[str, Any]],
    case_ids: list[str] | None = None,
    localtrack_project: str = "",
) -> list[EvalRun]:
    """Run one suite against every target. One `EvalRun` per target."""
    suite = store.get_suite(suite_id)
    if suite is None:
        raise ValueError(f"no suite {suite_id!r}")
    cases = store.load_cases(suite)
    if case_ids:
        wanted = set(case_ids)
        cases = [c for c in cases if c.id in wanted]
    if not cases:
        raise ValueError("no cases to run")

    runs = await asyncio.gather(
        *(
            _run_one_target(suite_id, cases, t, agent_tools, localtrack_project)
            for t in targets
        )
    )
    return list(runs)


def start_sweep(
    suite_id: str,
    targets: list[RunTarget],
    agent_tools: list[dict[str, Any]],
    case_ids: list[str] | None = None,
    localtrack_project: str = "",
) -> str:
    """Kick a sweep off in the background and return its key.

    Detached deliberately: the HTTP request that starts a sweep must not hold a
    connection open for the minutes it takes, and the pane that started it must be
    free to close.
    """
    key = f"{suite_id}:{time.time():.0f}"

    async def _go() -> None:
        try:
            await run_sweep(
                suite_id, targets, agent_tools, case_ids, localtrack_project
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("evals: sweep failed")
            await _emit("sweep_failed", {"suiteId": suite_id, "error": str(exc)})
        finally:
            _running.pop(key, None)

    _running[key] = asyncio.create_task(_go())
    return key


def active_sweeps() -> list[str]:
    return sorted(_running)

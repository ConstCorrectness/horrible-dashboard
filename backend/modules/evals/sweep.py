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
from contextlib import AsyncExitStack
from typing import Any

from backend.modules.evals import fingerprint, llama_target, store
from backend.modules.evals.models import (
    CaseResult,
    EvalCase,
    EvalRun,
    RunTarget,
)
from backend.modules.localtrack.mirror import RunMirror
from backend.modules.ws import broadcast_event

logger = logging.getLogger(__name__)

CHANNEL = "evals"

#: Models answered concurrently. Two is deliberate rather than timid: most local
#: setups have one GPU and one model server, so a higher number does not finish
#: sooner — it just makes every per-case duration a measurement of queueing.
_target_semaphore = asyncio.Semaphore(2)

#: Live sweeps, so a second start can be refused and a cancel can find its task.
_running: dict[str, asyncio.Task[Any]] = {}

#: Strong references to fire-and-forget broadcasts, so the loop does not collect
#: one mid-flight. `create_task` alone keeps only a weak reference.
_detached: set[asyncio.Task[Any]] = set()

#: What each live sweep is, for the pane that offers to stop it. Kept beside the
#: task rather than parsed back out of the key: a key is an internal handle and
#: reading a suite id out of it would make the format load-bearing.
_sweep_info: dict[str, dict[str, Any]] = {}


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
        if target.endpoint:
            return info, target.endpoint
        # Not `info.default_endpoint`: a spawned llama-server takes an ephemeral
        # port when 8080 is occupied, so the default points at nothing while the
        # real server sits elsewhere. `_endpoint_for` asks the live manager first —
        # the same reason it exists for the agent.
        from backend.modules.agent.routes import _endpoint_for

        return info, _endpoint_for(info, None)

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
    harness: tuple[str, str] = ("", ""),
    started: list[str] | None = None,
) -> EvalRun:
    from backend.modules.evals.runner_agent import run_case

    run = store.create_run(
        suite_id=suite_id,
        label=target.label or target.model,
        provider=target.provider,
        endpoint=target.endpoint,
        model=target.model,
        total=len(cases),
        harness_hash=harness[0],
        harness_json=harness[1],
    )
    # Reported upward the moment the row exists, so a cancel can name the runs it
    # has to close out. Cancellation arrives as a `CancelledError` raised inside
    # whichever await is in flight, and by the time it reaches the sweep's own
    # task the gather has already discarded which children were running — so the
    # ids have to be handed out on the way in, not recovered on the way out.
    if started is not None:
        started.append(run.id)
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

    async with AsyncExitStack() as stack:
        # A llama.cpp target names a GGUF, and the server holds one model at a time,
        # so the weights are loaded for the length of this target and the user's own
        # server is put back afterwards (see evals/llama_target.py). The endpoint is
        # whatever the load produced — the port is picked at spawn.
        if target.provider == "llamacpp" and target.model_path:
            try:
                endpoint = await stack.enter_async_context(
                    llama_target.serving(target.model_path)
                )
            except Exception as exc:  # noqa: BLE001
                # Same reasoning as an unresolvable target: "the weights never
                # loaded" and "this model gets everything wrong" must not look
                # alike on a scoreboard.
                store.update_run(
                    run.id, status="failed", error=str(exc), finished_at=_now()
                )
                await _emit("run_failed", {"runId": run.id, "error": str(exc)})
                return store.get_run(run.id) or run

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
    """Mirrors a sweep into localtrack.

    A thin adapter over `localtrack.mirror.RunMirror`, which is shared with the
    training module — the four moves and the swallow-everything posture are the
    same, only the metric names differ. What stays here is the part that is
    genuinely about evals: the metric vocabulary, and writing the localtrack run id
    back onto the `EvalRun` so the two records can be joined later.
    """

    def __init__(self, project: str, run: EvalRun) -> None:
        self._mirror = RunMirror(
            project,
            name=f"{run.label} · {run.suite_id}",
            config={"model": run.model, "suite": run.suite_id},
            tags=["evals", run.suite_id],
        )
        self.run_id = self._mirror.run_id
        if self.run_id:
            try:
                store.update_run(run.id, localtrack_run_id=self.run_id)
            except Exception:  # noqa: BLE001
                logger.debug(
                    "evals: could not record the localtrack run id", exc_info=True
                )

    def log(self, step: int, result: CaseResult) -> None:
        self._mirror.log(
            step,
            {
                "passed": float(result.passed),
                "duration_ms": result.duration_ms,
                "rounds": float(result.rounds),
                "tools_offered": float(result.tools_offered),
            },
        )

    def finish(self, run: EvalRun) -> None:
        rate = (run.passed / run.completed) if run.completed else 0.0
        self._mirror.finish(
            summary={"pass_rate": rate, "passed": run.passed, "total": run.total}
        )


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


async def run_sweep(
    suite_id: str,
    targets: list[RunTarget],
    agent_tools: list[dict[str, Any]],
    case_ids: list[str] | None = None,
    localtrack_project: str = "",
    started: list[str] | None = None,
) -> list[EvalRun]:
    """Run one suite against every target. One `EvalRun` per target.

    `started` is filled with each run id as its row is created — the handle a
    cancel needs to close those rows out.
    """
    suite = store.get_suite(suite_id)
    if suite is None:
        raise ValueError(f"no suite {suite_id!r}")
    cases = store.load_cases(suite)
    if case_ids:
        wanted = set(case_ids)
        cases = [c for c in cases if c.id in wanted]
    if not cases:
        raise ValueError("no cases to run")

    # Read once for the whole sweep, not per target: every run in a sweep is
    # answering the same suite with the same catalog, and reading it per target
    # would let a skill toggled mid-sweep make two runs of one comparison
    # incomparable without anything saying so.
    harness = fingerprint.compute()

    runs = await asyncio.gather(
        *(
            _run_one_target(
                suite_id, cases, t, agent_tools, localtrack_project, harness, started
            )
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
    started: list[str] = []

    async def _go() -> None:
        try:
            await run_sweep(
                suite_id, targets, agent_tools, case_ids, localtrack_project, started
            )
        except asyncio.CancelledError:
            # A cancelled sweep must not leave rows reading `running` forever. They
            # are closed as `cancelled` rather than `failed`, because "you stopped
            # it" and "it broke" are different facts and the scoreboard treats a
            # failed run as a signal about the model. Re-raised so the task ends
            # cancelled, which is what it is.
            _close_out(started)
            # Detached, not awaited: this task is already cancelling, and an await
            # inside the handler can be cancelled again before it lands — leaving
            # the panes watching a sweep that simply stops updating. The rows are
            # already closed out synchronously above, so the broadcast is the only
            # thing at stake.
            _detached.add(
                task := asyncio.create_task(
                    _emit("sweep_cancelled", {"suiteId": suite_id, "runs": started})
                )
            )
            task.add_done_callback(_detached.discard)
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("evals: sweep failed")
            await _emit("sweep_failed", {"suiteId": suite_id, "error": str(exc)})
        finally:
            _running.pop(key, None)
            _sweep_info.pop(key, None)

    _sweep_info[key] = {
        "key": key,
        "suiteId": suite_id,
        "targets": [t.label or t.model for t in targets],
        "startedAt": _now(),
    }
    _running[key] = asyncio.create_task(_go())
    return key


def _close_out(run_ids: list[str]) -> None:
    """Mark every run of a cancelled sweep that had not finished.

    Only the unfinished ones: a target that completed before the cancel landed has
    real results, and rewriting its status would throw away a measurement that was
    actually taken.
    """
    for run_id in run_ids:
        try:
            run = store.get_run(run_id)
            if run is None or run.status not in ("queued", "running"):
                continue
            store.update_run(run_id, status="cancelled", finished_at=_now())
        except Exception:  # noqa: BLE001
            logger.debug("evals: could not close out run %s", run_id, exc_info=True)


def active_sweeps() -> list[dict[str, Any]]:
    """The sweeps running right now, newest last.

    Dicts rather than bare keys: a pane offering to stop something has to be able
    to say *what* it is stopping, and a key is an opaque handle.
    """
    return [_sweep_info[k] for k in sorted(_running) if k in _sweep_info]


def cancel_sweep(key: str) -> bool:
    """Stop a running sweep. False when there is no such sweep.

    Cancelling the task rather than setting a flag the loop checks: the time is
    spent inside a provider call, so a flag would not be read until the case in
    flight finished — which on a stuck endpoint is the timeout, and the whole
    reason to press stop.
    """
    task = _running.get(key)
    if task is None:
        return False
    task.cancel()
    return True

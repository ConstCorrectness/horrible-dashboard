"""The durable research runner: a process-global worker pool over run rows.

Temporal-inspired without the Temporal server: every stage is a checkpointed
step row (output + transcript persisted atomically with the status flip), steps
retry with exponential backoff up to `max_attempts`, and `start()`'s resume pass
re-enqueues every non-terminal run on boot — a backend restart mid-run costs at
most the step that was in flight. Failed **subagent** steps do *not* fail the
run: synthesis proceeds with the survivors (checkpoint-not-restart). Progress
streams on the `research` /ws channel.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.modules.research import engine, runstore
from backend.modules.research.broadcast import publish_run, publish_step
from backend.modules.settings.routes import get_value

logger = logging.getLogger(__name__)

_BACKOFF_CAP_S = 120.0


class RunCancelled(Exception):
    """Raised inside a run when its cancel flag is set."""


class ResearchRunner:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._workers: list[asyncio.Task[None]] = []
        self._running = False
        self._enqueued: set[str] = set()

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        runstore.init_research_db()
        # Resume pass: anything non-terminal goes back on the queue; steps stuck
        # `running` (crash) reset to `pending` keeping their attempt count.
        for run in runstore.list_resumable_runs():
            reset = runstore.reset_running_steps(run["id"])
            if reset:
                logger.info(
                    "research resume: run %s had %d in-flight step(s) reset",
                    run["id"],
                    reset,
                )
            self.enqueue(run["id"])
        workers = max(1, int(get_value("research.maxConcurrentRuns", 1) or 1))
        self._workers = [
            asyncio.create_task(self._worker_loop(), name=f"research-runner-{i}")
            for i in range(workers)
        ]

    def stop(self) -> None:
        self._running = False
        for task in self._workers:
            task.cancel()
        self._workers = []
        # In-flight steps stay `running` in the DB; the next boot's resume pass
        # resets them — that's the durability story, not a bug.

    def enqueue(self, run_id: str) -> None:
        if run_id in self._enqueued:
            return
        self._enqueued.add(run_id)
        self._queue.put_nowait(run_id)

    # -- worker --------------------------------------------------------------

    async def _worker_loop(self) -> None:
        while self._running:
            run_id = await self._queue.get()
            self._enqueued.discard(run_id)
            try:
                await self._execute_run(run_id)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — one broken run must not kill the pool
                logger.exception("research run %s crashed the worker", run_id)
                self._set_run(run_id, status="failed", error="internal runner error")

    def _set_run(self, run_id: str, **fields: Any) -> None:
        runstore.update_run(run_id, **fields)
        run = runstore.get_run(run_id)
        if run is not None:
            publish_run(run)

    def _cancelled(self, run_id: str) -> bool:
        return runstore.cancel_requested(run_id)

    async def _execute_run(self, run_id: str) -> None:
        run = runstore.get_run(run_id)
        if run is None or run["status"] in runstore.TERMINAL_STATUSES:
            return
        logger.info("research run %s: starting (%s)", run_id, run["query"][:80])
        try:
            lead, sub = engine.resolve_models(run)
        except RuntimeError as exc:
            self._set_run(run_id, status="failed", error=str(exc))
            return

        try:
            await self._pipeline(run, lead, sub)
        except RunCancelled:
            self._set_run(run_id, status="cancelled")
            logger.info("research run %s: cancelled", run_id)
        except asyncio.CancelledError:
            raise  # shutdown; resume pass picks the run up next boot
        except Exception as exc:  # noqa: BLE001 — the run fails, the pool survives
            logger.exception("research run %s failed", run_id)
            self._set_run(run_id, status="failed", error=str(exc))

    async def _pipeline(
        self, run: dict[str, Any], lead: engine.ModelChoice, sub: engine.ModelChoice
    ) -> None:
        run_id = run["id"]
        steps = {
            s["kind"]: s for s in runstore.list_steps(run_id) if s["kind"] != "subagent"
        }

        # 1. plan --------------------------------------------------------------
        plan_step = steps.get("plan") or runstore.create_step(
            run_id, seq=0, kind="plan", name="Plan the run"
        )
        if plan_step["status"] != "done":
            self._set_run(run_id, status="planning")
            plan_output = await self._run_step(
                run_id,
                plan_step,
                lambda: engine.run_plan_step(runstore.get_run(run_id) or run, lead),
            )
            runstore.update_run(run_id, plan=plan_output)
        else:
            plan_output = plan_step["output"]
        plan = plan_output

        # 2. subagents ---------------------------------------------------------
        self._set_run(run_id, status="researching")
        existing = [s for s in runstore.list_steps(run_id) if s["kind"] == "subagent"]
        if not existing:
            existing = [
                runstore.create_step(
                    run_id,
                    seq=1 + i,
                    kind="subagent",
                    name=spec["name"],
                    input=spec,
                )
                for i, spec in enumerate(plan["subagents"])
            ]
            for step in existing:
                publish_step(step)

        parallelism = max(1, int(get_value("research.subagentParallelism", 2) or 2))
        semaphore = asyncio.Semaphore(parallelism)

        async def run_one(step: dict[str, Any]) -> None:
            if step["status"] in ("done", "skipped"):
                return
            if self._over_budget(run_id):
                runstore.finish_step(
                    step["id"], status="skipped", error="token budget exhausted"
                )
                self._publish_step(step["id"])
                return
            async with semaphore:
                try:
                    await self._run_step(
                        run_id,
                        step,
                        lambda: engine.run_subagent_step(
                            run,
                            step["input"],
                            sub,
                            is_cancelled=lambda: self._cancelled(run_id),
                        ),
                    )
                except RunCancelled:
                    raise
                except Exception:  # noqa: BLE001 — a failed subagent doesn't fail the run
                    logger.warning(
                        "research run %s: subagent %s failed permanently; "
                        "synthesis proceeds with the survivors",
                        run_id,
                        step["name"],
                    )

        results = await asyncio.gather(
            *(run_one(s) for s in existing), return_exceptions=True
        )
        for result in results:
            if isinstance(result, RunCancelled):
                raise result
            if isinstance(result, asyncio.CancelledError):
                raise result
        self._check_cancel(run_id)

        subagent_outputs = [
            s["output"]
            for s in runstore.list_steps(run_id)
            if s["kind"] == "subagent" and s["status"] == "done" and s["output"]
        ]
        if not subagent_outputs:
            raise RuntimeError("every subagent failed — nothing to synthesize")

        # 3. synthesis ---------------------------------------------------------
        steps = {
            s["kind"]: s for s in runstore.list_steps(run_id) if s["kind"] != "subagent"
        }
        synth_step = steps.get("synthesis") or runstore.create_step(
            run_id, seq=100, kind="synthesis", name="Synthesize the report"
        )
        if synth_step["status"] != "done":
            self._set_run(run_id, status="synthesizing")
            synth_output = await self._run_step(
                run_id,
                synth_step,
                lambda: engine.run_synthesis_step(
                    run, synth_step["id"], subagent_outputs, lead
                ),
            )
        else:
            synth_output = synth_step["output"]

        # 4. citations ---------------------------------------------------------
        steps = {
            s["kind"]: s for s in runstore.list_steps(run_id) if s["kind"] != "subagent"
        }
        cite_step = steps.get("citations") or runstore.create_step(
            run_id, seq=101, kind="citations", name="Verify citations"
        )
        if cite_step["status"] != "done":
            self._set_run(run_id, status="citing")
            cite_output = await self._run_step(
                run_id,
                cite_step,
                lambda: engine.run_citations_step(run, synth_output, lead),
            )
        else:
            cite_output = cite_step["output"]

        # 5. export ------------------------------------------------------------
        steps = {
            s["kind"]: s for s in runstore.list_steps(run_id) if s["kind"] != "subagent"
        }
        export_step = steps.get("export") or runstore.create_step(
            run_id, seq=102, kind="export", name="File the report"
        )
        if export_step["status"] != "done":
            self._set_run(run_id, status="exporting")
            export_output = await self._run_step(
                run_id,
                export_step,
                lambda: engine.run_export_step(run, cite_output),
            )
        else:
            export_output = export_step["output"]

        self._set_run(
            run_id,
            status="done",
            report_artifact_id=export_output["artifact_id"],
            report_source_id=export_output["source_id"],
            error=None,
        )
        logger.info("research run %s: done", run_id)

    # -- step machinery ------------------------------------------------------

    def _publish_step(self, step_id: str) -> None:
        step = runstore.get_step(step_id)
        if step is not None:
            publish_step(step)

    def _check_cancel(self, run_id: str) -> None:
        if self._cancelled(run_id):
            raise RunCancelled

    def _over_budget(self, run_id: str) -> bool:
        run = runstore.get_run(run_id)
        return bool(run and run["tokens_used"] >= run["token_budget"])

    async def _run_step(
        self, run_id: str, step: dict[str, Any], factory
    ) -> dict[str, Any]:
        """Drive one step to `done`, retrying with backoff; raises when the step
        exhausts its attempts (caller decides whether that fails the run)."""
        step_id = step["id"]
        while True:
            self._check_cancel(run_id)
            current = runstore.get_step(step_id)
            assert current is not None
            if current["status"] == "done":
                assert current["output"] is not None
                return current["output"]
            if current["attempt"] >= current["max_attempts"]:
                raise RuntimeError(
                    f"step {current['name']} failed after {current['attempt']} attempts: "
                    f"{current['error']}"
                )
            if current["attempt"] > 0:
                delay = min(5 * 2 ** (current["attempt"] - 1), _BACKOFF_CAP_S)
                await asyncio.sleep(delay)
                self._check_cancel(run_id)
            runstore.mark_step_running(step_id)
            self._publish_step(step_id)
            try:
                output, transcript, tokens = await factory()
            except RunCancelled:
                runstore.finish_step(step_id, status="pending")
                self._publish_step(step_id)
                raise
            except asyncio.CancelledError:
                # Shutdown mid-step: leave `running`; the resume pass resets it.
                raise
            except Exception as exc:  # noqa: BLE001 — recorded, retried, then surfaced
                logger.warning(
                    "research step %s attempt %d failed: %s",
                    step["name"],
                    (runstore.get_step(step_id) or {}).get("attempt", "?"),
                    exc,
                )
                runstore.finish_step(step_id, status="failed", error=str(exc))
                self._publish_step(step_id)
                continue
            runstore.finish_step(
                step_id,
                status="done",
                output=output,
                transcript=transcript,
                tokens_used=tokens,
            )
            runstore.add_run_tokens(run_id, tokens)
            self._publish_step(step_id)
            run = runstore.get_run(run_id)
            if run is not None:
                publish_run(run)
            return output


research_runner = ResearchRunner()

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
from backend.modules.research.broadcast import (
    publish_run,
    publish_step,
    publish_tool_call,
)
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

    def _linear_steps(self, run_id: str) -> dict[tuple[str, int], dict[str, Any]]:
        """The non-subagent steps, keyed by `(kind, round)`.

        Keyed by the pair rather than by kind alone: with gap-filling rounds there
        can be several `critique` steps, and a dict keyed by kind silently collapses
        them so round 2's critique reads as already done.
        """
        return {
            (s["kind"], s["round"]): s
            for s in runstore.list_steps(run_id)
            if s["kind"] != "subagent"
        }

    def _max_rounds(self, run: dict[str, Any], plan: dict[str, Any]) -> int:
        """How many research rounds this run may spend.

        The effort tier decides by default — a quick run that loops three times
        isn't quick — and the plan's own complexity wins over the requested effort,
        because the model may have judged the question simpler than it was asked to
        treat it.

        `research.maxRounds` is an **override, not a lid**: it defaults to 0 meaning
        "use the tier", so a user who wants every run capped at one round can say so
        without a non-zero default silently preventing deep runs from ever reaching
        their third round.
        """
        tier = plan.get("complexity") or run.get("effort") or "standard"
        ceiling = {"quick": 1, "standard": 2, "deep": 3}.get(tier, 2)
        override = int(get_value("research.maxRounds", 0) or 0)
        return max(1, min(override, ceiling)) if override > 0 else ceiling

    async def _pipeline(
        self, run: dict[str, Any], lead: engine.ModelChoice, sub: engine.ModelChoice
    ) -> None:
        run_id = run["id"]
        steps = self._linear_steps(run_id)

        # 1. plan --------------------------------------------------------------
        plan_step = steps.get(("plan", 0)) or runstore.create_step(
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

        # 1b. the approval gate -------------------------------------------------
        #
        # Parking here *returns* rather than blocking. Blocking would hold a worker
        # from a pool whose default size is 1, so one paused run would halt every
        # other run on the node — and it wouldn't survive a restart either. The run
        # comes back through `POST /runs/{id}/plan`, which re-enqueues it.
        # Approval is recorded by flipping `approval_mode` back to `auto`, so there
        # is exactly one gate per run and no separate "approved" flag to keep in
        # sync with it.
        fresh = runstore.get_run(run_id) or run
        if fresh.get("approval_mode") == "plan":
            if fresh["status"] != "awaiting_plan":
                self._set_run(run_id, status="awaiting_plan")
                logger.info("research run %s: awaiting plan approval", run_id)
            return

        # The edited plan, if the user changed one: the run row is the authority
        # after approval, and the step output was updated to match.
        plan = (runstore.get_run(run_id) or {}).get("plan") or plan_output

        # 2. research rounds ----------------------------------------------------
        parallelism = max(1, int(get_value("research.subagentParallelism", 2) or 2))
        semaphore = asyncio.Semaphore(parallelism)
        max_rounds = self._max_rounds(run, plan)

        # Derived from the DB, never from a loop counter: that's what makes a restart
        # mid-round resume into the right round instead of starting over.
        subagent_steps = [
            s for s in runstore.list_steps(run_id) if s["kind"] == "subagent"
        ]
        round_no = max((s["round"] for s in subagent_steps), default=0)

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
                            on_tool=self._tool_observer(run_id, step["id"]),
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

        while True:
            self._set_run(run_id, status="researching")

            wave = [
                s
                for s in runstore.list_steps(run_id)
                if s["kind"] == "subagent" and s["round"] == round_no
            ]
            if not wave:
                specs = (
                    plan["subagents"]
                    if round_no == 0
                    else self._pending_specs(run_id, round_no)
                )
                wave = [
                    runstore.create_step(
                        run_id,
                        seq=100 * round_no + 1 + i,
                        kind="subagent",
                        name=spec["name"],
                        input=spec,
                        round=round_no,
                    )
                    for i, spec in enumerate(specs)
                ]
                for step in wave:
                    publish_step(step)
            if not wave:
                break

            # A `while` rather than a single gather: a follow-up posted mid-wave
            # creates a subagent step in this round, and this picks it up in the
            # same wave instead of making the user wait for the next one.
            done_ids: set[str] = set()
            while True:
                todo = [s for s in wave if s["id"] not in done_ids]
                if not todo:
                    break
                results = await asyncio.gather(
                    *(run_one(s) for s in todo), return_exceptions=True
                )
                for result in results:
                    if isinstance(result, (RunCancelled, asyncio.CancelledError)):
                        raise result
                done_ids.update(s["id"] for s in todo)
                wave = [
                    s
                    for s in runstore.list_steps(run_id)
                    if s["kind"] == "subagent" and s["round"] == round_no
                ]
            self._check_cancel(run_id)

            outputs = self._round_outputs(run_id)
            if not outputs:
                raise RuntimeError("every subagent failed — nothing to synthesize")

            runstore.update_run(run_id, rounds_used=round_no + 1)
            if round_no + 1 >= max_rounds or self._over_budget(run_id):
                break

            # 2b. critique -----------------------------------------------------
            critique = await self._critique(run, plan, outputs, lead, round_no)
            if critique.get("sufficient") or not critique.get("subagents"):
                break
            round_no += 1

        subagent_outputs = self._round_outputs(run_id)
        if not subagent_outputs:
            raise RuntimeError("every subagent failed — nothing to synthesize")

        # 3. synthesis ---------------------------------------------------------
        steps = self._linear_steps(run_id)
        synth_step = steps.get(("synthesis", 0)) or runstore.create_step(
            run_id, seq=900, kind="synthesis", name="Synthesize the report"
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

        # 4. verification -------------------------------------------------------
        steps = self._linear_steps(run_id)
        verify_step = steps.get(("verify", 0)) or runstore.create_step(
            run_id, seq=901, kind="verify", name="Check claim support"
        )
        if verify_step["status"] != "done":
            self._set_run(run_id, status="verifying")
            try:
                verify_output = await self._run_step(
                    run_id,
                    verify_step,
                    lambda: engine.run_verification_step(
                        run, synth_output, subagent_outputs, lead
                    ),
                )
            except RunCancelled:
                raise
            except Exception as exc:  # noqa: BLE001 — the audit is additive
                logger.warning(
                    "research run %s: verification failed (%s); shipping the report "
                    "without an audit",
                    run_id,
                    exc,
                )
                verify_output = {}
        else:
            verify_output = verify_step["output"]

        # 5. citations ---------------------------------------------------------
        steps = self._linear_steps(run_id)
        cite_step = steps.get(("citations", 0)) or runstore.create_step(
            run_id, seq=902, kind="citations", name="Verify citations"
        )
        if cite_step["status"] != "done":
            self._set_run(run_id, status="citing")
            cite_output = await self._run_step(
                run_id,
                cite_step,
                lambda: engine.run_citations_step(
                    run, synth_output, lead, verify_output
                ),
            )
        else:
            cite_output = cite_step["output"]

        # 6. export ------------------------------------------------------------
        steps = self._linear_steps(run_id)
        export_step = steps.get(("export", 0)) or runstore.create_step(
            run_id, seq=903, kind="export", name="File the report"
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

    # -- rounds --------------------------------------------------------------

    def _round_outputs(self, run_id: str) -> list[dict[str, Any]]:
        """Every finished subagent's output, across all rounds.

        Cumulative on purpose: round 2 exists to *fill gaps* in round 1, not to
        replace it, so synthesis reads both.
        """
        return [
            s["output"]
            for s in runstore.list_steps(run_id)
            if s["kind"] == "subagent" and s["status"] == "done" and s["output"]
        ]

    def _pending_specs(self, run_id: str, round_no: int) -> list[dict[str, Any]]:
        """The subagent specs the previous round's critique asked for."""
        previous = self._linear_steps(run_id).get(("critique", round_no - 1))
        if previous is None or previous["status"] != "done" or not previous["output"]:
            return []
        return list(previous["output"].get("subagents") or [])

    async def _critique(
        self,
        run: dict[str, Any],
        plan: dict[str, Any],
        outputs: list[dict[str, Any]],
        lead: engine.ModelChoice,
        round_no: int,
    ) -> dict[str, Any]:
        """Close a round: what's still missing, and is another round worth it.

        A failed critique is treated as "sufficient" rather than failing the run —
        we already have findings, and throwing away a completed round because the
        reviewer couldn't produce JSON would be the worst possible trade. Same
        principle as a failed subagent not failing the run.
        """
        run_id = run["id"]
        step = self._linear_steps(run_id).get(("critique", round_no)) or (
            runstore.create_step(
                run_id,
                seq=100 * round_no + 90,
                kind="critique",
                name=f"Review round {round_no + 1}",
                round=round_no,
            )
        )
        if step["status"] == "done" and step["output"]:
            return step["output"]

        # Anything the user asked mid-run that no subagent picked up shapes the next
        # round instead of being silently dropped.
        pending = runstore.list_followups(run_id, unconsumed_only=True)
        try:
            critique = await self._run_step(
                run_id,
                step,
                lambda: engine.run_critique_step(
                    run,
                    plan,
                    outputs,
                    lead,
                    round_no=round_no,
                    followups=[f["text"] for f in pending],
                ),
            )
        except RunCancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "research run %s: critique failed (%s); treating the findings as "
                "sufficient and moving to synthesis",
                run_id,
                exc,
            )
            return {"sufficient": True, "gaps": [], "subagents": []}

        runstore.consume_followups([f["id"] for f in pending])
        return critique

    def _tool_observer(self, run_id: str, step_id: str):
        """A callback that turns each subagent tool call into a `/ws` event and a row.

        Persisted as well as broadcast: "what did subagent 3 actually search for" is
        the highest-value thing to know when a run comes back thin, and a browser
        that wasn't open when the run happened would otherwise have no way to find out.
        """

        def observe(payload: dict[str, Any]) -> None:
            runstore.record_tool_call(
                run_id,
                step_id,
                seq=int(payload.get("seq") or 0),
                name=str(payload.get("name") or "?"),
                args=payload.get("args"),
                ok=bool(payload.get("ok")),
                ms=payload.get("ms"),
                summary=str(payload.get("summary") or ""),
            )
            publish_tool_call(run_id, step_id, payload)

        return observe

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

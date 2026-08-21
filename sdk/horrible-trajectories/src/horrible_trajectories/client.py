"""Push agent trajectories into a horrible-dashboard node from anywhere.

The intended use is an agent living in someone else's codebase: import this, wrap
the run, and the dashboard gets a queryable trajectory without the agent having to
know anything about the dashboard.

    from horrible_trajectories import TrajectoryRecorder

    rec = TrajectoryRecorder(dataset="my-coding-agent")
    h = rec.harness(system_prompt=PROMPT, tools=TOOL_SCHEMAS, model="claude-opus-5")

    with rec.run(goal="fix the failing test", harness=h) as run:
        run.message("assistant", "I'll start by running the tests.")
        run.action("bash", {"cmd": "pytest"}, {"rc": 1}, ok=False, ms=1200)
        run.label("outcome", "success")

## Three properties this client has on purpose

**It never raises into your agent.** Every network failure is logged at `debug` and
dropped. A telemetry client that can crash the program it measures is worse than no
telemetry — the same rule the recorder inside the backend follows. If the dashboard
is down, your agent does not notice.

**It never blocks your loop.** Steps go onto a queue drained by a background
thread, batched by size and interval. `Run.__exit__` and `close()` flush
synchronously so a short-lived script does not exit with data still queued.

**It is idempotent.** Each run carries an `external_id`, so a retry replaces the
run rather than filing a second copy of it.

## Dependencies

`httpx` and the standard library, nothing else. This package is distributed on its
own (`pip install horrible-trajectories`) precisely so it can be added to a project
that has no idea what a horrible-dashboard is; a second dependency would turn
"just add the recorder" into a dependency negotiation.
"""

from __future__ import annotations

import atexit
import logging
import os
import queue
import threading
import time
import uuid
from types import TracebackType
from typing import Any

import httpx

logger = logging.getLogger("horrible.trajectories")

DEFAULT_BASE_URL = os.environ.get("HORRIBLE_URL", "http://127.0.0.1:8000")


class Run:
    """A single in-flight run. Use it as a context manager."""

    def __init__(self, recorder: "TrajectoryRecorder", payload: dict[str, Any]) -> None:
        self._recorder = recorder
        self._payload = payload
        self._steps: list[dict[str, Any]] = []
        self._labels: list[dict[str, Any]] = []
        self._sealed = False

    # -- steps ---------------------------------------------------------------

    def message(self, role: str, content: str, *, tokens: int | None = None) -> None:
        self._step(
            {"kind": "message", "role": role, "content": content, "tokens": tokens}
        )

    def thought(self, content: str) -> None:
        self._step({"kind": "thought", "content": content})

    def action(
        self,
        name: str,
        args: Any = None,
        result: Any = None,
        *,
        ok: bool = True,
        ms: int | None = None,
        error: str | None = None,
    ) -> None:
        """One tool call **and its result** — a single step, never two.

        Passing the result separately from the call is the one thing that makes a
        trajectory analysable: the pairing is unambiguous even when the same tool
        is called twice in a row.
        """
        self._step(
            {
                "kind": "action",
                "name": name,
                "args": args,
                "result": result,
                "ok": ok,
                "duration_ms": ms,
                "error": error,
            }
        )

    def observation(self, value: Any) -> None:
        """State the agent was handed without asking — an environment tick."""
        self._step({"kind": "observation", "result": value})

    def reward(self, value: float) -> None:
        self._step({"kind": "reward", "result": value})

    def _step(self, step: dict[str, Any]) -> None:
        if self._sealed:
            logger.debug("trajectories: step after the run was sealed; dropped")
            return
        step.setdefault("ts", time.time())
        step["seq"] = len(self._steps)
        self._steps.append(step)

    # -- grading -------------------------------------------------------------

    def label(
        self,
        key: str,
        value: str = "",
        *,
        score: float | None = None,
        source: str = "downstream",
        rationale: str = "",
    ) -> None:
        """Attach a judgment. `label("outcome", "success")` is the common one."""
        self._labels.append(
            {
                "key": key,
                "value": value,
                "score": score,
                "source": source,
                "rationale": rationale,
            }
        )

    # -- lifecycle -----------------------------------------------------------

    def finish(
        self, *, status: str = "complete", outcome: str | None = None, error: str = ""
    ) -> None:
        if self._sealed:
            return
        self._sealed = True
        self._payload.update(
            {
                "status": status,
                "error": error,
                "finished_at": time.time(),
                "step_list": self._steps,
                "labels": self._labels,
            }
        )
        if outcome is not None:
            self._payload["outcome"] = outcome
        elif any(lbl["key"] == "outcome" for lbl in self._labels):
            self._payload["outcome"] = next(
                lbl["value"] for lbl in self._labels if lbl["key"] == "outcome"
            )
        self._recorder._enqueue(self._payload)

    def __enter__(self) -> "Run":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        # An exception escaping the block is the run's outcome, not a reason to
        # lose it — a crashed run is the most interesting kind.
        if exc is not None:
            self.finish(status="failed", error=f"{type(exc).__name__}: {exc}")
        else:
            self.finish()
        self._recorder.flush()
        return False


class TrajectoryRecorder:
    """Client handle on a node's trajectory store."""

    def __init__(
        self,
        dataset: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        batch_size: int = 10,
        flush_interval_sec: float = 2.0,
        timeout: float = 10.0,
    ) -> None:
        self.dataset = dataset
        self.base_url = base_url.rstrip("/")
        self.batch_size = batch_size
        self.flush_interval_sec = flush_interval_sec

        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=10000)
        self._client = httpx.Client(timeout=timeout)
        self._stop = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()
        atexit.register(self.close)

    # -- authoring -----------------------------------------------------------

    @staticmethod
    def harness(
        *,
        system_prompt: str = "",
        tools: Any = None,
        model: str = "",
        provider: str = "",
        agent_id: str = "",
        params: dict[str, Any] | None = None,
        label: str = "",
    ) -> dict[str, Any]:
        """Describe the configuration a run executed under.

        `tools` may be a list of schemas or a `{name: schema}` mapping; both are
        normalised here so the fingerprint does not depend on which one you had.
        The fingerprint itself is computed **server-side** — two clients hashing
        slightly differently would split one harness into two and quietly make
        every comparison across them empty.
        """
        schemas: dict[str, Any] = {}
        if isinstance(tools, dict):
            schemas = dict(tools)
        elif isinstance(tools, list):
            for index, tool in enumerate(tools):
                name = None
                if isinstance(tool, dict):
                    name = tool.get("name") or (tool.get("function") or {}).get("name")
                schemas[str(name or index)] = tool
        return {
            "agent_id": agent_id,
            "model": model,
            "provider": provider,
            "system_prompt": system_prompt,
            "tool_names": sorted(schemas),
            "tool_schemas": schemas,
            "params": params or {},
            "label": label,
        }

    def run(
        self,
        goal: str,
        *,
        harness: dict[str, Any] | None = None,
        external_id: str | None = None,
        agent_id: str = "",
        agent_name: str = "",
        model: str = "",
        provider: str = "",
        meta: dict[str, Any] | None = None,
    ) -> Run:
        payload: dict[str, Any] = {
            "dataset_id": self.dataset,
            "source": "external",
            "external_id": external_id or uuid.uuid4().hex,
            "goal": goal,
            "agent_id": agent_id or (harness or {}).get("agent_id", ""),
            "agent_name": agent_name,
            "model": model or (harness or {}).get("model", ""),
            "provider": provider or (harness or {}).get("provider", ""),
            "started_at": time.time(),
            "meta": meta or {},
        }
        if harness:
            payload["harness"] = harness
        return Run(self, payload)

    # -- transport -----------------------------------------------------------

    def _enqueue(self, payload: dict[str, Any]) -> None:
        try:
            self._idle.clear()
            self._queue.put_nowait(payload)
        except queue.Full:
            # Dropping the oldest work would be worse: this at least fails at a
            # known point rather than silently reordering a dataset.
            logger.debug("trajectories: queue full, run dropped")

    def _worker_loop(self) -> None:
        batch: list[dict[str, Any]] = []
        last = time.time()
        while not self._stop.is_set():
            try:
                timeout = max(0.1, self.flush_interval_sec - (time.time() - last))
                item = self._queue.get(timeout=timeout)
                if item is None:
                    break
                batch.append(item)
                if len(batch) >= self.batch_size:
                    self._send(batch)
                    batch, last = [], time.time()
            except queue.Empty:
                if batch:
                    self._send(batch)
                    batch = []
                last = time.time()
                self._idle.set()
        if batch:
            self._send(batch)
        self._idle.set()

    def _send(self, batch: list[dict[str, Any]]) -> None:
        try:
            response = self._client.post(
                f"{self.base_url}/api/trajectories/ingest", json={"runs": batch}
            )
            if response.status_code >= 400:
                logger.debug("trajectories: ingest rejected: %s", response.text[:400])
        except Exception as exc:
            # The whole contract of this client: the dashboard being unreachable
            # is not your agent's problem.
            logger.debug("trajectories: ingest failed: %s", exc)

    def flush(self, timeout: float = 5.0) -> None:
        """Block until the queue has drained, or `timeout` elapses."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._queue.empty() and self._idle.is_set():
                return
            time.sleep(0.05)

    def close(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:  # pragma: no cover
            pass
        self._worker.join(timeout=5.0)
        self._client.close()

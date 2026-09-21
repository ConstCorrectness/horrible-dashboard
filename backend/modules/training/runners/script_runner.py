"""Long-running training scripts, outside the kernel.

`python <script>` runs in the **project venv** with the project root as cwd —
the venv has `horrible-train`, so `ht.log()`/`ht.frame()` in the script flow
through the same sentinel pipeline as notebook cells (indistinguishable to the
panes). Spawn is blocking `Popen` pumped on a daemon thread (Windows-safe,
LSP pattern); plain output lines stream to the UI as `run_output` events.

Driven by the `recipe.run` / `training.start_run` / `training.stop_run` agent
tools and the REST endpoints; runs are project-scoped and survive pane/tab closes.

**Every run also writes its output to a file.** Until it did, that output existed
only as `run_output` broadcasts: whoever had a pane open saw it and nobody else
ever could. So a headless run — the only kind an agent can start — was observable
as `state: running` and then, at best, `returncode: 1`. A crash was a number. And
because the registry lives in memory, a backend restart (a `--reload` on any file
under `backend/`, tests included) erased even that: the run list came back
**empty**, which is indistinguishable from a run that finished cleanly. The log
file is on disk under the project, so it outlives both the pane and the backend.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any

from backend.modules.training.envs import python_path, venv_ready
from backend.modules.training.metrics import finish_run, record_event
from backend.modules.training.models import ProjectModel
from backend.modules.training.sentinel import EVENT_NAMES, LineSplitter
from backend.modules.training.stream import broadcast_threadsafe

logger = logging.getLogger(__name__)

#: Where run logs land, under the project root so they travel with the project.
LOG_DIR = "runs/logs"

#: Lines kept in memory per run. The file keeps all of them.
TAIL_LINES = 200

#: How often the pump re-reads the log while the run is alive. Short enough that
#: a progress bar looks live, long enough that an idle run costs nothing.
POLL_INTERVAL_S = 0.2

#: A line terminator as the console produces one. A carriage return counts,
#: because that is how tqdm redraws in place — a progress bar that only ends
#: at a newline is one line several megabytes long.
_NEWLINE = re.compile("\r\n|\r|\n")


def _detached() -> dict[str, Any]:
    """Popen kwargs that put the child in its own process group / session.

    Per-platform because the spelling differs and neither name exists on the
    other: `CREATE_NEW_PROCESS_GROUP` is a Windows creation flag, `start_new_session`
    is a POSIX `setsid`. Both mean the same thing here — a Ctrl-C or a group
    signal sent to the backend stops at the backend.
    """
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


class ScriptRun:
    def __init__(self, run_id: str, project: ProjectModel, script: str) -> None:
        self.id = run_id
        self.project = project
        self.script = script
        self.proc: subprocess.Popen[str] | None = None
        self.returncode: int | None = None
        self.stopped = False
        #: Every metric run id this process opened. A script may call `ht.run()`
        #: several times (a sweep child does not, but a hand-written loop might),
        #: and each one needs closing when the process exits.
        self.metric_runs: list[str] = []
        #: Where this run's output is written. Under the project, so it outlives
        #: both the pane that started it and the backend process.
        self.log_path: Path = (
            Path(project.root) / LOG_DIR / f"{Path(script).stem}-{run_id}.log"
        )
        #: The last few lines, in memory, so a status call answers without a read.
        #: Bounded on purpose: a training run prints a progress line per step and
        #: what a caller wants is the traceback at the end, not the history.
        self.tail: deque[str] = deque(maxlen=TAIL_LINES)
        #: The child's stdout, held open for its lifetime and closed with it.
        self.log_file: Any = None
        #: A trailing line the last read caught mid-write.
        self.partial: str = ""

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None


class ScriptRunner:
    """Process-global registry of script runs."""

    def __init__(self) -> None:
        self.runs: dict[str, ScriptRun] = {}

    def start(self, project: ProjectModel, script: str) -> ScriptRun:
        if not venv_ready(project):
            raise ValueError("project venv is not ready")
        root = Path(project.root).resolve()
        target = (root / script).resolve()
        if not target.is_relative_to(root):
            raise ValueError(f"script escapes project root: {script}")
        if not target.is_file():
            raise ValueError(f"no such script: {script}")
        run = ScriptRun(uuid.uuid4().hex[:8], project, script)
        run.log_path.parent.mkdir(parents=True, exist_ok=True)
        # Held open for the child's lifetime and inherited by it. Line-buffered
        # is not available for a binary handle and `-u` already makes the child
        # unbuffered, which is what the reader needs.
        run.log_file = run.log_path.open("wb")
        run.proc = subprocess.Popen(
            [str(python_path(project)), "-u", str(target)],
            cwd=str(root),
            # Same contract as the kernel: tracker credentials arrive through the
            # environment at spawn, for the trackers this project's recipe asked
            # for, and are never written anywhere the browser can read.
            #
            # `PYTHONIOENCODING` because stdout is a **file** now, not a pipe:
            # Python picks the locale encoding for a file, which on Windows is
            # cp1252, and a training script prints tracebacks and dataset text
            # that are not in it. `errors=replace` so an unencodable character
            # is a `?` rather than a `UnicodeEncodeError` that ends the run.
            env={
                **os.environ,
                "PYTHONIOENCODING": "utf-8:replace",
                **_otlp_env(project),
                **_tracker_env(project),
            },
            # Straight to the log file, not through a pipe this backend owns —
            # see `_pump`. A pipe makes the run's survival depend on ours.
            stdout=run.log_file,
            stderr=subprocess.STDOUT,
            # Its own process group / session, so a console signal aimed at the
            # backend does not also land here. A fine-tune is the longest-lived
            # thing this app starts — hours — and it was dying of Ctrl-C it was
            # never sent: `uvicorn --reload` restarts its worker by signalling
            # the group, and the Tauri supervisor restarts the backend the same
            # way, so the run ended mid-step with a bare `KeyboardInterrupt` in
            # the traceback and no explanation anywhere. `stop()` is unaffected —
            # it kills by handle, not by signal.
            **_detached(),
        )
        self.runs[run.id] = run
        threading.Thread(
            target=self._pump, args=(run,), daemon=True, name=f"run-{run.id}"
        ).start()
        broadcast_threadsafe(
            "run_state",
            {
                "runId": run.id,
                "projectId": project.id,
                "script": script,
                "state": "running",
            },
        )
        return run

    def stop(self, run_id: str) -> bool:
        run = self.runs.get(run_id)
        if run is None or run.proc is None:
            return False
        if run.running:
            run.stopped = True
            run.proc.kill()
        return True

    def _pump(self, run: ScriptRun) -> None:
        """Follow the run's log file and turn it into events.

        **The child writes to the file itself; this reads it.** It used to be the
        other way round — the child wrote into a pipe, this thread read the pipe
        and appended each line to the file — and that made the log a thing the
        backend produced rather than a thing the run left behind. The two
        properties a long fine-tune needs are incompatible with it:

        - The run has to outlive the backend (a `--reload`, a crash, a desktop
          update restarting its supervisor). Detaching the process group achieved
          that, and then the run died anyway: the read end of its pipe went with
          the old process, and the next `print` was a broken pipe.
        - And the output has to survive too, which it cannot if it is buffered in
          a pipe that nobody is left to drain.

        Writing to the file directly gives both, and makes this thread
        *resumable* rather than load-bearing: it is one reader of a file that is
        the truth, so losing it loses live streaming and nothing else.
        """
        assert run.proc is not None
        splitter = LineSplitter()
        try:
            with run.log_path.open("r", encoding="utf-8", errors="replace") as fh:
                while True:
                    chunk = fh.read()
                    if chunk:
                        self._consume(run, splitter, chunk)
                        continue
                    if not run.running:
                        # One last read after exit: the child may have flushed
                        # between the empty read and the poll above.
                        self._consume(run, splitter, fh.read())
                        break
                    time.sleep(POLL_INTERVAL_S)
        except Exception:  # noqa: BLE001 — pump must not die silently
            logger.exception("script run pump failed (%s)", run.id)
        finally:
            tail = splitter.flush()
            if tail:
                self._consume(run, splitter, tail)
            self._flush_partial(run)
            run.returncode = run.proc.wait()
            if run.log_file is not None:
                run.log_file.close()
                run.log_file = None
            # Close every metric run this process opened. `finish_run` is
            # idempotent, so a script that already called `ht.finish()` keeps the
            # status it chose and this is a no-op — which is the point: the
            # process's exit code is the *fallback* verdict, not the authority.
            # A killed run is `failed` rather than `crashed`: the user stopped it.
            status = (
                "finished"
                if run.returncode == 0
                else "failed"
                if run.stopped
                else "crashed"
            )
            for metric_run in run.metric_runs:
                finish_run(metric_run, status)
            broadcast_threadsafe(
                "run_state",
                {
                    "runId": run.id,
                    "projectId": run.project.id,
                    "script": run.script,
                    "state": "exited",
                    "returncode": run.returncode,
                    "status": status,
                },
            )

    def _consume(self, run: ScriptRun, splitter: LineSplitter, chunk: str) -> None:
        """Route one chunk of the log: sentinel events to metrics, text to panes."""
        if not chunk:
            return
        text, events = splitter.feed(chunk)
        for event in events:
            ws_event = EVENT_NAMES.get(str(event.get("type", "")))
            if ws_event is None:
                continue
            data = {k: v for k, v in event.items() if k != "type"}
            data["projectId"] = run.project.id
            data.setdefault("runId", run.id)
            metric_run = str(data.get("runId") or "")
            if metric_run and metric_run not in run.metric_runs:
                run.metric_runs.append(metric_run)
            record_event(ws_event, data)
        if text:
            # Split here, because a file read is not a pipe iteration. The old
            # pump got one line per loop for free; a read returns whatever has
            # been flushed, so the whole of a run could arrive as one "line" —
            # one giant `run_output` event and a tail buffer holding a single
            # entry. `\r` counts as a terminator too: that is how tqdm redraws a
            # progress bar, and text-mode universal newlines used to split on it.
            run.partial = run.partial + text
            pieces = _NEWLINE.split(run.partial)
            run.partial = pieces.pop()
            for line in pieces:
                self._record(run, line)

    def _flush_partial(self, run: ScriptRun) -> None:
        """Emit a last line that never got its terminator (a crash mid-print)."""
        if run.partial:
            self._record(run, run.partial)
            run.partial = ""

    def _record(self, run: ScriptRun, line: str) -> None:
        """One output line, to the pane and to the in-memory tail.

        It is no longer written anywhere: the child writes the file, and this
        line came *from* that file. Writing it back would be the log duplicating
        itself, which is how a "why is every progress bar here twice" hunt starts.
        """
        run.tail.append(line)
        broadcast_threadsafe(
            "run_output",
            {"runId": run.id, "projectId": run.project.id, "line": line},
        )

    def status(self, *, tail: int = 0) -> list[dict[str, Any]]:
        return [
            {
                "runId": r.id,
                "projectId": r.project.id,
                "script": r.script,
                "state": "running" if r.running else "exited",
                "returncode": r.returncode,
                # Relative: the absolute path is the backend's business and the
                # project root is what the caller already has.
                "log": r.log_path.relative_to(Path(r.project.root)).as_posix(),
                **({"tail": list(r.tail)[-tail:]} if tail else {}),
            }
            for r in self.runs.values()
        ]


script_runner = ScriptRunner()


def _otlp_env(project: ProjectModel) -> dict[str, str]:
    from backend.modules.training.kernels import _otlp_env as kernel_otlp_env

    return kernel_otlp_env(project)


def _tracker_env(project: ProjectModel) -> dict[str, str]:
    """Tracker credentials for this project's recipe. See `training/trackers.py`."""
    from backend.modules.training import recipes, trackers

    try:
        return trackers.env_for(recipes.load_recipe(project).trackers)
    except Exception as exc:  # noqa: BLE001 — a missing credential must not block a run
        logger.info("training: no tracker env for %s (%s)", project.id, exc)
        return {}

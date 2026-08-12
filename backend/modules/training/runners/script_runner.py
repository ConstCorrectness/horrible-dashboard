"""Long-running training scripts, outside the kernel.

`python <script>` runs in the **project venv** with the project root as cwd —
the venv has `horrible-train`, so `ht.log()`/`ht.frame()` in the script flow
through the same sentinel pipeline as notebook cells (indistinguishable to the
panes). Spawn is blocking `Popen` pumped on a daemon thread (Windows-safe,
LSP pattern); plain output lines stream to the UI as `run_output` events.

Driven by the `training.start_run` / `training.stop_run` agent tools and the
REST endpoints; runs are project-scoped and survive pane/tab closes.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any

from backend.modules.training.envs import python_path, venv_ready
from backend.modules.training.metrics import record_event
from backend.modules.training.models import ProjectModel
from backend.modules.training.sentinel import EVENT_NAMES, LineSplitter
from backend.modules.training.stream import broadcast_threadsafe

logger = logging.getLogger(__name__)


class ScriptRun:
    def __init__(self, run_id: str, project: ProjectModel, script: str) -> None:
        self.id = run_id
        self.project = project
        self.script = script
        self.proc: subprocess.Popen[str] | None = None
        self.returncode: int | None = None

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
        run.proc = subprocess.Popen(
            [str(python_path(project)), "-u", str(target)],
            cwd=str(root),
            # Same contract as the kernel: tracker credentials arrive through the
            # environment at spawn, for the trackers this project's recipe asked
            # for, and are never written anywhere the browser can read.
            env={**os.environ, **_tracker_env(project)},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
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
            run.proc.kill()
        return True

    def _pump(self, run: ScriptRun) -> None:
        assert run.proc is not None and run.proc.stdout is not None
        splitter = LineSplitter()
        try:
            for chunk in run.proc.stdout:
                text, events = splitter.feed(chunk)
                for event in events:
                    ws_event = EVENT_NAMES.get(str(event.get("type", "")))
                    if ws_event is None:
                        continue
                    data = {k: v for k, v in event.items() if k != "type"}
                    data["projectId"] = run.project.id
                    data.setdefault("runId", run.id)
                    record_event(ws_event, data)
                if text:
                    broadcast_threadsafe(
                        "run_output",
                        {
                            "runId": run.id,
                            "projectId": run.project.id,
                            "line": text.rstrip("\n"),
                        },
                    )
        except Exception:  # noqa: BLE001 — pump must not die silently
            logger.exception("script run pump failed (%s)", run.id)
        finally:
            tail = splitter.flush()
            if tail:
                broadcast_threadsafe(
                    "run_output",
                    {"runId": run.id, "projectId": run.project.id, "line": tail},
                )
            run.returncode = run.proc.wait()
            broadcast_threadsafe(
                "run_state",
                {
                    "runId": run.id,
                    "projectId": run.project.id,
                    "script": run.script,
                    "state": "exited",
                    "returncode": run.returncode,
                },
            )

    def status(self) -> list[dict[str, Any]]:
        return [
            {
                "runId": r.id,
                "projectId": r.project.id,
                "script": r.script,
                "state": "running" if r.running else "exited",
                "returncode": r.returncode,
            }
            for r in self.runs.values()
        ]


script_runner = ScriptRunner()


def _tracker_env(project: ProjectModel) -> dict[str, str]:
    """Tracker credentials for this project's recipe. See `training/trackers.py`."""
    from backend.modules.training import recipes, trackers

    try:
        return trackers.env_for(recipes.load_recipe(project).trackers)
    except Exception as exc:  # noqa: BLE001 — a missing credential must not block a run
        logger.info("training: no tracker env for %s (%s)", project.id, exc)
        return {}

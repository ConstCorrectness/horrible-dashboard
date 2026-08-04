"""Training kernel sessions — a thin, domain-specific subclass of the shared
`notebook_core` engine, over the `training` ws channel.

The neutral kernel machinery (spawn/exec/iopub/save on daemon threads, Windows/
uvicorn-`--reload` safe) lives in `backend/notebook_core/`. This module adds only
what's training-specific: sessions are keyed `{projectId}:{path}`, spawned from the
**project venv's** python, and their stdout `@@HORRIBLE@@` sentinel lines are
stripped and re-emitted app-wide as metrics/frames/model-graph events.

**Windows interrupt caveat** (unchanged): interrupts raise KeyboardInterrupt
*between* Python statements — training loops stop in milliseconds; one long
blocking C call defers the interrupt until it returns. Restart is the hard stop.

Channel protocol additions on top of the core's kernel slice:

| Direction     | event      | data                                     |
| ------------- | ---------- | ---------------------------------------- |
| client→server | `open`     | `{projectId, notebook?}`                 |
| client→server | `watch_run`| `{runId}`                                |
| server→client | `opened`   | `{sessionKey, projectId, notebook, kernel}` |

Sentinel events inside stream output are stripped and re-emitted app-wide
(`metrics`, `frame`, `model_graph`, …) via the training stream fanout.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.modules.training import notebooks, projects
from backend.modules.training.envs import python_path, venv_ready
from backend.modules.training.sentinel import EVENT_NAMES, LineSplitter
from backend.notebook_core import KernelSession, KernelSessionManager, SessionConfig
from backend.notebook_core.detach import run_detached

logger = logging.getLogger(__name__)


class UnknownProjectError(ValueError):
    """The requested project no longer exists (e.g. a persisted pane referencing a
    deleted/corrupt project). Carries an `unknown_project` code to the pane so it
    can self-heal instead of showing a dead-end error."""


class TrainingKernelSession(KernelSession):
    """A kernel session whose stdout is scanned for the training sentinel."""

    def __init__(
        self, config: SessionConfig, loop: asyncio.AbstractEventLoop, project: Any
    ) -> None:
        super().__init__(config, loop)
        self.project = project
        self._splitters: dict[tuple[str, str], LineSplitter] = {}

    def _on_stream(self, cell_id: str, content: dict[str, Any]) -> None:
        name = content.get("name", "stdout")
        splitter = self._splitters.setdefault((cell_id, name), LineSplitter())
        text, events = splitter.feed(content.get("text", ""))
        for event in events:
            self._fan_sentinel(event)
        if text:
            self._append_output(
                cell_id, {"output_type": "stream", "name": name, "text": text}
            )

    def _fan_sentinel(self, event: dict[str, Any]) -> None:
        """Re-emit a helper event app-wide (metrics pane, graph pane, …)."""
        ws_event = EVENT_NAMES.get(str(event.get("type", "")))
        if ws_event is None:
            return
        data = {k: v for k, v in event.items() if k != "type"}
        data["projectId"] = self.project.id
        from backend.modules.training.metrics import record_event

        record_event(ws_event, data)


class TrainingKernelManager(KernelSessionManager):
    """Process-global registry of training kernel sessions (survives pane close)."""

    channel = "training"
    SessionCls = TrainingKernelSession

    async def _handle_extra(self, conn: Any, event: str, data: dict[str, Any]) -> bool:
        if event == "watch_run":
            from backend.modules.training.metrics import backfill, known_runs

            run_id = str(data.get("runId", ""))
            await conn.send_json(
                self._evt(
                    "run_backfill",
                    {
                        "runId": run_id,
                        "points": backfill(run_id),
                        "runs": known_runs(),
                    },
                )
            )
            return True
        return False

    async def _open(self, conn: Any, data: dict[str, Any]) -> None:
        project_id = str(data.get("projectId", ""))
        nb_rel = str(data.get("notebook") or projects.DEFAULT_NOTEBOOK)
        key = f"{project_id}:{nb_rel}"
        try:
            async with self._open_lock:
                session = self.sessions.get(key)
                if session is None:
                    session = await run_detached(self._create, project_id, nb_rel, key)
                    self.sessions[key] = session
        except UnknownProjectError as exc:
            logger.warning("kernel open failed for %s: %s", key, exc)
            await conn.send_json(
                self._evt(
                    "error",
                    {"sessionKey": key, "message": str(exc), "code": "unknown_project"},
                )
            )
            return
        except Exception as exc:  # noqa: BLE001 — surfaced to the pane
            logger.exception("kernel open failed for %s", key)
            await conn.send_json(
                self._evt("error", {"sessionKey": key, "message": str(exc)})
            )
            return
        session.subscribers.add(conn)
        await conn.send_json(
            self._evt(
                "opened",
                {
                    "sessionKey": key,
                    "projectId": project_id,
                    "notebook": session.notebook_model(),
                    "kernel": session.status,
                },
            )
        )

    def _create(self, project_id: str, nb_rel: str, key: str) -> TrainingKernelSession:
        project = projects.get_project(project_id)
        if project is None:
            raise UnknownProjectError(f"unknown project: {project_id}")
        if not venv_ready(project):
            raise ValueError("project venv is not ready yet — wait for the bootstrap")
        config = SessionConfig(
            key=key,
            python_executable=str(python_path(project)),
            cwd=project.root,
            notebook_abs_path=notebooks.notebook_path(project, nb_rel),
            rel_path=nb_rel,
            channel="training",
            display_name=f"training:{project.id}",
        )
        session = TrainingKernelSession(config, self._loop(), project)
        session.start()
        return session

    def _loop(self) -> asyncio.AbstractEventLoop:
        # The loop captured when the first `/ws` connection subscribed for training
        # broadcasts — the same loop the sentinel metrics fanout runs on.
        from backend.modules.training import stream

        loop = stream._loop
        if loop is None or loop.is_closed():
            raise RuntimeError("no event loop available for kernel fanout")
        return loop

    def session_for(self, project_id: str, nb_rel: str) -> TrainingKernelSession | None:  # type: ignore[override]
        session = self.sessions.get(f"{project_id}:{nb_rel}")
        return session  # type: ignore[return-value]


training_kernels = TrainingKernelManager()


async def handle_training_message(conn: Any, msg: dict[str, Any]) -> None:
    """`training` channel entry point (kernel events; more slices ride along)."""
    await training_kernels.handle(conn, msg)

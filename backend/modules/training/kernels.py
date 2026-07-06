"""Jupyter kernel sessions for training notebooks, over the `training` ws channel.

One `KernelSession` per open notebook (`{projectId}:{path}`), spawned from the
**project venv's** python via `jupyter_client`'s *sync* API on daemon threads —
its `KernelProvisionerBase`/`Popen` spawn path is event-loop-agnostic, which is
what makes this work under uvicorn `--reload` on Windows (see lsp/manager.py for
the long version). The manager is **process-global**: kernels keep training when
the pane (or the whole tab) closes; a socket close only unsubscribes.

The in-memory nbformat `doc` is authoritative: UI/agent cell edits mutate it,
iopub outputs append to it, and it's flushed to disk atomically (debounced 2 s,
and after every completed execution), so the `.ipynb` stays truthful.

**Windows interrupt caveat**: interrupts ride the Win32 interrupt event →
`interrupt_main()`, which raises KeyboardInterrupt *between* Python statements.
Training loops stop in milliseconds; one long blocking C call (a single
`time.sleep(600)`, a giant blocking native op) defers the interrupt until that
call returns. Restart is the hard stop.

Channel protocol (`{channel:'training', event, data}`), kernel slice:

| Direction     | event               | data                                          |
| ------------- | ------------------- | --------------------------------------------- |
| client→server | `open`              | `{projectId, notebook?}`                      |
| client→server | `run_cell`/`run_all`| `{sessionKey, cellId}` / `{sessionKey}`       |
| client→server | `cells`             | `{sessionKey, ops: [{op, ...}]}`              |
| client→server | `interrupt`/`restart`/`shutdown` | `{sessionKey}`                   |
| server→client | `opened`            | `{sessionKey, projectId, notebook, kernel}`   |
| server→client | `kernel_status`     | `{sessionKey, status}`                        |
| server→client | `execution_state`   | `{sessionKey, cellId, state, execCount?}`     |
| server→client | `output`            | `{sessionKey, cellId, output}`                |
| server→client | `cells_changed`     | `{sessionKey, notebook}`                      |
| server→client | `error`             | `{sessionKey?, message}`                      |

Sentinel events inside stream output are stripped and re-emitted app-wide
(`metrics`, `frame`, `model_graph`, …) via the training stream fanout.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
from typing import Any

from backend.modules.training import notebooks, projects
from backend.modules.training.envs import python_path, venv_ready
from backend.modules.training.models import ProjectModel
from backend.modules.training.sentinel import EVENT_NAMES, LineSplitter

logger = logging.getLogger(__name__)

SAVE_DEBOUNCE_S = 2.0
START_TIMEOUT_S = 60.0
_STOP = object()  # worker-queue poison pill


class UnknownProjectError(ValueError):
    """The requested project no longer exists (e.g. a persisted pane referencing a
    deleted/corrupt project). Carries an `unknown_project` code to the pane so it
    can self-heal instead of showing a dead-end error."""


def _evt(event: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"channel": "training", "event": event, "data": data}


def _make_kernel_manager(project: ProjectModel):
    """A jupyter_client KernelManager pinned to the project venv's python, with an
    in-memory kernelspec (no kernelspec files, no ipykernel in the backend env;
    `interrupt_mode='signal'` so the provisioner wires the Windows interrupt
    event / POSIX SIGINT for us)."""
    from jupyter_client.kernelspec import KernelSpec
    from jupyter_client.manager import KernelManager

    spec = KernelSpec(
        argv=[
            str(python_path(project)),
            "-m",
            "ipykernel_launcher",
            "-f",
            "{connection_file}",
        ],
        display_name=f"training:{project.id}",
        language="python",
        interrupt_mode="signal",
    )

    class _VenvKernelManager(KernelManager):
        @property
        def kernel_spec(self) -> KernelSpec:  # type: ignore[override]
            return spec

    return _VenvKernelManager()


class KernelSession:
    """One kernel + one notebook doc + the browser tabs watching them."""

    def __init__(
        self,
        key: str,
        project: ProjectModel,
        nb_rel: str,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self.key = key
        self.project = project
        self.nb_rel = nb_rel
        self.loop = loop
        self.nb_path = notebooks.notebook_path(project, nb_rel)
        self.doc = notebooks.load(self.nb_path)
        self.doc_lock = threading.RLock()
        self.subscribers: set[Any] = set()
        self.status = "starting"
        self.closing = False
        self.km: Any = None
        self.kc: Any = None
        self.exec_queue: queue.Queue[Any] = queue.Queue()
        self.msg_to_cell: dict[str, str] = {}
        self._save_timer: threading.Timer | None = None
        self._splitters: dict[tuple[str, str], LineSplitter] = {}
        self._worker: threading.Thread | None = None
        self._iopub: threading.Thread | None = None

    # --- lifecycle (all blocking work runs on threads) ----------------------

    def start(self) -> None:
        """Spawn the kernel and the pump threads. Blocking — call off-loop."""
        self.km = _make_kernel_manager(self.project)
        self.km.start_kernel(cwd=self.project.root)
        self.kc = self.km.client()
        self.kc.start_channels()
        try:
            self.kc.wait_for_ready(timeout=START_TIMEOUT_S)
        except RuntimeError:
            self.shutdown()
            raise
        self._set_status("idle")
        self._worker = threading.Thread(
            target=self._worker_loop, daemon=True, name=f"kernel-exec-{self.key}"
        )
        self._iopub = threading.Thread(
            target=self._iopub_loop, daemon=True, name=f"kernel-iopub-{self.key}"
        )
        self._worker.start()
        self._iopub.start()

    def interrupt(self) -> None:
        if self.km is not None and self.km.is_alive():
            self.km.interrupt_kernel()

    def restart(self) -> None:
        if self.km is None:
            return
        self._set_status("restarting")
        # Drop anything still queued; the kernel state it assumed is gone.
        self._drain_queue()
        if self.kc is not None:
            try:
                self.kc.stop_channels()
            except Exception:
                pass
        self.km.restart_kernel(now=True)
        self.kc = self.km.client()
        self.kc.start_channels()
        self.kc.wait_for_ready(timeout=START_TIMEOUT_S)
        self._set_status("idle")

    def shutdown(self) -> None:
        self.closing = True
        self._drain_queue()
        self.exec_queue.put(_STOP)
        if self._save_timer is not None:
            self._save_timer.cancel()
        try:
            if self.kc is not None:
                self.kc.stop_channels()
            if self.km is not None and self.km.has_kernel:
                self.km.shutdown_kernel(now=True)
        except Exception:  # noqa: BLE001 — teardown must not raise
            logger.exception("kernel shutdown for %s", self.key)
        self._set_status("dead")
        self.save_now()

    def _drain_queue(self) -> None:
        try:
            while True:
                item = self.exec_queue.get_nowait()
                if isinstance(item, str):
                    self._emit("execution_state", {"cellId": item, "state": "done"})
        except queue.Empty:
            pass

    # --- notebook doc --------------------------------------------------------

    def notebook_model(self) -> dict[str, Any]:
        with self.doc_lock:
            return notebooks.to_model(self.doc, self.nb_rel).model_dump()

    def apply_ops(self, ops: list[dict[str, Any]]) -> None:
        with self.doc_lock:
            for op in ops:
                notebooks.apply_op(self.doc, op)
        self.save_soon()

    def _cell(self, cell_id: str) -> Any | None:
        with self.doc_lock:
            for cell in self.doc.cells:
                if cell.get("id") == cell_id:
                    return cell
        return None

    def save_soon(self) -> None:
        if self._save_timer is not None:
            self._save_timer.cancel()
        self._save_timer = threading.Timer(SAVE_DEBOUNCE_S, self.save_now)
        self._save_timer.daemon = True
        self._save_timer.start()

    def save_now(self) -> None:
        try:
            with self.doc_lock:
                notebooks.save(self.nb_path, self.doc)
        except Exception:  # noqa: BLE001 — a failed save must not kill a pump
            logger.exception("notebook save failed for %s", self.key)

    # --- execution -----------------------------------------------------------

    def enqueue(self, cell_id: str) -> bool:
        cell = self._cell(cell_id)
        if cell is None or cell.get("cell_type") != "code":
            return False
        with self.doc_lock:
            cell["outputs"] = []
            cell["execution_count"] = None
        self._emit("execution_state", {"cellId": cell_id, "state": "queued"})
        self.exec_queue.put(cell_id)
        return True

    def enqueue_all(self) -> int:
        with self.doc_lock:
            ids = [c["id"] for c in self.doc.cells if c.get("cell_type") == "code"]
        return sum(1 for cid in ids if self.enqueue(cid))

    def _worker_loop(self) -> None:
        while True:
            item = self.exec_queue.get()
            if item is _STOP or self.closing:
                return
            try:
                self._execute(str(item))
            except Exception:  # noqa: BLE001 — keep the worker alive
                logger.exception("cell execution crashed (%s)", self.key)
                self._emit("execution_state", {"cellId": str(item), "state": "error"})

    def _execute(self, cell_id: str) -> None:
        cell = self._cell(cell_id)
        if cell is None:
            return
        with self.doc_lock:
            source = cell["source"]
        msg_id = self.kc.execute(source)
        self.msg_to_cell[msg_id] = cell_id
        self._emit("execution_state", {"cellId": cell_id, "state": "running"})
        content = self._await_reply(msg_id)
        state = "done" if content.get("status") == "ok" else "error"
        exec_count = content.get("execution_count")
        with self.doc_lock:
            if exec_count is not None:
                cell["execution_count"] = exec_count
        self._emit(
            "execution_state",
            {"cellId": cell_id, "state": state, "execCount": exec_count},
        )
        self.save_now()

    def _await_reply(self, msg_id: str) -> dict[str, Any]:
        """Block (worker thread) until the execute_reply for `msg_id` arrives.
        No overall cap — training cells legitimately run for hours; interrupt
        or a dead kernel are the exits."""
        while not self.closing:
            try:
                reply = self.kc.get_shell_msg(timeout=1)
            except queue.Empty:
                if self.km is None or not self.km.is_alive():
                    self._set_status("dead")
                    return {"status": "error"}
                continue
            if reply.get("parent_header", {}).get("msg_id") == msg_id:
                return reply.get("content", {})
        return {"status": "error"}

    # --- iopub pump -----------------------------------------------------------

    def _iopub_loop(self) -> None:
        while not self.closing:
            try:
                msg = self.kc.get_iopub_msg(timeout=1)
            except queue.Empty:
                continue
            except Exception:  # noqa: BLE001 — channel torn down mid-read
                if self.closing:
                    return
                logger.exception("iopub pump error (%s)", self.key)
                continue
            try:
                self._route_iopub(msg)
            except Exception:  # noqa: BLE001 — keep the pump alive
                logger.exception("iopub routing error (%s)", self.key)

    def _route_iopub(self, msg: dict[str, Any]) -> None:
        msg_type = msg.get("msg_type")
        content = msg.get("content", {})
        parent = msg.get("parent_header", {}).get("msg_id", "")
        cell_id = self.msg_to_cell.get(parent)

        if msg_type == "status":
            state = content.get("execution_state")
            if state in ("idle", "busy") and not self.closing:
                self._set_status(state)
            return
        if cell_id is None:
            return

        if msg_type == "stream":
            self._on_stream(cell_id, content)
        elif msg_type in ("display_data", "execute_result"):
            output = {
                "output_type": msg_type,
                "data": content.get("data", {}),
                "metadata": content.get("metadata", {}),
            }
            if msg_type == "execute_result":
                output["execution_count"] = content.get("execution_count")
            self._append_output(cell_id, output)
        elif msg_type == "error":
            self._append_output(
                cell_id,
                {
                    "output_type": "error",
                    "ename": content.get("ename", ""),
                    "evalue": content.get("evalue", ""),
                    "traceback": content.get("traceback", []),
                },
            )
        elif msg_type == "clear_output":
            with self.doc_lock:
                cell = self._cell(cell_id)
                if cell is not None:
                    cell["outputs"] = []
            self._emit("output", {"cellId": cell_id, "output": None})

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

    def _append_output(self, cell_id: str, output: dict[str, Any]) -> None:
        with self.doc_lock:
            cell = self._cell(cell_id)
            if cell is None:
                return
            outputs = cell["outputs"]
            # Merge consecutive stream chunks of the same name (nbformat norm).
            if (
                output["output_type"] == "stream"
                and outputs
                and outputs[-1].get("output_type") == "stream"
                and outputs[-1].get("name") == output["name"]
            ):
                outputs[-1]["text"] += output["text"]
            else:
                import nbformat

                outputs.append(nbformat.from_dict(output))
        self._emit("output", {"cellId": cell_id, "output": output})
        self.save_soon()

    # --- fanout ---------------------------------------------------------------

    def _set_status(self, status: str) -> None:
        if status == self.status:
            return
        self.status = status
        self._emit("kernel_status", {"status": status})

    def _emit(self, event: str, data: dict[str, Any]) -> None:
        """Send to this session's subscribers, from any thread."""
        payload = _evt(event, {"sessionKey": self.key, **data})

        async def send() -> None:
            for conn in list(self.subscribers):
                try:
                    await conn.send_json(payload)
                except Exception:  # noqa: BLE001 — dead socket
                    self.subscribers.discard(conn)

        asyncio.run_coroutine_threadsafe(send(), self.loop)


class TrainingKernelManager:
    """Process-global registry of kernel sessions (training survives pane close)."""

    def __init__(self) -> None:
        self.sessions: dict[str, KernelSession] = {}
        self._open_lock = asyncio.Lock()

    async def handle(self, conn: Any, msg: dict[str, Any]) -> None:
        event = str(msg.get("event", ""))
        data = msg.get("data") or {}
        if event == "open":
            # Detached: a cold kernel takes seconds to boot; never stall the
            # receive loop (see ws-handler conventions).
            asyncio.create_task(self._open(conn, data))
            return
        if event == "watch_run":
            from backend.modules.training.metrics import backfill, known_runs

            run_id = str(data.get("runId", ""))
            await conn.send_json(
                _evt(
                    "run_backfill",
                    {
                        "runId": run_id,
                        "points": backfill(run_id),
                        "runs": known_runs(),
                    },
                )
            )
            return
        session = self.sessions.get(str(data.get("sessionKey", "")))
        if session is None:
            if event in (
                "run_cell",
                "run_all",
                "cells",
                "interrupt",
                "restart",
                "shutdown",
            ):
                await conn.send_json(
                    _evt(
                        "error",
                        {"message": f"unknown session: {data.get('sessionKey')}"},
                    )
                )
            return
        if event == "run_cell":
            if not session.enqueue(str(data.get("cellId", ""))):
                await conn.send_json(
                    _evt(
                        "error",
                        {
                            "sessionKey": session.key,
                            "message": f"no code cell {data.get('cellId')}",
                        },
                    )
                )
        elif event == "run_all":
            session.enqueue_all()
        elif event == "cells":
            ops = data.get("ops") or []
            try:
                await asyncio.to_thread(session.apply_ops, list(ops))
            except ValueError as exc:
                await conn.send_json(
                    _evt("error", {"sessionKey": session.key, "message": str(exc)})
                )
                return
            # Everyone else re-syncs; the sender already applied optimistically.
            payload = _evt(
                "cells_changed",
                {"sessionKey": session.key, "notebook": session.notebook_model()},
            )
            for sub in list(session.subscribers):
                if sub is not conn:
                    await sub.send_json(payload)
        elif event == "interrupt":
            await asyncio.to_thread(session.interrupt)
        elif event == "restart":
            asyncio.create_task(asyncio.to_thread(session.restart))
        elif event == "shutdown":
            self.sessions.pop(session.key, None)
            asyncio.create_task(asyncio.to_thread(session.shutdown))

    async def _open(self, conn: Any, data: dict[str, Any]) -> None:
        project_id = str(data.get("projectId", ""))
        nb_rel = str(data.get("notebook") or projects.DEFAULT_NOTEBOOK)
        key = f"{project_id}:{nb_rel}"
        try:
            async with self._open_lock:
                session = self.sessions.get(key)
                if session is None:
                    session = await asyncio.to_thread(
                        self._create, project_id, nb_rel, key
                    )
                    self.sessions[key] = session
        except UnknownProjectError as exc:
            logger.warning("kernel open failed for %s: %s", key, exc)
            payload: dict[str, Any] = {
                "sessionKey": key,
                "message": str(exc),
                "code": "unknown_project",
            }
            await conn.send_json(_evt("error", payload))
            return
        except Exception as exc:  # noqa: BLE001 — surfaced to the pane
            logger.exception("kernel open failed for %s", key)
            payload: dict[str, Any] = {"sessionKey": key, "message": str(exc)}
            await conn.send_json(_evt("error", payload))
            return
        session.subscribers.add(conn)
        await conn.send_json(
            _evt(
                "opened",
                {
                    "sessionKey": key,
                    "projectId": project_id,
                    "notebook": session.notebook_model(),
                    "kernel": session.status,
                },
            )
        )

    def _create(self, project_id: str, nb_rel: str, key: str) -> KernelSession:
        project = projects.get_project(project_id)
        if project is None:
            raise UnknownProjectError(f"unknown project: {project_id}")
        if not venv_ready(project):
            raise ValueError("project venv is not ready yet — wait for the bootstrap")
        session = KernelSession(key, project, nb_rel, self._loop())
        session.start()
        return session

    @staticmethod
    def _loop() -> asyncio.AbstractEventLoop:
        from backend.modules.training import stream

        loop = stream._loop  # captured when the first `/ws` connection subscribed
        if loop is None or loop.is_closed():
            raise RuntimeError("no event loop available for kernel fanout")
        return loop

    def detach(self, conn: Any) -> None:
        """Socket closed: unsubscribe everywhere. Kernels keep running."""
        for session in self.sessions.values():
            session.subscribers.discard(conn)

    def session_for(self, project_id: str, nb_rel: str) -> KernelSession | None:
        return self.sessions.get(f"{project_id}:{nb_rel}")

    async def shutdown_all(self) -> None:
        for key in list(self.sessions):
            session = self.sessions.pop(key)
            await asyncio.to_thread(session.shutdown)


training_kernels = TrainingKernelManager()


async def handle_training_message(conn: Any, msg: dict[str, Any]) -> None:
    """`training` channel entry point (kernel events; more slices ride along)."""
    await training_kernels.handle(conn, msg)

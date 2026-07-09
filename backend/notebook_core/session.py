"""Neutral Jupyter kernel session: one kernel + one nbformat doc + the browser
tabs watching them.

Spawned via `jupyter_client`'s *sync* API on daemon threads — its
`KernelProvisionerBase`/`Popen` spawn path is event-loop-agnostic, which is what
makes this work under uvicorn `--reload` on Windows (asyncio's subprocess API
requires the ProactorEventLoop; uvicorn's reloader uses the SelectorEventLoop —
see backend/modules/lsp/manager.py for the long version). The in-memory nbformat
`doc` is authoritative: UI/agent edits mutate it, iopub outputs append to it, and
it's flushed to disk atomically (debounced, and after every completed execution).

Subclasses customize two seams: `_on_stream` (e.g. the training module's stdout
sentinel scan) and, later, reactive/comm behavior. Everything else is domain-free.

Channel protocol (`{channel, event, data}`):

| Direction     | event               | data                                          |
| ------------- | ------------------- | --------------------------------------------- |
| server→client | `opened`            | `{sessionKey, notebook, kernel, ...}`         |
| server→client | `kernel_status`     | `{sessionKey, status}`                        |
| server→client | `execution_state`   | `{sessionKey, cellId, state, execCount?}`     |
| server→client | `output`            | `{sessionKey, cellId, output}`                |
| server→client | `cells_changed`     | `{sessionKey, notebook}`                      |
| server→client | `error`             | `{sessionKey?, message}`                      |
"""

from __future__ import annotations

import asyncio
import base64
import logging
import queue
import threading
from typing import Any

from backend.notebook_core import notebooks
from backend.notebook_core.config import SessionConfig
from backend.notebook_core.reactive import ReactiveGraph

logger = logging.getLogger(__name__)

SAVE_DEBOUNCE_S = 2.0
START_TIMEOUT_S = 60.0
_STOP = object()  # worker-queue poison pill


class _DeleteDefs:
    """A worker-queue item: drop stale names from the kernel namespace (marimo
    semantics) so downstream cells raise NameError instead of reading dead values."""

    def __init__(self, names: list[str]) -> None:
        self.names = names


class _CommSend:
    """A side-queue item: a widget `comm_msg` from the browser to send to the kernel
    on the shell channel. Serviced by the worker thread, which is the sole owner of
    the (non-thread-safe) zmq shell socket — even mid-execution, so widgets stay live."""

    def __init__(
        self, comm_id: str, data: dict[str, Any], buffers: list[bytes]
    ) -> None:
        self.comm_id = comm_id
        self.data = data
        self.buffers = buffers


def _b64_buffers(buffers: Any) -> list[str]:
    """Base64-encode binary comm buffers (memoryviews/bytes) so they survive JSON."""
    out: list[str] = []
    for buf in buffers or ():
        raw = buf.tobytes() if isinstance(buf, memoryview) else bytes(buf)
        out.append(base64.b64encode(raw).decode("ascii"))
    return out


def _make_kernel_manager(python_executable: str, display_name: str):
    """A jupyter_client KernelManager pinned to a specific python, with an
    in-memory kernelspec (no kernelspec files; `interrupt_mode='signal'` so the
    provisioner wires the Windows interrupt event / POSIX SIGINT for us)."""
    from jupyter_client.kernelspec import KernelSpec
    from jupyter_client.manager import KernelManager

    spec = KernelSpec(
        argv=[
            python_executable,
            "-m",
            "ipykernel_launcher",
            "-f",
            "{connection_file}",
        ],
        display_name=display_name,
        language="python",
        interrupt_mode="signal",
    )

    class _PinnedKernelManager(KernelManager):
        @property
        def kernel_spec(self) -> KernelSpec:  # type: ignore[override]
            return spec

    return _PinnedKernelManager()


class KernelSession:
    """One kernel + one notebook doc + the browser tabs watching them."""

    def __init__(self, config: SessionConfig, loop: asyncio.AbstractEventLoop) -> None:
        self.config = config
        self.key = config.key
        self.channel = config.channel
        self.loop = loop
        self.nb_path = config.notebook_abs_path
        self.rel_path = config.rel_path
        self.doc = notebooks.load(self.nb_path)
        self.doc_lock = threading.RLock()
        self.mode = self._read_mode()
        self.graph: ReactiveGraph | None = None
        self.subscribers: set[Any] = set()
        self.status = "starting"
        self.closing = False
        self.km: Any = None
        self.kc: Any = None
        self.exec_queue: queue.Queue[Any] = queue.Queue()
        self.comm_q: queue.Queue[_CommSend] = (
            queue.Queue()
        )  # browser→kernel widget msgs
        self.comms: dict[str, dict[str, Any]] = {}  # comm_id -> {target, state}
        self.msg_to_cell: dict[str, str] = {}
        self._save_timer: threading.Timer | None = None
        self._worker: threading.Thread | None = None
        self._iopub: threading.Thread | None = None

    # --- lifecycle (all blocking work runs on threads) ----------------------

    def start(self) -> None:
        """Spawn the kernel and the pump threads. Blocking — call off-loop."""
        self.km = _make_kernel_manager(
            self.config.python_executable, self.config.display_name
        )
        self.km.start_kernel(cwd=self.config.cwd)
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
            return notebooks.to_model(self.doc, self.rel_path).model_dump()

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
        ids = self._code_cells()
        if self.mode == "reactive":
            self.rebuild_graph()
            if self.graph is not None:
                order = {cid: i for i, cid in enumerate(self.graph.order)}
                ids.sort(key=lambda c: order.get(c, len(order)))
        return sum(1 for cid in ids if self.enqueue(cid))

    # --- reactive dataflow ---------------------------------------------------

    def _read_mode(self) -> str:
        with self.doc_lock:
            horrible = self.doc.metadata.get("horrible", {})
        mode = horrible.get("execution_mode")
        return mode if mode in ("reactive", "classic") else self.config.default_mode

    def _code_cells(self) -> list[str]:
        with self.doc_lock:
            return [c["id"] for c in self.doc.cells if c.get("cell_type") == "code"]

    def _code_sources(self) -> list[tuple[str, str]]:
        with self.doc_lock:
            return [
                (c["id"], c.get("source", ""))
                for c in self.doc.cells
                if c.get("cell_type") == "code"
            ]

    def rebuild_graph(self) -> ReactiveGraph:
        """Re-analyze code cells into a dependency graph and fan it to the UI."""
        graph = ReactiveGraph.build(self._code_sources())
        self.graph = graph
        self._emit("graph", graph.to_payload())
        return graph

    def set_mode(self, mode: str) -> None:
        if mode not in ("reactive", "classic"):
            return
        self.mode = mode
        with self.doc_lock:
            self.doc.metadata.setdefault("horrible", {})["execution_mode"] = mode
        self.save_soon()
        self._emit("mode", {"mode": mode})
        if mode == "reactive":
            self.rebuild_graph()

    def reactive_enqueue(self, cell_id: str) -> bool:
        """Run `cell_id` and its transitive dependents in topological order,
        deleting any names this cell no longer provides first."""
        cell = self._cell(cell_id)
        if cell is None or cell.get("cell_type") != "code":
            return False
        old_provider = dict(self.graph.provider) if self.graph else {}
        graph = self.rebuild_graph()
        # A cell with its own diagnostic (syntax/cycle/multiple-defs) runs alone so
        # the user sees the error, without cascading a broken definition.
        run_ids = (
            [cell_id] if graph.has_diagnostic(cell_id) else graph.run_order(cell_id)
        )
        stale = sorted(set(old_provider) - set(graph.provider))
        if stale:
            self.exec_queue.put(_DeleteDefs(stale))
        for cid in run_ids:
            self.enqueue(cid)
        return True

    def on_cells_changed(self, had_delete: bool) -> None:
        """After UI/agent cell ops: refresh the graph, and (on delete) drop any
        now-undefined names and re-run the cells that depended on them."""
        if self.mode != "reactive":
            return
        old_provider = dict(self.graph.provider) if self.graph else {}
        graph = self.rebuild_graph()
        if not had_delete:
            return
        stale = sorted(set(old_provider) - set(graph.provider))
        if not stale:
            return
        stale_set = set(stale)
        dependents = [
            cid for cid in graph.order if graph.analyses[cid].refs & stale_set
        ]
        self.exec_queue.put(_DeleteDefs(stale))
        for cid in dependents:
            self.enqueue(cid)

    def _worker_loop(self) -> None:
        while True:
            try:
                # Short timeout so idle widget messages (comm_q) still get serviced.
                item = self.exec_queue.get(timeout=0.2)
            except queue.Empty:
                self._drain_comms()
                continue
            if item is _STOP or self.closing:
                return
            if isinstance(item, _DeleteDefs):
                self._delete_defs(item.names)
                continue
            try:
                self._execute(str(item))
            except Exception:  # noqa: BLE001 — keep the worker alive
                logger.exception("cell execution crashed (%s)", self.key)
                self._emit("execution_state", {"cellId": str(item), "state": "error"})

    def _delete_defs(self, names: list[str]) -> None:
        """Drop stale names from the kernel namespace (output-suppressed, not mapped
        to any cell, so iopub routing ignores it)."""
        if not names:
            return
        src = f"for _n in {names!r}: globals().pop(_n, None)\n"
        try:
            msg_id = self.kc.execute(src, silent=True, store_history=False)
            self._await_reply(msg_id)
        except Exception:  # noqa: BLE001 — namespace cleanup must not kill the worker
            logger.exception("stale-def deletion failed (%s)", self.key)

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
        No overall cap — cells legitimately run for a long time; interrupt or a
        dead kernel are the exits."""
        while not self.closing:
            # Service widget messages while a cell runs, so sliders stay interactive.
            self._drain_comms()
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
        # Comm (ipywidgets) messages have no cell — route them before the guard.
        if msg_type in ("comm_open", "comm_msg", "comm_close"):
            self._route_comm(msg_type, content, msg.get("buffers"))
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

    # --- comm / ipywidgets ----------------------------------------------------

    def _route_comm(self, msg_type: str, content: dict[str, Any], buffers: Any) -> None:
        """Forward a kernel comm message (widget state) to the browser, folding
        state updates into a per-comm snapshot so a pane that attaches to an
        already-running kernel (dockview unmounts inactive panes) can be resynced
        without re-driving the kernel — see `comms_snapshot`."""
        comm_id = content.get("comm_id", "")
        if msg_type == "comm_open":
            self.comms[comm_id] = {
                "target": content.get("target_name", ""),
                "state": dict(content.get("data", {}).get("state", {})),
            }
        elif msg_type == "comm_msg":
            rec = self.comms.get(comm_id)
            data = content.get("data", {})
            if rec is not None and data.get("method") in ("update", "echo_update"):
                rec["state"].update(data.get("state") or {})
        elif msg_type == "comm_close":
            self.comms.pop(comm_id, None)
        self._emit(msg_type, {"comm": content, "buffers": _b64_buffers(buffers)})

    def comms_snapshot(self) -> list[dict[str, Any]]:
        """Current open widget comms (comm_id, target, last-known state). The
        backend is the source of truth for comm state — it sees every open and
        update — so resyncing a late-attaching pane needs no `comm_info` round
        trip to the kernel."""
        return [
            {
                "comm_id": comm_id,
                "target_name": rec.get("target", ""),
                "state": rec.get("state", {}),
            }
            for comm_id, rec in self.comms.items()
        ]

    def send_comm(
        self, comm_id: str, data: dict[str, Any], buffers: list[bytes]
    ) -> None:
        """Queue a browser→kernel widget message; the worker thread sends it on the
        shell channel (the only owner of the zmq socket)."""
        self.comm_q.put(_CommSend(comm_id, data, buffers))

    def _drain_comms(self) -> None:
        """Send any queued widget messages to the kernel. Worker thread only."""
        try:
            while True:
                item = self.comm_q.get_nowait()
                try:
                    self.kc.session.send(
                        self.kc.shell_channel.socket,
                        "comm_msg",
                        {"comm_id": item.comm_id, "data": item.data},
                        buffers=item.buffers or None,
                    )
                except Exception:  # noqa: BLE001 — one bad widget msg can't stall the worker
                    logger.exception("comm send failed (%s)", self.key)
        except queue.Empty:
            pass

    def _on_stream(self, cell_id: str, content: dict[str, Any]) -> None:
        """Route a `stream` iopub message. Base: append the text verbatim.
        Subclasses (e.g. training) override to scan for stdout sentinels first."""
        name = content.get("name", "stdout")
        text = content.get("text", "")
        if text:
            self._append_output(
                cell_id, {"output_type": "stream", "name": name, "text": text}
            )

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
        payload = {
            "channel": self.channel,
            "event": event,
            "data": {"sessionKey": self.key, **data},
        }

        async def send() -> None:
            for conn in list(self.subscribers):
                try:
                    await conn.send_json(payload)
                except Exception:  # noqa: BLE001 — dead socket
                    self.subscribers.discard(conn)

        asyncio.run_coroutine_threadsafe(send(), self.loop)

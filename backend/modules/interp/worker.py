"""Supervises the interpretability worker (`worker_main.py`) from the backend side.

One process, started on demand and kept: loading a model is the slow part, and a
worker that exited after each request would pay it every time. Requests go in as JSON
lines and are matched to replies by id, so two callers can wait on it at once (the
worker itself runs them in order).

Spawned with blocking `subprocess.Popen` and pumped on daemon threads — never
`asyncio.create_subprocess_exec`, which breaks on the SelectorEventLoop uvicorn uses
under `--reload` on Windows. Async callers go through `asyncio.to_thread`.

The interpreter is `-I` (isolated): the backend's directory and `PYTHONPATH` must not
be importable from the worker, or a module of ours could shadow a library of theirs.
"""

from __future__ import annotations

import itertools
import json
import logging
import os
import subprocess
import threading
from collections import deque
from concurrent.futures import Future
from pathlib import Path
from typing import Any

from backend.modules.interp import env

logger = logging.getLogger(__name__)

WORKER_SCRIPT = Path(__file__).with_name("worker_main.py")

#: How long a start may take before it is reported as failed. Importing torch with
#: CUDA is several seconds; loading a model is a separate request with its own wait.
START_TIMEOUT = 120.0


class WorkerUnavailable(Exception):
    """The worker cannot run: no environment, or it could not start."""


class Worker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[str] | None = None
        self._pending: dict[str, Future[dict[str, Any]]] = {}
        self._ids = itertools.count(1)
        self._ready = threading.Event()
        self._ops: list[str] = []
        self._stderr: deque[str] = deque(maxlen=60)
        self._exit: int | None = None

    # ── lifecycle ────────────────────────────────────────────────────────────

    def state(self) -> str:
        if not env.python_path().is_file() or "base" not in env.installed_groups():
            return "no_env"
        with self._lock:
            proc = self._proc
        if proc is None:
            return "crashed" if self._exit not in (None, 0) else "stopped"
        if proc.poll() is not None:
            return "crashed"
        return "ready" if self._ready.is_set() else "starting"

    def start(self, hf_token: str | None = None) -> None:
        """Start the worker if it is not running. Blocking until it reports ready."""
        if self.state() == "no_env":
            raise WorkerUnavailable("the interp environment is not installed")
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                proc_running = True
            else:
                proc_running = False
                child_env = dict(os.environ)
                if hf_token:
                    child_env["HF_TOKEN"] = hf_token
                # Unbuffered, so a reply is on the pipe the moment it is written.
                child_env["PYTHONUNBUFFERED"] = "1"
                self._ready.clear()
                self._exit = None
                self._stderr.clear()
                self._proc = subprocess.Popen(
                    [str(env.python_path()), "-I", str(WORKER_SCRIPT)],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=child_env,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                proc = self._proc
                threading.Thread(
                    target=self._read_stdout, args=(proc,), daemon=True
                ).start()
                threading.Thread(
                    target=self._read_stderr, args=(proc,), daemon=True
                ).start()
        if not proc_running and not self._ready.wait(START_TIMEOUT):
            self.stop()
            raise WorkerUnavailable(
                "the worker did not start in time: "
                + " | ".join(list(self._stderr)[-3:])
            )
        if self.state() == "crashed":
            raise WorkerUnavailable(
                "the worker exited: " + " | ".join(list(self._stderr)[-3:])
            )

    def stop(self) -> None:
        with self._lock:
            proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
            proc.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()
        self._fail_pending("the worker was stopped")

    # ── pipes ────────────────────────────────────────────────────────────────

    def _read_stdout(self, proc: subprocess.Popen[str]) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            try:
                message = json.loads(line)
            except ValueError:
                self._stderr.append(f"(stdout) {line.rstrip()[:300]}")
                continue
            if message.get("event") == "ready":
                self._ops = list(message.get("ops") or [])
                self._ready.set()
                continue
            future = None
            with self._lock:
                future = self._pending.pop(str(message.get("id")), None)
            if future is not None and not future.done():
                future.set_result(message)
        self._exit = proc.wait()
        self._ready.clear()
        self._fail_pending(f"the worker exited with code {self._exit}")

    def _read_stderr(self, proc: subprocess.Popen[str]) -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            stripped = line.rstrip()
            if stripped:
                self._stderr.append(stripped[-400:])

    def _fail_pending(self, reason: str) -> None:
        with self._lock:
            pending, self._pending = self._pending, {}
        for future in pending.values():
            if not future.done():
                future.set_result({"state": "failed", "traceback": reason})

    # ── requests ─────────────────────────────────────────────────────────────

    def request(
        self, op: str, args: dict[str, Any] | None = None, timeout: float = 600.0
    ) -> dict[str, Any]:
        """Send one request and wait for its reply. Blocking.

        Never raises for an op's own failure — that comes back as `failed` /
        `not_checked`. A worker that is not running is `not_checked` too: the
        caller asked a question nothing could answer, which is different from an
        answer that went wrong.
        """
        if self.state() != "ready":
            return {"state": "not_checked", "reason": f"the worker is {self.state()}"}
        rid = str(next(self._ids))
        future: Future[dict[str, Any]] = Future()
        with self._lock:
            proc = self._proc
            self._pending[rid] = future
        if proc is None or proc.stdin is None:
            return {"state": "not_checked", "reason": "the worker is not running"}
        try:
            proc.stdin.write(
                json.dumps({"id": rid, "op": op, "args": args or {}}) + "\n"
            )
            proc.stdin.flush()
        except OSError as exc:
            with self._lock:
                self._pending.pop(rid, None)
            return {
                "state": "failed",
                "traceback": f"could not write to the worker: {exc}",
            }
        try:
            return future.result(timeout=timeout)
        except TimeoutError:
            with self._lock:
                self._pending.pop(rid, None)
            return {
                "state": "failed",
                "traceback": f"{op} took longer than {timeout:.0f}s",
            }

    def describe(self) -> dict[str, Any]:
        with self._lock:
            pid = self._proc.pid if self._proc else None
        return {
            "state": self.state(),
            "pid": pid,
            "ops": self._ops,
            "exitCode": self._exit,
            "log": list(self._stderr)[-12:],
        }


#: The one worker this node runs.
worker = Worker()

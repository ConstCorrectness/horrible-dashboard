"""Spawning the tracer subprocess and relaying its progress.

The tracer is a separate process by design (see `tracer.py`), so the backend's
job here is small: write a spec, launch `python -m backend.modules.llamacpp.tracer`,
and turn its NDJSON stdout into events the route can stream straight through.

Two details are load-bearing:

- **`subprocess.Popen` on a worker thread, never `asyncio.create_subprocess_exec`.**
  Under `uvicorn --reload` on Windows the loop is a `SelectorEventLoop`, which
  cannot spawn subprocesses at all — the same trap the LSP and PTY managers hit.
- **A crash is a result, not an exception.** The whole reason for the subprocess
  is that a ggml callback can segfault; a non-zero exit with no `error` line
  becomes an explicit "the tracer died" event rather than a stream that simply
  stops.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from backend import paths
from backend.modules.llamacpp import traces

logger = logging.getLogger(__name__)

#: Nothing may run forever: a traced pass is minutes at worst, and a wedged
#: subprocess holding a 20 GB mmap is not something to discover tomorrow.
TIMEOUT_SECONDS = 20 * 60


def new_trace_id() -> str:
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def available() -> tuple[bool, str]:
    """Whether this environment can trace at all.

    Checked by importing in *this* process, which is cheap (the wheel is only
    imported, not used) and gives the pane a real reason to show rather than a
    subprocess that dies with an ImportError two seconds after the user clicks.
    """
    from backend import extras

    # `extras.probe` keeps the distinction this function's original comment
    # already noticed: a broken native load is not an ImportError, and saying
    # "not installed" about a wheel that is present sends people to reinstall it.
    verdict = extras.probe("llamacpp")
    if verdict.available:
        return True, ""
    detail = verdict.reason or "llama-cpp-python is not available here"
    if verdict.certain:
        return False, (
            f"activations are unavailable: {detail}. "
            f"Install it with: {verdict.install}"
        )
    return False, f"could not determine whether activations are available: {detail}"


async def run_trace(spec: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    """Run one trace, yielding the subprocess's events plus a final summary."""
    ok, reason = available()
    if not ok:
        yield {"error": reason}
        return

    spec = dict(spec)
    spec.setdefault("traceId", new_trace_id())
    spec.setdefault("byteBudget", traces.budget_bytes())
    traces.traces_root().mkdir(parents=True, exist_ok=True)

    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    )
    with handle as spec_file:
        json.dump(spec, spec_file)
    spec_path = Path(handle.name)

    cmd = [sys.executable, "-m", "backend.modules.llamacpp.tracer", str(spec_path)]
    env = dict(os.environ)
    # The subprocess resolves the data dir itself to find the trace directory;
    # pinning it explicitly (already resolved, always absolute) means a
    # differently-launched child can never write its trace somewhere the backend
    # does not look.
    env["HORRIBLE_DATA_DIR"] = str(paths.data_dir().resolve())

    loop = asyncio.get_running_loop()
    proc = await loop.run_in_executor(None, lambda: _launch(cmd, env))
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    def pump() -> None:
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    # The tracer only ever prints JSON, so anything else is the
                    # native library talking (llama.cpp logs to stderr, but a
                    # loader warning can land here). Surface it as a log line
                    # rather than dropping it.
                    event = {"log": line}
                loop.call_soon_threadsafe(queue.put_nowait, event)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    await loop.run_in_executor(None, lambda: _spawn_pump(pump))

    saw_error = False
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            proc.kill()
            yield {"error": f"the tracer ran past {TIMEOUT_SECONDS // 60} minutes"}
            saw_error = True
            break
        try:
            event = await asyncio.wait_for(queue.get(), timeout=remaining)
        except TimeoutError:
            continue
        if event is None:
            break
        if event.get("error"):
            saw_error = True
        yield event

    code = await loop.run_in_executor(None, proc.wait)
    spec_path.unlink(missing_ok=True)

    if code != 0 and not saw_error:
        # A segfault inside a ggml callback lands here: no error line, just a
        # dead process. This is the message that stops it reading as a hang.
        yield {
            "error": (
                f"the tracer exited with code {code} without reporting an error — "
                "most likely a crash inside llama.cpp. Nothing was written."
            )
        }
        traces.delete_trace(str(spec["traceId"]))
        return
    if saw_error:
        traces.delete_trace(str(spec["traceId"]))
        return

    pruned = traces.prune()
    trace = traces.load(str(spec["traceId"]))
    if trace is not None:
        # Catalogued here — the one place every trace, forks included, is known
        # to have finished. `prune()` above already dropped the rows it evicted.
        from backend.modules.llamacpp import trace_catalog

        trace_catalog.record(trace)
    yield {
        "status": "stored",
        "traceId": spec["traceId"],
        "pruned": pruned,
        "trace": trace.summary_dict() if trace else None,
    }


def _launch(cmd: list[str], env: dict[str, str]) -> subprocess.Popen[str]:
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        # Merged, not discarded: llama.cpp prints its load failures to stderr,
        # and "the tracer exited with code 1" with the reason thrown away is the
        # least debuggable outcome available. Non-JSON lines become `log` events.
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )


def _spawn_pump(target: Any) -> None:
    import threading

    threading.Thread(target=target, name="llamacpp-trace-pump", daemon=True).start()

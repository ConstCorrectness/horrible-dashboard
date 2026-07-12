"""The verification runner: execute a small Python job (usually pytest over
submitted code + hidden tests) in a constrained subprocess and report the result.

Shared by both sides of the fabric: the **server** grades submissions
authoritatively (code golf, test duels, bug hunts), and a **node** runs the
visible tests locally for its task agent's feedback loop.

Safety posture (documented, not container-grade):

- Gated behind ``GAMES_ENABLE_CODE_EXEC=1`` — a server that hasn't opted in
  reports grading as unavailable instead of executing anything.
- ``python -I`` (isolated: no user site, no env-var injection), an **empty
  environment** (plus the handful of vars Python needs on Windows), a throwaway
  temp cwd, output capped, wall-clock kill with a process-tree kill
  (``taskkill /T /F`` on Windows, ``killpg`` on POSIX), and rlimits (CPU/memory)
  on POSIX.
- Windows + uvicorn --reload breaks asyncio subprocess spawning, so this module
  is **synchronous ``Popen``** by design; async callers run it in a thread
  (``asyncio.to_thread``) — the referee already does.

The roadmap hardening is per-job containers/machines; until then, treat hosted
grading as "untrusted-ish", which is why it's opt-in.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

OUTPUT_CAP = 64 * 1024
DEFAULT_TIMEOUT_S = 30.0


@dataclass(frozen=True)
class JobResult:
    ok: bool  # the job ran to completion (regardless of test outcomes)
    green: bool  # ok, and every test passed (the "submission is correct" signal)
    passed: int
    failed: int
    stdout: str
    stderr: str
    duration_ms: int


def code_exec_enabled() -> bool:
    return os.environ.get("GAMES_ENABLE_CODE_EXEC") == "1"


def _disabled_result() -> JobResult:
    return JobResult(
        ok=False,
        green=False,
        passed=0,
        failed=0,
        stdout="",
        stderr=(
            "code execution is disabled on this host "
            "(set GAMES_ENABLE_CODE_EXEC=1 to grade code submissions)"
        ),
        duration_ms=0,
    )


def _minimal_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if os.name == "nt":
        # Python on Windows needs SystemRoot (and friends) to even start.
        for var in ("SystemRoot", "SYSTEMROOT", "TEMP", "TMP", "COMSPEC"):
            if os.environ.get(var):
                env[var] = os.environ[var]
    return env


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                check=False,
            )
        else:
            import signal

            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _posix_limits(mem_mb: int, timeout_s: float):
    """A preexec_fn applying rlimits (POSIX only; Windows relies on the wall
    clock + kill tree)."""

    def apply() -> None:
        import resource

        os.setsid()  # own process group so the kill tree works
        cpu = max(1, int(timeout_s))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 2))
        mem = mem_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))

    return apply


_PYTEST_COUNTS = re.compile(r"(\d+) (passed|failed|errors?)")


def _parse_counts(output: str) -> tuple[int, int]:
    passed = failed = 0
    for count, kind in _PYTEST_COUNTS.findall(output):
        if kind == "passed":
            passed += int(count)
        else:
            failed += int(count)
    return passed, failed


def run_python_job(
    files: dict[str, str],
    *,
    entry: list[str] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    mem_mb: int = 512,
) -> JobResult:
    """Write `files` into a temp dir and run `entry` (default: ``pytest -q``)
    there. Synchronous — call via ``asyncio.to_thread`` from async code."""
    if not code_exec_enabled():
        return _disabled_result()
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="games-job-") as tmp:
        root = Path(tmp)
        for name, content in files.items():
            target = (root / name).resolve()
            if root.resolve() not in target.parents:
                continue  # refuse path escapes in submitted file names
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        cmd = [
            sys.executable,
            "-I",
            "-m",
            "pytest",
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
        ]
        if entry is not None:
            cmd = [sys.executable, "-I", *entry]
        popen_kwargs: dict = {
            "cwd": tmp,
            "env": _minimal_env(),
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
        }
        if os.name != "nt":
            popen_kwargs["preexec_fn"] = _posix_limits(mem_mb, timeout_s)
        try:
            proc = subprocess.Popen(cmd, **popen_kwargs)
        except Exception as exc:
            return JobResult(
                ok=False,
                green=False,
                passed=0,
                failed=0,
                stdout="",
                stderr=f"failed to start job: {exc}",
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        try:
            stdout, stderr = proc.communicate(timeout=timeout_s)
            timed_out = False
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            try:
                stdout, stderr = proc.communicate(timeout=5)
            except Exception:
                stdout, stderr = "", ""
            stderr = (stderr or "") + f"\n[killed: exceeded {timeout_s}s wall clock]"
            timed_out = True
        duration_ms = int((time.monotonic() - start) * 1000)
        stdout = (stdout or "")[:OUTPUT_CAP]
        stderr = (stderr or "")[:OUTPUT_CAP]
        passed, failed = _parse_counts(stdout)
        ok = not timed_out
        green = ok and proc.returncode == 0 and failed == 0 and passed > 0
        return JobResult(
            ok=ok,
            green=green,
            passed=passed,
            failed=failed,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
        )

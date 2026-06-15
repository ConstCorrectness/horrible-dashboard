"""Optional vLLM server lifecycle: the backend can spawn an OpenAI-compatible
vLLM server to serve a model locally, then the agent talks to it like any other
``openai``-dialect provider (see providers.py).

vLLM is heavy and platform-sensitive — it needs Linux/WSL2 or Docker and usually
a CUDA GPU, and does **not** run on native Windows. Spawning is therefore
best-effort and fully guarded: if vLLM isn't importable we raise a clear error
instead of launching a doomed process. The manager owns at most one subprocess
for the app's lifetime.
"""

from __future__ import annotations

import importlib.util
import shlex
import subprocess
import sys
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from backend.modules.agent.providers import PROVIDERS

DEFAULT_VLLM_MODEL = "google/gemma-2-2b-it"
_DEFAULT_PORT = urlparse(PROVIDERS["vllm"].default_endpoint).port or 8001
_LOG_LINES = 200

# Injectable so tests can spawn a fake process instead of a real ML server.
Launcher = Callable[[list[str]], "subprocess.Popen[str]"]


@dataclass
class VllmManager:
    """Owns at most one spawned vLLM subprocess for the app's lifetime."""

    launcher: Launcher | None = None
    _proc: subprocess.Popen[str] | None = field(default=None, init=False)
    _model: str | None = field(default=None, init=False)
    _port: int = field(default=_DEFAULT_PORT, init=False)
    _logs: deque[str] = field(
        default_factory=lambda: deque(maxlen=_LOG_LINES), init=False
    )

    def available(self) -> bool:
        """Whether vLLM is importable in the backend environment."""
        return importlib.util.find_spec("vllm") is not None

    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def endpoint(self) -> str:
        return f"http://localhost:{self._port}"

    def status(self) -> dict[str, Any]:
        return {
            "available": self.available(),
            "running": self.running(),
            "model": self._model,
            "endpoint": self.endpoint,
            "pid": self._proc.pid if self.running() else None,
            "logs": list(self._logs),
        }

    def spawn(self, model: str, port: int | None = None) -> dict[str, Any]:
        if self.running():
            raise RuntimeError("vLLM is already running")
        if self.launcher is None and not self.available():
            raise RuntimeError(
                "vLLM is not installed in the backend environment (uv add vllm). "
                "Note: vLLM needs Linux/WSL2 or Docker and usually a CUDA GPU — "
                "it does not run on native Windows."
            )
        self._model = model
        self._port = port or _DEFAULT_PORT
        cmd = [
            sys.executable,
            "-m",
            "vllm.entrypoints.openai.api_server",
            "--model",
            model,
            "--port",
            str(self._port),
        ]
        self._logs.clear()
        self._logs.append("$ " + shlex.join(cmd))
        launch = self.launcher or self._default_launch
        self._proc = launch(cmd)
        threading.Thread(target=self._drain, daemon=True).start()
        return self.status()

    def stop(self) -> dict[str, Any]:
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        self._proc = None
        return self.status()

    def _default_launch(self, cmd: list[str]) -> subprocess.Popen[str]:
        return subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

    def _drain(self) -> None:
        """Tail the subprocess output into a ring buffer for diagnostics."""
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            self._logs.append(line.rstrip())


vllm_manager = VllmManager()

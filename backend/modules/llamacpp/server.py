"""The supervised `llama-server` process.

Three things here are the difference between "it works" and "it silently doesn't":

**`--jinja` is passed explicitly, never left to the default.** It selects the
model's own chat template, which is what carries the model's tool-call syntax.
Upstream now defaults it *on* (verified on b10362, where a server started without
it reports the same template), but the default has flipped before, `--no-jinja`
exists, and it also reads `LLAMA_ARG_JINJA` from the environment — so whether the
agent can call tools would otherwise depend on which build got downloaded and what
is in the user's env. Without the model's template, `llama-server` accepts only
"commonly used" templates and falls back to a generic one for the rest, and that
failure is silent: 200, a perfectly fluent answer, and every tool in the schema
simply never called — indistinguishable from "this small model is bad at tool use",
the most expensive wrong conclusion available here. Hence the explicit flag and the
test that pins it.

**Readiness is a health gate, not a sleep.** Loading a 20 GB GGUF takes tens of
seconds; a request that arrives first gets a connection refused, which the agent
surfaces as "provider unreachable". `wait_ready` polls `/health` until the server
reports it has finished loading, and the status the UI reads distinguishes
`loading` from `ready` so nobody is told to retry a server that is working.

**Spawning goes through `subprocess.Popen` on a thread, never asyncio.** Under
`uvicorn --reload` on Windows the event loop is a `SelectorEventLoop`, where
asyncio subprocess creation raises — the same reason the LSP and PTY managers do
it this way.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import socket
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from backend.modules.llamacpp import binaries

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8080
_LOG_LINES = 400

#: Injectable so tests drive the lifecycle without a real inference server.
Launcher = Callable[[list[str]], "subprocess.Popen[str]"]


def _free_port(preferred: int) -> int:
    """`preferred` if it's bindable, else an ephemeral port.

    Not a nicety on this machine: Hyper-V reserves ranges that swallow ordinary
    dev ports, and 8080 is a popular one — a hard-coded port turns into a
    WinError 10013 that reads like a permissions problem.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass
class LlamaServerManager:
    """Owns at most one `llama-server` for the app's lifetime."""

    launcher: Launcher | None = None
    _proc: subprocess.Popen[str] | None = field(default=None, init=False)
    _model_path: str | None = field(default=None, init=False)
    _alias: str = field(default="", init=False)
    _port: int = field(default=DEFAULT_PORT, init=False)
    _ready: bool = field(default=False, init=False)
    _error: str = field(default="", init=False)
    _started_at: float = field(default=0.0, init=False)
    _logs: deque[str] = field(
        default_factory=lambda: deque(maxlen=_LOG_LINES), init=False
    )

    # ── state ────────────────────────────────────────────────────────────────

    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    @property
    def model_path(self) -> str | None:
        """The GGUF the running server loaded, or None.

        This is what makes the model explorer exact for this provider: an
        OpenAI-dialect server exposes no path, and here we *are* the one who chose
        the file.
        """
        return self._model_path if self.running() else None

    @property
    def alias(self) -> str:
        """The model id the server advertises on `/v1/models`."""
        return self._alias if self.running() else ""

    def status(self) -> dict[str, Any]:
        install = binaries.newest_install()
        return {
            "installed": install is not None,
            "install": install.to_dict() if install else None,
            "installs": [i.to_dict() for i in binaries.list_installs()],
            "running": self.running(),
            "ready": self._ready and self.running(),
            "modelPath": self._model_path if self.running() else None,
            "model": self.alias,
            "endpoint": self.endpoint,
            "pid": self._proc.pid if self.running() else None,
            "error": self._error,
            "uptimeSeconds": (time.time() - self._started_at)
            if self.running()
            else 0.0,
            "logs": list(self._logs),
        }

    # ── lifecycle ────────────────────────────────────────────────────────────

    def spawn(
        self,
        model_path: str,
        *,
        alias: str = "",
        port: int | None = None,
        context_size: int = 4096,
        gpu_layers: int = 0,
        threads: int | None = None,
        extra_args: list[str] | None = None,
    ) -> dict[str, Any]:
        if self.running():
            raise RuntimeError("llama-server is already running — stop it first")
        path = Path(model_path).expanduser()
        if not path.is_file():
            raise RuntimeError(f"no GGUF at {path}")
        install = binaries.newest_install()
        if install is None and self.launcher is None:
            raise RuntimeError(
                "no llama-server build is installed — install one from the "
                "llama.cpp pane (or POST /api/llamacpp/install)"
            )

        self._model_path = str(path)
        self._alias = alias or path.stem
        self._port = _free_port(port or DEFAULT_PORT)
        self._ready = False
        self._error = ""
        self._started_at = time.time()

        binary = str(install.binary) if install else "llama-server"
        cmd = [
            binary,
            "-m",
            str(path),
            "--host",
            "127.0.0.1",
            "--port",
            str(self._port),
            "--alias",
            self._alias,
            "-c",
            str(context_size),
            "-ngl",
            str(gpu_layers),
            # Explicit, not inherited: the default has flipped upstream before and
            # `LLAMA_ARG_JINJA` in the environment can turn it off. See the module
            # docstring — the failure mode is a fluent 200 with no tool call.
            "--jinja",
        ]
        if threads:
            cmd += ["-t", str(threads)]
        cmd += extra_args or []

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
        self._ready = False
        self._model_path = None
        self._alias = ""
        return self.status()

    async def wait_ready(self, timeout: float = 300.0) -> bool:
        """Poll `/health` until the server has finished loading the model.

        Returns False (and records the reason) rather than raising: a model that is
        too large for the machine exits during load, and "the process died, here are
        its last log lines" is the only useful thing to say about that.
        """
        deadline = time.time() + timeout
        async with httpx.AsyncClient(timeout=5) as client:
            while time.time() < deadline:
                if not self.running():
                    tail = " | ".join(list(self._logs)[-3:])
                    self._error = f"llama-server exited during load. {tail}"
                    return False
                try:
                    res = await client.get(f"{self.endpoint}/health")
                    if res.status_code == 200:
                        self._ready = True
                        return True
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.5)
        self._error = f"llama-server did not become ready within {timeout:.0f}s"
        return False

    # ── internals ────────────────────────────────────────────────────────────

    def _default_launch(self, cmd: list[str]) -> subprocess.Popen[str]:
        return subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=str(Path(cmd[0]).parent) if Path(cmd[0]).parent.is_dir() else None,
        )

    def _drain(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            self._logs.append(line.rstrip())


llama_manager = LlamaServerManager()

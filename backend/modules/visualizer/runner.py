import os
import sys
import tempfile
import asyncio
import logging
import subprocess
import threading
from typing import Dict
from backend.modules.ws import WsConnection

logger = logging.getLogger(__name__)

# Don't let a reader thread block forever relaying to a dead socket.
_SEND_TIMEOUT_S = 5.0

# Monkeypatching wrapper code prepended to Pygame scripts.
# Intercepts display updates, captures surfaces as JPEG, and streams Base64 to stdout.
PYGAME_WRAPPER_TEMPLATE = """# -*- coding: utf-8 -*-
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"
import sys
import base64
import io

# Ensure pygame is imported after setting the dummy driver
import pygame

# Standard capture logic
def _capture_frame():
    try:
        surface = pygame.display.get_surface()
        if surface:
            buf = io.BytesIO()
            pygame.image.save(surface, buf, "JPEG")
            b64_data = base64.b64encode(buf.getvalue()).decode("utf-8")
            sys.stdout.write(f"FRAME:{{b64_data}}\\n")
            sys.stdout.flush()
    except Exception as e:
        sys.stderr.write(f"Capture error: {{e}}\\n")
        sys.stderr.flush()

# Monkeypatch update calls
pygame.init()
_original_flip = pygame.display.flip
_original_update = pygame.display.update

def _custom_flip():
    _original_flip()
    _capture_frame()

def _custom_update(*args, **kwargs):
    _original_update(*args, **kwargs)
    _capture_frame()

pygame.display.flip = _custom_flip
pygame.display.update = _custom_update

# --- USER SCRIPT START ---
{user_code}
# --- USER SCRIPT END ---
"""


class PygameProcess:
    """Manages a single running Pygame subprocess and its output reader threads.

    Spawns with blocking `subprocess.Popen` and pumps stdout/stderr on daemon
    threads (relaying to the event loop with `run_coroutine_threadsafe`) instead
    of the asyncio subprocess API, which raises NotImplementedError on the
    SelectorEventLoop that uvicorn --reload uses on Windows — same reasoning as
    backend/modules/lsp/manager.py.
    """

    def __init__(self, ws_conn: WsConnection, code: str):
        self.ws_conn = ws_conn
        self.code = code
        self.proc: subprocess.Popen[bytes] | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.temp_file_path: str | None = None
        self.reader_threads: list[threading.Thread] = []
        # Set on terminate so a late reader-thread send stays quiet once we've
        # torn the session down deliberately.
        self.closing = False

    async def _send(self, event: str, data: dict) -> None:
        await self.ws_conn.send_json(
            {"channel": "visualizer", "event": event, "data": data}
        )

    def _send_threadsafe(self, event: str, data: dict) -> bool:
        """Relay an event to the browser from a reader thread, blocking until the
        loop has sent it (preserves order + applies backpressure). Returns False if
        the socket is gone, so the reader can stop."""
        if self.closing or self.loop is None:
            return False
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._send(event, data), self.loop
            )
            future.result(timeout=_SEND_TIMEOUT_S)
            return True
        except Exception:  # noqa: BLE001 — loop stopped / socket closed / timed out
            return False

    async def start(self) -> None:
        # Prepend the wrapper template
        wrapped_code = PYGAME_WRAPPER_TEMPLATE.format(user_code=self.code)

        # Create a temp file
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write(wrapped_code)
            self.temp_file_path = f.name

        self.loop = asyncio.get_running_loop()
        try:
            # Blocking spawn, offloaded so it never stalls the event loop.
            # Loop-agnostic on purpose (see class docstring) — works under
            # --reload on Windows.
            self.proc = await asyncio.to_thread(
                subprocess.Popen,
                [sys.executable, self.temp_file_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except Exception as e:
            logger.error(f"Failed to start Pygame subprocess: {e}")
            await self._send(
                "error",
                {"message": f"Failed to start Python subprocess: {str(e)}"},
            )
            self.cleanup()
            return

        self.reader_threads = [
            threading.Thread(target=self._read_stdout, name="viz-stdout", daemon=True),
            threading.Thread(target=self._read_stderr, name="viz-stderr", daemon=True),
        ]
        for t in self.reader_threads:
            t.start()

    def _read_stdout(self) -> None:
        """Pump FRAME: lines from the subprocess (blocking, on a daemon thread)
        and relay each as a `frame` event until EOF or the socket closes."""
        if not self.proc or not self.proc.stdout:
            return
        try:
            for line_bytes in self.proc.stdout:
                line = line_bytes.decode("utf-8", errors="ignore").strip()
                if line.startswith("FRAME:"):
                    b64_frame = line[len("FRAME:") :]
                    # Stream frame to WebSocket
                    if not self._send_threadsafe(
                        "frame",
                        {"frame": f"data:image/jpeg;base64,{b64_frame}"},
                    ):
                        return
        except Exception as e:  # noqa: BLE001 — keep one bad subprocess quiet
            logger.error(f"Error reading Pygame stdout: {e}")

    def _read_stderr(self) -> None:
        """Accumulate stderr (blocking, on a daemon thread) and relay it as an
        `error` event when the subprocess exits."""
        if not self.proc or not self.proc.stderr:
            return
        try:
            accumulated_errors = []
            for line_bytes in self.proc.stderr:
                line = line_bytes.decode("utf-8", errors="ignore").strip()
                accumulated_errors.append(line)
                # If we detect common import errors, notify immediately
                if "No module named 'pygame'" in line:
                    self._send_threadsafe(
                        "error",
                        {
                            "message": "Pygame is not installed on the system. Run 'uv add pygame' to install it."
                        },
                    )

            if accumulated_errors:
                err_msg = "\n".join(accumulated_errors)
                logger.warning(f"Pygame stderr: {err_msg}")
                # Filter out pygame community welcome header
                filtered_errs = [
                    e
                    for e in accumulated_errors
                    if "Hello from the pygame community" not in e
                ]
                if filtered_errs:
                    self._send_threadsafe(
                        "error",
                        {"message": "\n".join(filtered_errs)},
                    )
        except Exception as e:  # noqa: BLE001 — keep one bad subprocess quiet
            logger.error(f"Error reading Pygame stderr: {e}")

    def terminate(self) -> None:
        self.closing = True
        if self.proc and self.proc.poll() is None:
            try:
                # Killing the proc EOFs its pipes, which unblocks the daemon
                # reader threads so they exit on their own.
                self.proc.terminate()
            except OSError as e:
                logger.debug(f"Pygame terminate failed: {e}")
        self.cleanup()

    def cleanup(self) -> None:
        if self.temp_file_path and os.path.exists(self.temp_file_path):
            try:
                os.remove(self.temp_file_path)
            except Exception as e:
                logger.warning(f"Failed to remove temp file {self.temp_file_path}: {e}")
            self.temp_file_path = None


class VisualizerManager:
    """Manages active Pygame sessions per active WebSocket connection."""

    def __init__(self):
        self.sessions: Dict[WsConnection, PygameProcess] = {}

    async def handle(self, ws_conn: WsConnection, message: dict) -> None:
        event = message.get("event")
        data = message.get("data") or {}

        if event == "start_pygame":
            code = data.get("code", "")
            # Stop any existing process for this connection
            self.stop_for(ws_conn)

            # Start new process
            proc = PygameProcess(ws_conn, code)
            self.sessions[ws_conn] = proc
            await proc.start()

        elif event == "stop_pygame":
            self.stop_for(ws_conn)

    def stop_for(self, ws_conn: WsConnection) -> None:
        if ws_conn in self.sessions:
            proc = self.sessions.pop(ws_conn)
            proc.terminate()

    def close_all(self) -> None:
        connections = list(self.sessions.keys())
        for conn in connections:
            self.stop_for(conn)


visualizer_manager = VisualizerManager()

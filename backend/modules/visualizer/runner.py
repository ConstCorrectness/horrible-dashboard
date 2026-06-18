import os
import sys
import tempfile
import asyncio
import logging
from typing import Dict
from backend.modules.ws import WsConnection

logger = logging.getLogger(__name__)

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
    """Manages a single running Pygame subprocess and its output reading tasks."""
    def __init__(self, ws_conn: WsConnection, code: str):
        self.ws_conn = ws_conn
        self.code = code
        self.proc: asyncio.subprocess.Process | None = None
        self.temp_file_path: str | None = None
        self.read_tasks: list[asyncio.Task] = []

    async def _send(self, event: str, data: dict) -> None:
        await self.ws_conn.send_json(
            {"channel": "visualizer", "event": event, "data": data}
        )

    async def start(self) -> None:
        # Prepend the wrapper template
        wrapped_code = PYGAME_WRAPPER_TEMPLATE.format(user_code=self.code)

        # Create a temp file
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write(wrapped_code)
            self.temp_file_path = f.name

        try:
            # Start python subprocess
            self.proc = await asyncio.create_subprocess_exec(
                sys.executable,
                self.temp_file_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            # Create async reading tasks
            self.read_tasks = [
                asyncio.create_task(self._read_stdout()),
                asyncio.create_task(self._read_stderr()),
            ]
        except Exception as e:
            logger.error(f"Failed to start Pygame subprocess: {e}")
            await self._send(
                "error",
                {"message": f"Failed to start Python subprocess: {str(e)}"},
            )
            self.cleanup()

    async def _read_stdout(self) -> None:
        if not self.proc or not self.proc.stdout:
            return
        try:
            while True:
                line_bytes = await self.proc.stdout.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode("utf-8", errors="ignore").strip()
                if line.startswith("FRAME:"):
                    b64_frame = line[len("FRAME:") :]
                    # Stream frame to WebSocket
                    await self._send(
                        "frame",
                        {"frame": f"data:image/jpeg;base64,{b64_frame}"},
                    )
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error reading Pygame stdout: {e}")

    async def _read_stderr(self) -> None:
        if not self.proc or not self.proc.stderr:
            return
        try:
            accumulated_errors = []
            while True:
                line_bytes = await self.proc.stderr.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode("utf-8", errors="ignore").strip()
                accumulated_errors.append(line)
                # If we detect common import errors, notify immediately
                if "No module named 'pygame'" in line:
                    await self._send(
                        "error",
                        {"message": "Pygame is not installed on the system. Run 'uv add pygame' to install it."},
                    )
            
            if accumulated_errors:
                err_msg = "\n".join(accumulated_errors)
                logger.warning(f"Pygame stderr: {err_msg}")
                # Filter out pygame community welcome header
                filtered_errs = [e for e in accumulated_errors if "Hello from the pygame community" not in e]
                if filtered_errs:
                    await self._send(
                        "error",
                        {"message": "\n".join(filtered_errs)},
                    )
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error reading Pygame stderr: {e}")

    def terminate(self) -> None:
        for t in self.read_tasks:
            t.cancel()
        if self.proc:
            try:
                self.proc.terminate()
            except ProcessLookupError:
                pass
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

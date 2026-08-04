import os
import asyncio
import struct
from typing import Optional

class XttsService:
    def __init__(self):
        self._process = None
        self._lock = asyncio.Lock()

    async def _ensure_process(self):
        if self._process is not None:
            return

        print("Starting XTTS-v2 runner subprocess via uv run (Python 3.11)...")
        runner_path = os.path.join(os.path.dirname(__file__), "xtts_runner.py")
        
        # We use uv run to run it in a python 3.11 environment with TTS installed
        cmd = [
            "uv", "run", "--no-project", "--python", "3.11", 
            "--with", "TTS", "--with", "torch", "--with", "soundfile", 
            "python", runner_path
        ]
        
        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=os.path.dirname(__file__)
        )

        # Wait for "READY"
        while True:
            line = await self._process.stdout.readline()
            if not line:
                stderr_output = await self._process.stderr.read()
                raise RuntimeError(f"XTTS runner failed to start: {stderr_output.decode()}")
            decoded = line.decode().strip()
            if decoded == "READY":
                print("XTTS-v2 runner ready.")
                break
            else:
                # print startup logs
                print(f"[XTTS Runner] {decoded}")

    async def generate_audio(self, text: str, speaker: str = "Claribel Dervla") -> bytes:
        async with self._lock:
            await self._ensure_process()
            
            # Send text
            self._process.stdin.write((text + "\n").encode())
            await self._process.stdin.drain()
            
            # Read size (4 bytes little-endian)
            size_bytes = await self._process.stdout.readexactly(4)
            size = struct.unpack('<I', size_bytes)[0]
            
            # Read audio data
            audio_data = await self._process.stdout.readexactly(size)
            return audio_data

xtts_service = XttsService()

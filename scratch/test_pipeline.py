import asyncio
import io
import subprocess
import os
import sys

# Add the project root to sys.path so we can import backend
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.modules.agent.xtts_service import xtts_service
from backend.modules.agent.stt_service import stt_service
import numpy as np

async def main():
    print("1. Generating TTS audio...")
    tts_audio_bytes = await xtts_service.generate_audio("Hello! This is a test of the audio pipeline.")
    
    # TTS gives us a WAV file. We need to convert it to a WebM/Opus chunk to simulate the browser
    print("2. Converting WAV to WebM/Opus using ffmpeg...")
    process = subprocess.Popen(
        ['ffmpeg', '-y', '-i', 'pipe:0', '-c:a', 'libopus', '-f', 'webm', 'pipe:1'],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    webm_bytes, stderr = process.communicate(input=tts_audio_bytes)
    
    if process.returncode != 0:
        print("Failed to convert to webm:")
        print(stderr.decode())
        return

    print(f"WebM bytes generated: {len(webm_bytes)} bytes")

    print("3. Passing WebM chunk to STT...")
    transcription = await stt_service.transcribe(webm_bytes)
    print(f"STT Transcription: '{transcription}'")

if __name__ == "__main__":
    asyncio.run(main())

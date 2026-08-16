"""Local speech-to-text (Whisper), for the Clubhouse voice agent.

Part of the optional ``voice`` extra (``uv sync --extra voice``) — Whisper pulls
in torch, so nothing here is imported until a caller actually asks for a
transcription. The route that does lazy-imports this module and turns the
``ImportError`` into a 503.

Decoding goes through **ffmpeg as a plain subprocess on a worker thread**, not
``asyncio.create_subprocess_exec``: under ``uvicorn --reload`` on Windows the
loop is a ``SelectorEventLoop``, which cannot spawn subprocesses at all.
"""

import asyncio
import logging
import subprocess

import numpy as np
import torch

logger = logging.getLogger(__name__)

MODEL_ID = "openai/whisper-tiny.en"
SAMPLE_RATE = 16000

# Whisper does not return "nothing" for silence — it returns its priors, and on
# a near-silent chunk those are stable and few. Treating them as speech is what
# makes an agent answer a room that said nothing.
_SILENCE_HALLUCINATIONS = frozenset(
    {"you", "thank you", "thanks for watching", "i'm going to"}
)


class SttService:
    def __init__(self) -> None:
        self.processor = None
        self.model = None
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self._lock = asyncio.Lock()

    def _load_model(self) -> None:
        if self.model is not None:
            return
        logger.info("Loading Whisper model %s on %s...", MODEL_ID, self.device)
        from transformers import WhisperForConditionalGeneration, WhisperProcessor

        self.processor = WhisperProcessor.from_pretrained(MODEL_ID)
        self.model = WhisperForConditionalGeneration.from_pretrained(MODEL_ID).to(
            self.device
        )
        logger.info("Whisper model loaded.")

    async def transcribe(self, audio_bytes: bytes) -> str:
        # Serialized: one Whisper pass at a time, so concurrent chunks from a
        # busy room don't multiply VRAM use.
        async with self._lock:
            return await asyncio.to_thread(self._transcribe_sync, audio_bytes)

    def _transcribe_sync(self, audio_bytes: bytes) -> str:
        self._load_model()

        # `-f webm` is passed explicitly: the browser sends MediaRecorder chunks
        # over a pipe, which is unseekable, so ffmpeg cannot probe the container.
        process = subprocess.Popen(
            [
                "ffmpeg",
                "-f",
                "webm",
                "-i",
                "pipe:0",
                "-f",
                "f32le",
                "-ac",
                "1",
                "-ar",
                str(SAMPLE_RATE),
                "pipe:1",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        raw_audio, stderr = process.communicate(input=audio_bytes)
        if process.returncode != 0 or not raw_audio:
            raise ValueError(
                f"ffmpeg failed to decode the audio stream: {stderr.decode()[:500]}"
            )

        data = np.frombuffer(raw_audio, dtype=np.float32)
        features = self.processor(
            data, sampling_rate=SAMPLE_RATE, return_tensors="pt"
        ).input_features.to(self.device)
        predicted_ids = self.model.generate(features)
        text = self.processor.batch_decode(predicted_ids, skip_special_tokens=True)[
            0
        ].strip()

        if text.strip(" .").lower() in _SILENCE_HALLUCINATIONS:
            return ""
        return text


stt_service = SttService()

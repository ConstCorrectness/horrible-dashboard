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
import os
import subprocess
from typing import Any

import numpy as np
import torch

logger = logging.getLogger(__name__)

MODEL_ID = os.getenv("WHISPER_MODEL", "openai/whisper-tiny.en")
SAMPLE_RATE = 16000

# Minimum RMS energy required before running Whisper inference.
# Near-silent audio chunks (background air hiss, muted line) fall below this
# threshold. Skipping them prevents Whisper from generating priors/hallucinations
# and saves significant CPU/GPU resources.
MIN_RMS_ENERGY = 0.0035

# Common Whisper silence hallucinations and subtitle artifacts
_SILENCE_HALLUCINATIONS = frozenset(
    {
        "you",
        "thank you",
        "thank you.",
        "thank you very much",
        "thank you so much",
        "thanks for watching",
        "thanks for watching!",
        "thanks for watching.",
        "thank you for watching",
        "thank you for watching.",
        "thanks for listening",
        "i'm going to",
        "subtitles by",
        "subscribe",
        "bye",
        "bye.",
        "bye!",
        "goodbye",
        "the end",
        "so",
        "oh",
        "okay",
        "[music]",
        "(bell dings)",
        "(music playing)",
        "(music)",
        "silence",
        "...",
        ".",
        "like and subscribe",
    }
)


class SttService:
    def __init__(self) -> None:
        self.processor = None
        self.model = None
        self.model_id = MODEL_ID
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self._lock = asyncio.Lock()

    def _load_model(self) -> None:
        if self.model is not None:
            return
        logger.info("Loading Whisper model %s on %s...", self.model_id, self.device)
        from transformers import WhisperForConditionalGeneration, WhisperProcessor

        self.processor = WhisperProcessor.from_pretrained(self.model_id)
        self.model = WhisperForConditionalGeneration.from_pretrained(self.model_id).to(
            self.device
        )
        logger.info("Whisper model loaded.")

    async def transcribe(self, audio_bytes: bytes, language: str | None = None) -> str:
        # Serialized: one Whisper pass at a time, so concurrent chunks from a
        # busy room don't multiply VRAM use.
        async with self._lock:
            return await asyncio.to_thread(self._transcribe_sync, audio_bytes, language)

    def _decode_audio(self, audio_bytes: bytes) -> bytes:
        if not audio_bytes or len(audio_bytes) < 32:
            return b""

        # Try auto-probe first (WAV, MP4, AAC, OGG, WebM), fallback to explicit WebM format
        for cmd in (
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
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
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
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
        ):
            try:
                process = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                raw_audio, _ = process.communicate(input=audio_bytes)
                if process.returncode == 0 and raw_audio:
                    return raw_audio
            except Exception as err:
                logger.debug("ffmpeg decoding attempt failed: %s", err)
        return b""

    def _transcribe_sync(self, audio_bytes: bytes, language: str | None = None) -> str:
        if not audio_bytes or len(audio_bytes) < 32:
            return ""

        raw_audio = self._decode_audio(audio_bytes)
        if not raw_audio or len(raw_audio) < 1600 * 4:  # less than 100ms of audio
            return ""

        data = np.frombuffer(raw_audio, dtype=np.float32)
        if data.size == 0:
            return ""
        data = np.nan_to_num(data)

        # Voice Activity Energy Check:
        # Check root-mean-square energy before running neural inference.
        rms = float(np.sqrt(np.mean(data**2)))
        if rms < MIN_RMS_ENERGY:
            logger.debug(
                "Audio below speech energy threshold (RMS %.5f < %.5f), skipping",
                rms,
                MIN_RMS_ENERGY,
            )
            return ""

        self._load_model()
        if self.processor is None or self.model is None:
            return ""

        try:
            features = self.processor(
                data, sampling_rate=SAMPLE_RATE, return_tensors="pt"
            ).input_features.to(self.device)

            generate_kwargs: dict[str, Any] = {}
            if (
                language
                and not self.model_id.endswith(".en")
                and hasattr(self.processor, "get_decoder_prompt_ids")
            ):
                try:
                    forced_decoder_ids = self.processor.get_decoder_prompt_ids(
                        language=language, task="transcribe"
                    )
                    generate_kwargs["forced_decoder_ids"] = forced_decoder_ids
                except Exception as ex:
                    logger.debug("Could not set forced_decoder_ids: %s", ex)

            predicted_ids = self.model.generate(features, **generate_kwargs)
            text = self.processor.batch_decode(predicted_ids, skip_special_tokens=True)[
                0
            ].strip()

            clean_text = text.strip(" .\"'").lower()
            if not clean_text or clean_text in _SILENCE_HALLUCINATIONS:
                return ""
            if clean_text.startswith("subtitles by") or clean_text.startswith(
                "[music]"
            ):
                return ""

            # Check for repetitive token loops (e.g. "you you you you you")
            words = clean_text.split()
            if len(words) >= 4 and len(set(words)) == 1:
                return ""

            return text.strip()
        except Exception as err:
            logger.warning("Whisper transcription failed: %s", err)
            return ""


stt_service = SttService()

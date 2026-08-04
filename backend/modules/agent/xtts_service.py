import os
import torch
import soundfile as sf
import io
import asyncio
from typing import Optional

class XttsService:
    def __init__(self):
        self.model = None
        self.config = None
        self.speaker_embeddings = None
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self._lock = asyncio.Lock()

    def _load_model(self):
        if self.model is not None:
            return

        print(f"Loading XTTS-v2 model on {self.device}...")
        from TTS.tts.configs.xtts_config import XttsConfig
        from TTS.tts.models.xtts import Xtts

        model_dir = os.path.expanduser("~/.local/share/tts/tts_models--multilingual--multi-dataset--xtts_v2")

        config = XttsConfig()
        config.load_json(os.path.join(model_dir, "config.json"))
        model = Xtts.init_from_config(config)
        model.load_checkpoint(config, checkpoint_dir=model_dir, eval=True)
        self.model = model.to(self.device)
        self.config = config

        speakers_file = os.path.join(model_dir, "speakers_xtts.pth")
        self.speaker_embeddings = torch.load(speakers_file, map_location="cpu", weights_only=False)
        print("XTTS-v2 loaded successfully.")

    async def generate_audio(self, text: str, speaker: str = "Claribel Dervla") -> bytes:
        async with self._lock:
            # Run loading and inference in a thread to avoid blocking the event loop
            return await asyncio.to_thread(self._generate_sync, text, speaker)

    def _generate_sync(self, text: str, speaker: str) -> bytes:
        self._load_model()
        
        if speaker not in self.speaker_embeddings:
            speaker = "Claribel Dervla" # fallback

        gpt_cond_latent = self.speaker_embeddings[speaker]["gpt_cond_latent"]
        speaker_embedding = self.speaker_embeddings[speaker]["speaker_embedding"]

        out = self.model.inference(
            text=text,
            language="en",
            gpt_cond_latent=gpt_cond_latent,
            speaker_embedding=speaker_embedding,
        )
        
        # Save to memory buffer
        buf = io.BytesIO()
        sf.write(buf, out["wav"], 24000, format='WAV')
        return buf.getvalue()

xtts_service = XttsService()

import io
import torch
import numpy as np
import asyncio

class SttService:
    def __init__(self):
        self.processor = None
        self.model = None
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self._lock = asyncio.Lock()

    def _load_model(self):
        if self.model is not None:
            return
            
        print(f"Loading Whisper model on {self.device}...")
        from transformers import WhisperProcessor, WhisperForConditionalGeneration
        self.processor = WhisperProcessor.from_pretrained("openai/whisper-tiny.en")
        self.model = WhisperForConditionalGeneration.from_pretrained("openai/whisper-tiny.en").to(self.device)
        print("Whisper model loaded successfully.")

    async def transcribe(self, audio_bytes: bytes) -> str:
        async with self._lock:
            return await asyncio.to_thread(self._transcribe_sync, audio_bytes)

    def _transcribe_sync(self, audio_bytes: bytes) -> str:
        self._load_model()
        
        import subprocess
        
        # Load audio from bytes and resample to 16kHz mono float32 using ffmpeg
        process = subprocess.Popen(
            ['ffmpeg', '-f', 'webm', '-i', 'pipe:0', '-f', 'f32le', '-ac', '1', '-ar', '16000', 'pipe:1'],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        raw_audio, stderr = process.communicate(input=audio_bytes)
        
        if process.returncode != 0 or not raw_audio:
            raise ValueError(f"FFmpeg failed to decode the audio stream: {stderr.decode()}")
            
        data = np.frombuffer(raw_audio, dtype=np.float32)

        input_features = self.processor(data, sampling_rate=16000, return_tensors="pt").input_features.to(self.device) 
        
        predicted_ids = self.model.generate(input_features)
        transcription = self.processor.batch_decode(predicted_ids, skip_special_tokens=True)[0]
        return transcription.strip()

stt_service = SttService()

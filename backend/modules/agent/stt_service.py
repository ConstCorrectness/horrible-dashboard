import io
import torch
import av
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
        
        # Load audio from bytes and resample to 16kHz mono using PyAV
        container = av.open(io.BytesIO(audio_bytes))
        resampler = av.AudioResampler(format='s16p', layout='mono', rate=16000)
        
        audio_chunks = []
        for frame in container.decode(audio=0):
            frame.pts = None
            for resampled_frame in resampler.resample(frame):
                audio_chunks.append(resampled_frame.to_ndarray().flatten())
                
        for resampled_frame in resampler.resample(None):
            audio_chunks.append(resampled_frame.to_ndarray().flatten())
            
        data = np.concatenate(audio_chunks).astype(np.float32) / 32768.0

        input_features = self.processor(data, sampling_rate=16000, return_tensors="pt").input_features.to(self.device) 
        
        predicted_ids = self.model.generate(input_features)
        transcription = self.processor.batch_decode(predicted_ids, skip_special_tokens=True)[0]
        return transcription.strip()

stt_service = SttService()

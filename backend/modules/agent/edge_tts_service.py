import edge_tts

class EdgeTTSService:
    async def generate_audio(self, text: str) -> bytes:
        # We can pick a natural sounding voice, e.g., "en-US-ChristopherNeural" or "en-US-AriaNeural"
        communicate = edge_tts.Communicate(text, "en-US-ChristopherNeural")
        
        audio_data = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data += chunk["data"]
                
        return audio_data

edge_tts_service = EdgeTTSService()

"""Text-to-speech for the Clubhouse voice agent, via Microsoft Edge's TTS.

Part of the optional ``voice`` extra. This replaced a local XTTS-v2 runner:
XTTS held a model in VRAM alongside the local LLM (and OOM'd against it), and
took seconds per utterance — dead air in a live room. Edge TTS is a network
call with no local weights, so it costs no VRAM and starts speaking at once.
The trade is that it is not local: utterances leave the machine.
"""

import edge_tts

VOICE = "en-US-ChristopherNeural"


class EdgeTTSService:
    async def generate_audio(self, text: str, voice: str = VOICE) -> bytes:
        communicate = edge_tts.Communicate(text, voice)
        audio = bytearray()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio += chunk["data"]
        return bytes(audio)


edge_tts_service = EdgeTTSService()

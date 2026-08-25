"""Text-to-speech for the Clubhouse voice agent, via Microsoft Edge's TTS.

Part of the optional ``voice`` extra. This replaced a local XTTS-v2 runner:
XTTS held a model in VRAM alongside the local LLM (and OOM'd against it), and
took seconds per utterance — dead air in a live room. Edge TTS is a network
call with no local weights, so it costs no VRAM and starts speaking at once.
The trade is that it is not local: utterances leave the machine.
"""

import edge_tts
from typing import Any

VOICE = "en-US-ChristopherNeural"

POPULAR_VOICES = [
    {"name": "en-US-ChristopherNeural", "gender": "Male", "locale": "en-US", "label": "Christopher (US Male - Warm & Authoritative)"},
    {"name": "en-US-JennyNeural", "gender": "Female", "locale": "en-US", "label": "Jenny (US Female - Clear & Conversational)"},
    {"name": "en-US-GuyNeural", "gender": "Male", "locale": "en-US", "label": "Guy (US Male - Casual & Friendly)"},
    {"name": "en-US-AriaNeural", "gender": "Female", "locale": "en-US", "label": "Aria (US Female - Expressive)"},
    {"name": "en-US-EricNeural", "gender": "Male", "locale": "en-US", "label": "Eric (US Male - Crisp)"},
    {"name": "en-GB-RyanNeural", "gender": "Male", "locale": "en-GB", "label": "Ryan (UK Male - Natural)"},
    {"name": "en-GB-SoniaNeural", "gender": "Female", "locale": "en-GB", "label": "Sonia (UK Female - Clear)"},
    {"name": "en-AU-NatNeural", "gender": "Female", "locale": "en-AU", "label": "Nat (AU Female)"},
    {"name": "en-AU-WilliamNeural", "gender": "Male", "locale": "en-AU", "label": "William (AU Male)"},
    {"name": "ja-JP-KeitaNeural", "gender": "Male", "locale": "ja-JP", "label": "Keita (Japanese Male)"},
    {"name": "ja-JP-NanamiNeural", "gender": "Female", "locale": "ja-JP", "label": "Nanami (Japanese Female)"},
    {"name": "es-ES-AlvaroNeural", "gender": "Male", "locale": "es-ES", "label": "Alvaro (Spanish Male)"},
    {"name": "fr-FR-HenriNeural", "gender": "Male", "locale": "fr-FR", "label": "Henri (French Male)"},
    {"name": "de-DE-ConradNeural", "gender": "Male", "locale": "de-DE", "label": "Conrad (German Male)"},
]


class EdgeTTSService:
    async def generate_audio(
        self,
        text: str,
        voice: str = VOICE,
        rate: str = "+0%",
        pitch: str = "+0Hz",
        volume: str = "+0%",
    ) -> bytes:
        v = voice or VOICE
        communicate = edge_tts.Communicate(text, v, rate=rate, pitch=pitch, volume=volume)
        audio = bytearray()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio += chunk["data"]
        return bytes(audio)

    async def list_voices(self) -> list[dict[str, Any]]:
        try:
            voices = await edge_tts.list_voices()
            return [
                {
                    "name": v.get("ShortName") or v.get("Name"),
                    "gender": v.get("Gender"),
                    "locale": v.get("Locale"),
                    "label": f"{v.get('ShortName')} ({v.get('Locale')}, {v.get('Gender')})",
                }
                for v in voices
            ]
        except Exception:
            return POPULAR_VOICES


edge_tts_service = EdgeTTSService()


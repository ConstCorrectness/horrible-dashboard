"""Text-to-speech for the Clubhouse voice agent, via Microsoft Edge's TTS.

Part of the optional ``voice`` extra. This replaced a local XTTS-v2 runner:
XTTS held a model in VRAM alongside the local LLM (and OOM'd against it), and
took seconds per utterance — dead air in a live room. Edge TTS is a network
call with no local weights, so it costs no VRAM and starts speaking at once.
The trade is that it is not local: utterances leave the machine.
"""

import collections
import logging
import re
from typing import Any

import edge_tts

logger = logging.getLogger(__name__)

VOICE = "en-US-ChristopherNeural"

POPULAR_VOICES = [
    {
        "name": "en-US-AndrewMultilingualNeural",
        "gender": "Male",
        "locale": "en-US",
        "label": "Andrew (US Male - Ultra-Natural & Warm)",
    },
    {
        "name": "en-US-AvaMultilingualNeural",
        "gender": "Female",
        "locale": "en-US",
        "label": "Ava (US Female - Ultra-Natural & Conversational)",
    },
    {
        "name": "en-US-BrianMultilingualNeural",
        "gender": "Male",
        "locale": "en-US",
        "label": "Brian (US Male - Engaging & Friendly)",
    },
    {
        "name": "en-US-EmmaMultilingualNeural",
        "gender": "Female",
        "locale": "en-US",
        "label": "Emma (US Female - Cheerful & Clear)",
    },
    {
        "name": "en-US-ChristopherNeural",
        "gender": "Male",
        "locale": "en-US",
        "label": "Christopher (US Male - Warm & Authoritative)",
    },
    {
        "name": "en-US-JennyNeural",
        "gender": "Female",
        "locale": "en-US",
        "label": "Jenny (US Female - Clear & Conversational)",
    },
    {
        "name": "en-US-GuyNeural",
        "gender": "Male",
        "locale": "en-US",
        "label": "Guy (US Male - Casual & Friendly)",
    },
    {
        "name": "en-US-AriaNeural",
        "gender": "Female",
        "locale": "en-US",
        "label": "Aria (US Female - Expressive)",
    },
    {
        "name": "en-US-EricNeural",
        "gender": "Male",
        "locale": "en-US",
        "label": "Eric (US Male - Crisp)",
    },
    {
        "name": "en-GB-RyanNeural",
        "gender": "Male",
        "locale": "en-GB",
        "label": "Ryan (UK Male - Natural)",
    },
    {
        "name": "en-GB-SoniaNeural",
        "gender": "Female",
        "locale": "en-GB",
        "label": "Sonia (UK Female - Clear)",
    },
    {
        "name": "en-AU-NatNeural",
        "gender": "Female",
        "locale": "en-AU",
        "label": "Nat (AU Female)",
    },
    {
        "name": "en-AU-WilliamNeural",
        "gender": "Male",
        "locale": "en-AU",
        "label": "William (AU Male)",
    },
    {
        "name": "en-IN-NeerjaNeural",
        "gender": "Female",
        "locale": "en-IN",
        "label": "Neerja (IN Female - Professional)",
    },
    {
        "name": "ja-JP-KeitaNeural",
        "gender": "Male",
        "locale": "ja-JP",
        "label": "Keita (Japanese Male)",
    },
    {
        "name": "ja-JP-NanamiNeural",
        "gender": "Female",
        "locale": "ja-JP",
        "label": "Nanami (Japanese Female)",
    },
    {
        "name": "es-ES-AlvaroNeural",
        "gender": "Male",
        "locale": "es-ES",
        "label": "Alvaro (Spanish Male)",
    },
    {
        "name": "fr-FR-HenriNeural",
        "gender": "Male",
        "locale": "fr-FR",
        "label": "Henri (French Male)",
    },
    {
        "name": "de-DE-ConradNeural",
        "gender": "Male",
        "locale": "de-DE",
        "label": "Conrad (German Male)",
    },
]

# In-memory LRU cache to eliminate duplicate network calls for repeated or short utterances
_CACHE_MAX_SIZE = 256
_audio_cache: collections.OrderedDict[tuple[str, str, str, str, str], bytes] = (
    collections.OrderedDict()
)


def sanitize_text_for_tts(text: str) -> str:
    """Strip markdown formatting, URLs, code blocks, and symbols that sound unnatural when read aloud."""
    if not text:
        return ""
    # 1. Replace multi-line code blocks with brief spoken marker or remove
    text = re.sub(r"```[\s\S]*?```", " [code snippet omitted] ", text)
    # 2. Strip inline code backticks: `variable` -> variable
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # 3. Replace markdown links [label](url) -> label
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # 4. Strip bare URLs
    text = re.sub(r"https?://\S+", "", text)
    # 5. Strip markdown bold / italics / strikethrough (**, *, __, _, ~~)
    text = re.sub(r"(\*\*|__)(.*?)\1", r"\2", text)
    text = re.sub(r"(\*|_)(.*?)\1", r"\2", text)
    text = re.sub(r"~~(.*?)~~", r"\1", text)
    # 6. Strip markdown headers (#, ##) and blockquote / list symbols at start of lines
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^>\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\d+\.\s+", "", text, flags=re.MULTILINE)
    # 7. Strip common emoji ranges that cause audio glitches or weird letter spelling
    text = re.sub(
        r"[\U00010000-\U0010ffff\u2600-\u27bf\u2300-\u23ff\u2b50]",
        "",
        text,
    )
    # 8. Normalize multiple spaces and line breaks
    text = re.sub(r"\s+", " ", text).strip()
    return text


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
        r = rate or "+0%"
        p = pitch or "+0Hz"
        vol = volume or "+0%"
        clean = sanitize_text_for_tts(text)
        if not clean:
            return b""

        cache_key = (clean, v, r, p, vol)
        if cache_key in _audio_cache:
            _audio_cache.move_to_end(cache_key)
            return _audio_cache[cache_key]

        try:
            communicate = edge_tts.Communicate(clean, v, rate=r, pitch=p, volume=vol)
            audio = bytearray()
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio += chunk["data"]
            res = bytes(audio)
            if res:
                _audio_cache[cache_key] = res
                if len(_audio_cache) > _CACHE_MAX_SIZE:
                    _audio_cache.popitem(last=False)
            return res
        except Exception as exc:
            logger.warning("Edge TTS synthesis failed for voice %s: %s", v, exc)
            return b""

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

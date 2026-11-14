"""Unsloth Desktop's speech and saved (cloned) voices, for the agent's TTS.

Unsloth's local server (the `unsloth` provider in `providers.py`) serves an
OpenAI-shaped `POST /v1/audio/speech`, plus a library of **saved voices**: a clip
uploaded once (`POST /v1/audio/inputs`) and turned into a voice
(`POST /v1/audio/voices`) that speech can then speak in. That is local voice
cloning, so it never sends a sentence off the machine, unlike Edge TTS.

The seam is the voice *name*. A cloned voice appears in the TTS voice list as
``unsloth:<id>``, and `/api/agent/tts` routes on that prefix. So the Clubhouse
pane, which only ever passes a voice name, speaks a cloned voice with no change.

Every call borrows the chat provider's endpoint and key: one Unsloth, one key.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx

from backend.modules.agent import providers as P
from backend.modules.telemetry.instrument import instrumented_client

VOICE_PREFIX = "unsloth:"

#: Unsloth's own ids are short tokens. Checked here before one lands in a URL
#: path, so a crafted "voice" cannot walk to another Unsloth route.
_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

#: First speech after a model switch loads the TTS weights, which takes a while.
#: A sentence after that is quick. Bounded anyway: a hung Unsloth must not hold a
#: Clubhouse turn forever.
SPEECH_TIMEOUT = httpx.Timeout(180, connect=2)
#: Listing and file reads. Short connect: the settings page and the Clubhouse
#: voice picker both ask, and a closed port on Windows otherwise costs ~2s.
LIST_TIMEOUT = httpx.Timeout(10, connect=0.75)
UPLOAD_TIMEOUT = httpx.Timeout(120, connect=2)

#: Replaced in tests with a client on an `httpx.MockTransport`.
_client_factory = instrumented_client


@dataclass
class UnslothAudioError(Exception):
    """A failure worth showing the user as is. `status` is the one our route
    should answer with: 503 for "not running", 502 for "running but refused"."""

    status: int
    detail: str

    def __str__(self) -> str:
        return self.detail


def is_unsloth_voice(voice: str | None) -> bool:
    return bool(voice) and str(voice).startswith(VOICE_PREFIX)


def voice_id(voice: str) -> str:
    """The Unsloth id inside ``unsloth:<id>``, or a 400 when it is not one."""
    raw = voice[len(VOICE_PREFIX) :] if voice.startswith(VOICE_PREFIX) else voice
    if not _ID.match(raw):
        raise UnslothAudioError(400, f"Not an Unsloth voice id: {raw!r}")
    return raw


def rate_to_speed(rate: str | None) -> float | None:
    """Edge's ``"+10%"`` rate as Unsloth's ``speed`` multiplier (1.1). None for
    no change, so an untouched rate sends nothing and the model keeps its default."""
    match = re.fullmatch(r"\s*([+-]?\d+(?:\.\d+)?)\s*%\s*", rate or "")
    if not match:
        return None
    pct = float(match.group(1))
    if pct == 0:
        return None
    return round(min(4.0, max(0.25, 1 + pct / 100)), 3)


def endpoint() -> str:
    # Lazy: `routes` imports this module, and the endpoint rules live there.
    from backend.modules.agent.routes import _endpoint_for, _load_config

    return _endpoint_for(P.PROVIDERS["unsloth"], _load_config()).rstrip("/")


def _detail(res: httpx.Response) -> str:
    try:
        body = res.json()
    except ValueError:
        return res.text[:300] or f"HTTP {res.status_code}"
    detail = body.get("detail") if isinstance(body, dict) else None
    # OpenAI-shaped errors, sometimes nested inside `detail`:
    # {"error": {"message": "No model loaded.", ...}}
    for holder in (detail, body):
        err = holder.get("error") if isinstance(holder, dict) else None
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])[:300]
    if isinstance(detail, list):  # FastAPI validation errors
        return "; ".join(str(d.get("msg", d)) for d in detail if isinstance(d, dict))
    return str(detail or body)[:300]


async def _request(
    method: str, path: str, *, timeout: httpx.Timeout = LIST_TIMEOUT, **kwargs: Any
) -> httpx.Response:
    base = endpoint()
    headers = {**P.auth_headers(P.PROVIDERS["unsloth"]), **kwargs.pop("headers", {})}
    try:
        async with _client_factory(timeout=timeout) as client:
            res = await client.request(
                method, f"{base}{path}", headers=headers, **kwargs
            )
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        # ConnectTimeout first, because it is also a TimeoutException: on Windows a
        # closed loopback port is retried by the OS until the connect timeout, so
        # "not running" arrives as a timeout, not a refusal.
        raise UnslothAudioError(
            503, f"Unsloth isn't reachable at {base}. Is Unsloth Desktop running?"
        ) from exc
    except httpx.TimeoutException as exc:
        raise UnslothAudioError(
            504, f"Unsloth did not answer in time ({base})."
        ) from exc
    except httpx.TransportError as exc:
        raise UnslothAudioError(
            503, f"Unsloth isn't reachable at {base}. Is Unsloth Desktop running?"
        ) from exc
    if res.status_code in (401, 403):
        raise UnslothAudioError(
            502,
            "Unsloth refused the request: save its API key under Settings → Model "
            "provider API keys (create one in Unsloth → Settings → API).",
        )
    if res.status_code == 404:
        raise UnslothAudioError(404, f"Unsloth: {_detail(res)}")
    if res.status_code in (400, 413, 422):
        raise UnslothAudioError(400, f"Unsloth: {_detail(res)}")
    if res.is_error:
        raise UnslothAudioError(502, f"Unsloth: {_detail(res)}")
    return res


def _voice(raw: dict[str, Any]) -> dict[str, Any]:
    """The fields the UI shows. Unsloth's own `url` is dropped: the browser reaches
    the clip through our proxy, which carries the key it would not have."""
    return {
        "id": str(raw.get("id", "")),
        "name": str(raw.get("name", "")),
        "transcript": raw.get("transcript"),
        "language": raw.get("language"),
        "duration_s": raw.get("duration_s"),
        "created_at": raw.get("created_at"),
    }


async def list_voices() -> list[dict[str, Any]]:
    res = await _request("GET", "/v1/audio/voices")
    return [_voice(v) for v in res.json().get("voices") or [] if isinstance(v, dict)]


async def tts_voice_options() -> list[dict[str, str]]:
    """Saved voices in the TTS picker's shape. Empty, never an error, when Unsloth
    is off: it is one source among several, and the list must still load."""
    try:
        voices = await list_voices()
    except UnslothAudioError:
        return []
    return [
        {
            "name": f"{VOICE_PREFIX}{v['id']}",
            "label": f"{v['name']} (cloned, Unsloth)",
            "locale": v.get("language") or "",
            "gender": "",
        }
        for v in voices
        if v["id"]
    ]


async def create_voice(
    audio: bytes,
    *,
    filename: str,
    content_type: str | None,
    name: str,
    transcript: str | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    """Upload a recording and save it as a voice. Unsloth decodes any container
    (the browser's WebM/Opus included) to WAV on upload."""
    if not audio:
        raise UnslothAudioError(400, "The recording is empty.")
    upload = await _request(
        "POST",
        "/v1/audio/inputs",
        params={"name": filename[:255] or "voice"},
        content=audio,
        headers={"Content-Type": content_type or "application/octet-stream"},
        timeout=UPLOAD_TIMEOUT,
    )
    input_id = str(upload.json().get("id") or "")
    if not input_id:
        raise UnslothAudioError(502, "Unsloth accepted the upload but returned no id.")
    body: dict[str, Any] = {"source": {"input_id": input_id}, "name": name.strip()[:80]}
    if transcript and transcript.strip():
        body["transcript"] = transcript.strip()[:4000]
    if language and language.strip():
        body["language"] = language.strip()[:64]
    res = await _request("POST", "/v1/audio/voices", json=body, timeout=UPLOAD_TIMEOUT)
    return _voice(res.json())


async def delete_voice(voice: str) -> None:
    await _request("DELETE", f"/v1/audio/voices/{voice_id(voice)}")


async def voice_audio(voice: str) -> tuple[bytes, str]:
    """The saved reference clip, for the settings page's play button."""
    res = await _request(
        "GET", f"/v1/audio/voices/{voice_id(voice)}/file", timeout=UPLOAD_TIMEOUT
    )
    return res.content, res.headers.get("content-type", "audio/wav")


async def speak(text: str, voice: str, rate: str | None = None) -> bytes:
    """Speak ``text`` in a saved voice, as WAV.

    `model` is sent only when `agent.unslothTtsModel` names one. Left empty, Unsloth
    uses whatever speech model it has loaded.
    """
    from backend.modules.settings.routes import get_value

    body: dict[str, Any] = {
        "input": text,
        "voice": {"id": voice_id(voice)},
        "response_format": "wav",
    }
    speed = rate_to_speed(rate)
    if speed is not None:
        body["speed"] = speed
    model = str(get_value("agent.unslothTtsModel", "") or "").strip()
    if model:
        body["model"] = model
    try:
        res = await _request(
            "POST", "/v1/audio/speech", json=body, timeout=SPEECH_TIMEOUT
        )
    except UnslothAudioError as exc:
        if not model and "no model loaded" in exc.detail.lower():
            # Unsloth speaks with the resident model when none is named, and there
            # often is none. Say which setting fixes it.
            raise UnslothAudioError(
                exc.status,
                "Unsloth has no speech model loaded. Set Settings → Agent → "
                '"Unsloth speech model" (for example '
                "audio-cpp/audio.cpp-gguf/DotTTS-Edit-GGUF), or load one in Unsloth.",
            ) from exc
        raise
    if not res.content:
        # An empty 200 reaches the browser as "Unable to decode audio data", which
        # says nothing about why (see test_agent_tts.py).
        raise UnslothAudioError(502, "Unsloth returned no audio.")
    return res.content

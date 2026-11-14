"""Cloned voices through Unsloth Desktop: `/api/agent/tts` and `/api/agent/voices/unsloth`.

Unsloth is faked with an `httpx.MockTransport` behind `unsloth_audio._client_factory`,
so these run with no Unsloth and no `voice` extra installed.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.agent import unsloth_audio as UA

WAV = b"RIFF\x24\x00\x00\x00WAVEfmt "


class FakeUnsloth:
    """Records every request and answers like Unsloth's `/v1/audio/*` routes."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.voices: list[dict[str, Any]] = [
            {
                "id": "v1",
                "name": "Rob",
                "transcript": "hi",
                "language": "en",
                "duration_s": 8.5,
                "sample_rate": 24000,
                "created_at": "2026-11-13T00:00:00Z",
                "url": "/v1/audio/voices/v1/file?sig=x",
            }
        ]
        self.status: int | None = None  # force every answer to this status

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status is not None:
            return httpx.Response(self.status, json={"detail": "nope"})
        path, method = request.url.path, request.method
        if path == "/v1/audio/voices" and method == "GET":
            return httpx.Response(200, json={"voices": self.voices})
        if path == "/v1/audio/inputs" and method == "POST":
            return httpx.Response(201, json={"id": "in1", "name": "x", "url": "/u"})
        if path == "/v1/audio/voices" and method == "POST":
            body = json.loads(request.content)
            voice = {**self.voices[0], "id": "v2", "name": body["name"]}
            return httpx.Response(201, json=voice)
        if path == "/v1/audio/speech":
            return httpx.Response(
                200, content=WAV, headers={"content-type": "audio/wav"}
            )
        if path == "/v1/audio/voices/v1/file":
            return httpx.Response(
                200, content=WAV, headers={"content-type": "audio/wav"}
            )
        if path == "/v1/audio/voices/v1" and method == "DELETE":
            return httpx.Response(200, json={"removed": True})
        return httpx.Response(404, json={"detail": "Voice not found."})


@pytest.fixture
def fake(monkeypatch) -> FakeUnsloth:
    from backend.modules.database import secrets_store
    from backend.modules.settings import routes as settings_routes

    unsloth = FakeUnsloth()
    transport = httpx.MockTransport(unsloth.handler)
    real = httpx.AsyncClient
    monkeypatch.setattr(
        UA, "_client_factory", lambda **kw: real(transport=transport, **kw)
    )
    monkeypatch.setattr(UA, "endpoint", lambda: "http://127.0.0.1:8888")
    monkeypatch.setattr(secrets_store, "get_secret_or_none", lambda _name: None)
    monkeypatch.setenv("UNSLOTH_API_KEY", "sk-unsloth-test")
    monkeypatch.setattr(
        settings_routes, "get_value", lambda _key, default=None: default
    )
    return unsloth


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_cloned_voice_is_spoken_by_unsloth_as_wav(fake: FakeUnsloth, client) -> None:
    """`unsloth:<id>` is the whole switch: the Clubhouse pane passes a voice name
    and gets audio, whichever engine made it."""
    res = client.get(
        "/api/agent/tts",
        params={"text": "hello", "voice": "unsloth:v1", "rate": "+10%"},
    )
    assert res.status_code == 200
    assert res.headers["content-type"] == "audio/wav"
    assert res.content == WAV

    (req,) = fake.requests
    assert req.url.path == "/v1/audio/speech"
    assert req.headers["authorization"] == "Bearer sk-unsloth-test"
    body = json.loads(req.content)
    assert body == {
        "input": "hello",
        "voice": {"id": "v1"},
        "response_format": "wav",
        "speed": 1.1,
    }


def test_voice_list_merges_cloned_voices_first(fake: FakeUnsloth, client) -> None:
    voices = client.get("/api/agent/tts/voices").json()
    assert voices[0]["name"] == "unsloth:v1"
    assert "cloned" in voices[0]["label"]
    # Edge voices (or the static fallback) still follow.
    assert any(not v["name"].startswith("unsloth:") for v in voices)


def test_voice_list_survives_unsloth_being_off(fake: FakeUnsloth, client) -> None:
    fake.status = 503
    voices = client.get("/api/agent/tts/voices").json()
    assert voices and not any(v["name"].startswith("unsloth:") for v in voices)


def test_create_voice_uploads_then_saves(fake: FakeUnsloth, client) -> None:
    res = client.post(
        "/api/agent/voices/unsloth",
        files={"file": ("rec.webm", b"opus-bytes", "audio/webm")},
        data={"name": "Rob 2", "transcript": " what I said ", "language": ""},
    )
    assert res.status_code == 201, res.text
    assert res.json()["id"] == "v2"
    assert "url" not in res.json()  # Unsloth's URL needs the key; the proxy serves it

    upload, create = fake.requests
    assert upload.url.path == "/v1/audio/inputs"
    assert upload.url.params["name"] == "rec.webm"
    assert upload.headers["content-type"] == "audio/webm"
    assert upload.content == b"opus-bytes"
    assert json.loads(create.content) == {
        "source": {"input_id": "in1"},
        "name": "Rob 2",
        "transcript": "what I said",
    }


def test_listing_reports_unreachable_as_a_state(fake: FakeUnsloth, client) -> None:
    fake.status = 401
    body = client.get("/api/agent/voices/unsloth").json()
    assert body["reachable"] is False
    assert "API key" in body["error"]


def test_refused_key_on_speech_is_a_502_with_a_reason(
    fake: FakeUnsloth, client
) -> None:
    fake.status = 401
    res = client.get("/api/agent/tts", params={"text": "x", "voice": "unsloth:v1"})
    assert res.status_code == 502
    assert "API key" in res.json()["detail"]


def test_clip_and_delete_proxy(fake: FakeUnsloth, client) -> None:
    clip = client.get("/api/agent/voices/unsloth/v1/audio")
    assert clip.status_code == 200 and clip.content == WAV
    assert client.delete("/api/agent/voices/unsloth/v1").json() == {"removed": True}
    assert client.delete("/api/agent/voices/unsloth/nope").status_code == 404


def test_a_crafted_voice_id_never_reaches_unsloth(fake: FakeUnsloth, client) -> None:
    res = client.get(
        "/api/agent/tts", params={"text": "x", "voice": "unsloth:../../v1/models"}
    )
    assert res.status_code == 400
    assert fake.requests == []


@pytest.mark.parametrize(
    ("rate", "speed"),
    [
        ("+0%", None),
        ("", None),
        ("+50%", 1.5),
        ("-25%", 0.75),
        ("+900%", 4.0),
        ("fast", None),
    ],
)
def test_edge_rate_maps_to_unsloth_speed(rate: str, speed: float | None) -> None:
    assert UA.rate_to_speed(rate) == speed


def test_a_closed_port_reads_as_not_running(monkeypatch, client) -> None:
    """On Windows a closed loopback port arrives as a connect *timeout*. It must
    say "not running", not "did not answer in time"."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    real = httpx.AsyncClient
    monkeypatch.setattr(
        UA,
        "_client_factory",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    monkeypatch.setattr(UA, "endpoint", lambda: "http://127.0.0.1:8888")
    res = client.get("/api/agent/tts", params={"text": "x", "voice": "unsloth:v1"})
    assert res.status_code == 503
    assert "running" in res.json()["detail"]


def test_no_speech_model_names_the_setting(
    fake: FakeUnsloth, client, monkeypatch
) -> None:
    """Live Unsloth with nothing resident answers an OpenAI-shaped error nested in
    `detail`. It must reach the user as one readable line naming the fix."""
    original = fake.handler

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/audio/speech":
            return httpx.Response(
                400,
                json={
                    "detail": {
                        "error": {
                            "message": "No model loaded.",
                            "type": "invalid_request_error",
                        }
                    }
                },
            )
        return original(request)

    fake.handler = handler  # type: ignore[method-assign]
    real = httpx.AsyncClient
    monkeypatch.setattr(
        UA,
        "_client_factory",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    res = client.get("/api/agent/tts", params={"text": "x", "voice": "unsloth:v1"})
    assert res.status_code == 400
    detail = res.json()["detail"]
    assert "no speech model loaded" in detail
    assert "Unsloth speech model" in detail
    assert "{" not in detail  # no raw dict leaking through


def test_configured_speech_model_is_sent(
    fake: FakeUnsloth, client, monkeypatch
) -> None:
    from backend.modules.settings import routes as settings_routes

    monkeypatch.setattr(
        settings_routes,
        "get_value",
        lambda key, default=None: (
            "audio-cpp/audio.cpp-gguf/DotTTS-Edit-GGUF"
            if key == "agent.unslothTtsModel"
            else default
        ),
    )
    assert (
        client.get(
            "/api/agent/tts", params={"text": "x", "voice": "unsloth:v1"}
        ).status_code
        == 200
    )
    assert json.loads(fake.requests[-1].content)["model"].endswith("DotTTS-Edit-GGUF")

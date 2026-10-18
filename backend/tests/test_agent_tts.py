"""`/api/agent/tts` must fail loudly, never with an empty 200.

An empty ``audio/mpeg`` body reaches the browser as "Unable to decode audio
data" — which is how a Microsoft-side 403 (edge-tts pinned to a stale Edge
version) surfaced in the Clubhouse voice preview.
"""

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("edge_tts")

from backend.app import app  # noqa: E402
from backend.modules.agent import edge_tts_service as svc  # noqa: E402


class _Communicate:
    def __init__(self, *args, chunks=(), error=None, **kwargs) -> None:
        self._chunks = chunks
        self._error = error

    async def stream(self):
        if self._error:
            raise self._error
        for chunk in self._chunks:
            yield chunk


@pytest.fixture(autouse=True)
def _empty_cache(monkeypatch) -> None:
    monkeypatch.setattr(svc, "_audio_cache", type(svc._audio_cache)())


def _patch_communicate(monkeypatch, **behaviour) -> None:
    monkeypatch.setattr(
        svc.edge_tts, "Communicate", lambda *a, **k: _Communicate(*a, **behaviour, **k)
    )


def test_synthesis_error_is_a_502_with_the_cause(monkeypatch) -> None:
    _patch_communicate(
        monkeypatch, error=RuntimeError("403, message='Invalid response status'")
    )
    res = TestClient(app).get("/api/agent/tts", params={"text": "hello"})
    assert res.status_code == 502
    assert "403" in res.json()["detail"]


def test_no_audio_chunks_is_a_502(monkeypatch) -> None:
    _patch_communicate(monkeypatch, chunks=[{"type": "WordBoundary"}])
    res = TestClient(app).get("/api/agent/tts", params={"text": "hello"})
    assert res.status_code == 502


def test_audio_is_returned_as_mpeg(monkeypatch) -> None:
    _patch_communicate(monkeypatch, chunks=[{"type": "audio", "data": b"\xff\xf3mp3"}])
    res = TestClient(app).get("/api/agent/tts", params={"text": "hello"})
    assert res.status_code == 200
    assert res.headers["content-type"] == "audio/mpeg"
    assert res.content == b"\xff\xf3mp3"

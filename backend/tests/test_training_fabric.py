"""Training peer fabric: ad ingestion (valid/forged/retraction), specs snapshot,
browser fanout, and the advertise routes."""

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.training import fabric, specs
from backend.modules.training.models import TrainingAdModel


@pytest.fixture(autouse=True)
def clear_ads():
    fabric._ads.clear()
    yield
    fabric._ads.clear()


def _session(node_id: str, name: str = "peer"):
    return SimpleNamespace(info=SimpleNamespace(node_id=node_id, node_name=name))


def _env(data: dict):
    return SimpleNamespace(data=data)


def _ad_payload(node_id: str, status: str = "offering") -> dict:
    return TrainingAdModel(
        node_id=node_id,
        node_name="Peer",
        status=status,
        specs={"gpu": "RTX 4090", "vram_gb": 24.0},
        note="free evenings",
        ts=1.0,
    ).model_dump()


def test_on_ad_stores_and_fans_out(monkeypatch):
    sent: list = []
    monkeypatch.setattr(fabric, "broadcast", _async_capture(sent))

    asyncio.run(fabric._on_ad(None, _session("peerA"), _env(_ad_payload("peerA"))))
    ads = fabric.known_ads()
    assert len(ads) == 1 and ads[0].node_id == "peerA"
    assert ads[0].specs["gpu"] == "RTX 4090"
    assert sent and sent[0][0] == "training_ad"


def test_on_ad_pins_node_id_to_sender(monkeypatch):
    """A peer can't advertise on behalf of a different node."""
    monkeypatch.setattr(fabric, "broadcast", _async_capture([]))
    # Payload claims to be "victim", but it arrives over the "attacker" session.
    asyncio.run(fabric._on_ad(None, _session("attacker"), _env(_ad_payload("victim"))))
    ads = fabric.known_ads()
    assert [a.node_id for a in ads] == ["attacker"]


def test_on_ad_retraction_removes(monkeypatch):
    monkeypatch.setattr(fabric, "broadcast", _async_capture([]))
    asyncio.run(fabric._on_ad(None, _session("peerB"), _env(_ad_payload("peerB"))))
    assert len(fabric.known_ads()) == 1
    asyncio.run(
        fabric._on_ad(
            None, _session("peerB"), _env(_ad_payload("peerB", status="none"))
        )
    )
    assert fabric.known_ads() == []


def test_on_ad_ignores_malformed(monkeypatch):
    monkeypatch.setattr(fabric, "broadcast", _async_capture([]))
    asyncio.run(
        fabric._on_ad(None, _session("peerC"), _env({"status": "bogus-status"}))
    )
    assert fabric.known_ads() == []


def test_register_wires_handler_and_subscriber():
    handlers: dict = {}
    subs: list = []
    hub = SimpleNamespace(
        register_handler=lambda t, h: handlers.__setitem__(t, h),
        subscribe=lambda cb: subs.append(cb),
    )
    fabric.register(hub)
    assert fabric.TRAINING_AD in handlers
    assert len(subs) == 1


def test_specs_snapshot_shape(monkeypatch):
    # Mock nvidia-smi to a known GPU line.
    import backend.modules.training.specs as specs_mod

    monkeypatch.setattr(specs_mod.shutil, "which", lambda name: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(
        specs_mod.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="RTX 4090, 24564\n"),
    )
    snap = specs.snapshot()
    assert snap["gpu"] == "RTX 4090"
    assert snap["vram_gb"] == pytest.approx(24.0, abs=0.1)
    assert "platform" in snap and "cpu_count" in snap


def test_specs_no_gpu(monkeypatch):
    import backend.modules.training.specs as specs_mod

    monkeypatch.setattr(specs_mod.shutil, "which", lambda name: None)
    snap = specs.snapshot()
    assert snap["gpus"] == [] and snap["gpu"] is None


def test_advertise_route(monkeypatch, tmp_path):
    settings = Path(os.environ["HORRIBLE_DATA_DIR"]) / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text("{}")

    async def fake_broadcast_ad(hub):
        fake_broadcast_ad.called = True

    fake_broadcast_ad.called = False
    monkeypatch.setattr(fabric, "broadcast_ad", fake_broadcast_ad)

    client = TestClient(app)
    res = client.post(
        "/api/training/fabric/advertise", json={"status": "offering", "note": "hi"}
    )
    assert res.status_code == 200 and res.json()["status"] == "offering"
    stored = json.loads(settings.read_text())
    assert stored["training.fabric.advertise"] == "offering"
    assert stored["training.fabric.note"] == "hi"
    assert fake_broadcast_ad.called

    bad = client.post("/api/training/fabric/advertise", json={"status": "nonsense"})
    assert bad.status_code == 400


def test_ads_route(monkeypatch):
    monkeypatch.setattr(fabric, "broadcast", _async_capture([]))
    asyncio.run(fabric._on_ad(None, _session("peerD"), _env(_ad_payload("peerD"))))
    client = TestClient(app)
    res = client.get("/api/training/fabric/ads")
    assert res.status_code == 200
    ads = res.json()["ads"]
    assert any(a["node_id"] == "peerD" for a in ads)


def _async_capture(sink: list):
    async def _broadcast(event, data):
        sink.append((event, data))

    return _broadcast

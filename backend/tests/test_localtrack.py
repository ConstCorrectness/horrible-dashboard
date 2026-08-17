"""Unit and integration tests for LocalTrack backend, downsampling, and client logger."""

from __future__ import annotations

import math
import os
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.localtrack import store
from backend.modules.localtrack.downsampling import ema_smooth, lttb
from backend.modules.localtrack.models import MetricLogItem
from backend.sdk.localtrack.base import BaseLocalTrackLogger
from backend.sdk.localtrack.client import LocalTrackClient


@pytest.fixture(autouse=True)
def isolated_localtrack_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point LocalTrack storage to an isolated tmp directory for every test."""
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    store.init_db()
    yield tmp_path


def test_lttb_downsampling():
    # Generate 1000 points of a sine wave
    raw = [(float(i), math.sin(i * 0.05)) for i in range(1000)]
    downsampled = lttb(raw, 50)

    assert len(downsampled) == 50
    # First and last points must match
    assert downsampled[0] == raw[0]
    assert downsampled[-1] == raw[-1]
    # Steps must be strictly increasing
    steps = [p[0] for p in downsampled]
    assert steps == sorted(steps)


def test_ema_smoothing():
    raw = [10.0, 1.0, 10.0, 1.0, 10.0]
    smoothed = ema_smooth(raw, weight=0.8)
    assert len(smoothed) == len(raw)
    # The variance should be smoothed out
    assert smoothed[1] > raw[1]
    assert smoothed[2] < raw[2]


def test_project_and_run_crud():
    client = TestClient(app)

    # 1. Create project
    proj_resp = client.post(
        "/api/localtrack/projects",
        json={"id": "test-proj", "name": "Test Project", "description": "A test project"},
    )
    assert proj_resp.status_code == 200
    pdata = proj_resp.json()
    assert pdata["id"] == "test-proj"
    assert pdata["name"] == "Test Project"

    # 2. List projects
    list_resp = client.get("/api/localtrack/projects")
    assert list_resp.status_code == 200
    projects = list_resp.json()["projects"]
    assert any(p["id"] == "test-proj" for p in projects)

    # 3. Create run
    run_resp = client.post(
        "/api/localtrack/runs",
        json={
            "project_id": "test-proj",
            "name": "celestial-lake-3",
            "config": {"lr": 0.0001, "batch_size": 32},
            "tags": ["baseline", "sft"],
        },
    )
    assert run_resp.status_code == 200
    rdata = run_resp.json()
    run_id = rdata["id"]
    assert rdata["name"] == "celestial-lake-3"
    assert rdata["status"] == "running"
    assert rdata["config"]["batch_size"] == 32

    # 4. Ingest metrics
    logs = [
        MetricLogItem(
            run_id=run_id,
            step=i,
            epoch=i / 100.0,
            metrics={"train/loss": 2.5 / (1.0 + i * 0.05) + (0.1 if i % 2 == 0 else -0.1), "eval/acc": min(0.95, i * 0.01)},
        )
        for i in range(1, 101)
    ]
    ingest_resp = client.post(
        "/api/localtrack/metrics/ingest",
        json={"logs": [item.model_dump() for item in logs]},
    )
    assert ingest_resp.status_code == 200
    assert ingest_resp.json()["ingested_count"] == 200

    # 5. Query metric keys
    keys_resp = client.get(f"/api/localtrack/metrics/keys?project_id=test-proj")
    assert keys_resp.status_code == 200
    keys = keys_resp.json()["keys"]
    assert "train/loss" in keys
    assert "eval/acc" in keys

    # 6. Query metrics with downsampling and smoothing
    q_resp = client.post(
        "/api/localtrack/metrics/query",
        json={
            "run_ids": [run_id],
            "keys": ["train/loss"],
            "max_points": 25,
            "smoothing": 0.5,
        },
    )
    assert q_resp.status_code == 200
    series = q_resp.json()["series"]
    assert len(series) == 1
    assert series[0]["run_id"] == run_id
    assert series[0]["key"] == "train/loss"
    assert len(series[0]["steps"]) <= 25
    assert series[0]["raw_point_count"] == 100

    # 7. Upload and download artifact
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json") as tf:
        tf.write('{"model": "test-llm", "hidden_size": 768}')
        tf_path = tf.name

    try:
        with open(tf_path, "rb") as f:
            art_resp = client.post(
                f"/api/localtrack/runs/{run_id}/artifacts",
                files={"file": ("config.json", f, "application/json")},
            )
        assert art_resp.status_code == 200
        art_id = art_resp.json()["id"]

        # List artifacts
        arts_resp = client.get(f"/api/localtrack/runs/{run_id}/artifacts")
        assert arts_resp.status_code == 200
        assert len(arts_resp.json()["artifacts"]) == 1

        # Download artifact
        dl_resp = client.get(f"/api/localtrack/runs/{run_id}/artifacts/{art_id}/download")
        assert dl_resp.status_code == 200
        assert b"hidden_size" in dl_resp.content
    finally:
        if os.path.exists(tf_path):
            os.remove(tf_path)

    # 8. Update run status to finished
    patch_resp = client.patch(
        f"/api/localtrack/runs/{run_id}",
        json={"status": "finished", "duration_seconds": 45.2},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["status"] == "finished"
    assert patch_resp.json()["duration_seconds"] == 45.2


def test_python_client_logger():
    # Verify LocalTrackClient works seamlessly with BaseLocalTrackLogger interface
    client = LocalTrackClient(base_url="http://testserver")
    assert isinstance(client, BaseLocalTrackLogger)

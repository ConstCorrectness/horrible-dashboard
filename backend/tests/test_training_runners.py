"""Script/manim runners and the media route."""

import json
import os
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.training import projects
from backend.modules.training.models import EnvironmentRefModel, ManimRequest
from backend.modules.training.runners.manim_runner import ManimRunner
from backend.modules.training.runners.script_runner import ScriptRunner


@pytest.fixture
def project(tmp_path):
    settings = Path(os.environ["HORRIBLE_DATA_DIR"]) / "settings.json"
    settings.write_text(
        json.dumps({"training.projectsRoot": str(tmp_path / "projects")})
    )
    return projects.create_project(
        "Runner Test",
        [EnvironmentRefModel(provider="gymnasium", kind="env", id="CartPole-v1")],
        "3.12",
    )


@pytest.fixture
def use_this_python(monkeypatch):
    """Run scripts with the test process's python instead of a project venv."""
    from backend.modules.training.runners import manim_runner as mr
    from backend.modules.training.runners import script_runner as sr

    monkeypatch.setattr(sr, "python_path", lambda p: Path(sys.executable))
    monkeypatch.setattr(sr, "venv_ready", lambda p: True)
    monkeypatch.setattr(mr.envs, "python_path", lambda p: Path(sys.executable))


def _wait(predicate, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.05)
    raise AssertionError("timed out")


def test_script_runner_streams_sentinels_and_output(
    project, use_this_python, monkeypatch
) -> None:
    from backend.modules.training.runners import script_runner as sr

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        sr, "broadcast_threadsafe", lambda ev, d: events.append((ev, d))
    )
    monkeypatch.setattr(sr, "record_event", lambda ev, d: events.append((ev, d)))

    script = Path(project.root) / "train.py"
    script.write_text(
        "import json, sys\n"
        "print('starting up')\n"
        'sys.stdout.write("@@HORRIBLE@@" + json.dumps('
        '{"type": "metric", "runId": "rX", "step": 1, "values": {"loss": 0.9}}) + "\\n")\n'
        "print('done')\n",
        encoding="utf-8",
    )
    runner = ScriptRunner()
    run = runner.start(project, "train.py")
    _wait(
        lambda: any(e == "run_state" and d.get("state") == "exited" for e, d in events)
    )
    assert run.returncode == 0

    metric = next(d for e, d in events if e == "metrics")
    assert metric["values"] == {"loss": 0.9} and metric["projectId"] == project.id
    lines = [d["line"] for e, d in events if e == "run_output"]
    assert "starting up" in lines and "done" in lines
    assert not any("@@HORRIBLE@@" in line for line in lines)


def test_script_runner_rejects_bad_paths(project, use_this_python) -> None:
    runner = ScriptRunner()
    with pytest.raises(ValueError, match="escapes project root"):
        runner.start(project, "../evil.py")
    with pytest.raises(ValueError, match="no such script"):
        runner.start(project, "missing.py")


def test_script_runner_stop(project, use_this_python, monkeypatch) -> None:
    from backend.modules.training.runners import script_runner as sr

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        sr, "broadcast_threadsafe", lambda ev, d: events.append((ev, d))
    )
    script = Path(project.root) / "forever.py"
    script.write_text("import time\nwhile True: time.sleep(0.2)\n", encoding="utf-8")
    runner = ScriptRunner()
    run = runner.start(project, "forever.py")
    assert run.running
    assert runner.stop(run.id) is True
    _wait(lambda: not run.running)
    assert runner.stop("nope") is False


def test_manim_finds_newest_output(tmp_path) -> None:
    media = tmp_path / "media"
    old = media / "videos" / "scenes" / "480p15" / "Scene.mp4"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"old")
    time.sleep(0.05)
    new = media / "videos" / "scenes" / "720p30" / "Scene.mp4"
    new.parent.mkdir(parents=True)
    new.write_bytes(b"new")
    os.utime(new, None)
    found = ManimRunner._find_output(media, "Scene")
    assert found == new
    assert ManimRunner._find_output(media, "Other") is None


def test_manim_scene_file_guard(project) -> None:
    runner = ManimRunner()
    with pytest.raises(ValueError, match="needs"):
        runner._scene_file(project, ManimRequest(scene="S"))
    with pytest.raises(ValueError, match="bad scene file"):
        runner._scene_file(project, ManimRequest(scene="S", file="../../outside.py"))
    path = runner._scene_file(project, ManimRequest(scene="S", source="class S: pass"))
    assert path.is_file() and path.name == "S.py"


def test_media_route_serves_and_guards(project) -> None:
    client = TestClient(app)
    video = Path(project.root) / "media" / "videos" / "out.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"mp4bytes")
    ok = client.get(f"/api/training/projects/{project.id}/media/videos/out.mp4")
    assert ok.status_code == 200 and ok.content == b"mp4bytes"

    secret = Path(project.root) / "project.json"
    assert secret.is_file()  # exists, but outside media/
    for evil in ("../project.json", "..%2Fproject.json", "videos/../../project.json"):
        res = client.get(f"/api/training/projects/{project.id}/media/{evil}")
        assert res.status_code == 404, evil


def test_runner_instances_exist() -> None:
    from backend.modules.training.runners.manim_runner import manim_runner
    from backend.modules.training.runners.script_runner import script_runner

    assert isinstance(script_runner, ScriptRunner)
    assert isinstance(manim_runner, ManimRunner)

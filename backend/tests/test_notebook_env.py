"""The notebook venv's libraries: what counts as missing, where torch comes from,
and the order things install in. uv is never run — `_run` is replaced and the
commands it would have run are asserted instead."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.modules.notebook import env


@pytest.fixture
def venv(tmp_path, monkeypatch) -> dict[str, Any]:
    """A managed venv that exists, settings from a dict, and a recorded uv."""
    root = tmp_path / "notebook-venv"
    python = env._venv_python(root)
    python.parent.mkdir(parents=True)
    python.write_text("")
    settings: dict[str, Any] = {}
    calls: list[list[str]] = []

    monkeypatch.setattr(env, "managed_venv_dir", lambda: root)
    monkeypatch.setattr(
        env, "get_value", lambda key, default=None: settings.get(key, default)
    )
    monkeypatch.setattr(env, "_uv", lambda: "uv")
    monkeypatch.setattr(env, "_run", lambda cmd, progress: calls.append(cmd))
    monkeypatch.setattr(env, "_profile", lambda: None)
    monkeypatch.setattr(
        env, "_state", {"state": "idle", "installing": [], "line": "", "error": ""}
    )
    return {"python": str(python), "settings": settings, "calls": calls}


def _profile(os_name: str, kind: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        os=os_name, primary=SimpleNamespace(kind=kind) if kind else None
    )


def test_the_default_environment_carries_the_common_ai_stack(venv) -> None:
    names = {env.package_name(s) for s in env.requested_packages()}
    assert {"torch", "transformers", "datasets", "numpy", "pandas"} <= names


def test_a_fresh_venv_is_missing_everything_configured(venv) -> None:
    assert env.missing_packages() == env.requested_packages()
    assert env.library_status()["state"] == "idle"


def test_windows_with_nvidia_gets_cuda_torch_and_nothing_else_does() -> None:
    assert env.auto_torch_index("windows", "cuda") == env.PYTORCH_CUDA_INDEX
    # Linux PyPI wheels already bundle CUDA; macOS wheels use Metal.
    assert env.auto_torch_index("linux", "cuda") is None
    assert env.auto_torch_index("darwin", "metal") is None
    assert env.auto_torch_index("windows", None) is None


def test_torch_installs_first_from_its_own_index(venv, monkeypatch) -> None:
    monkeypatch.setattr(env, "_profile", lambda: _profile("windows", "cuda"))
    env._install_worker()

    first, second = venv["calls"]
    assert first[:5] == ["uv", "pip", "install", "--python", venv["python"]]
    assert "torch" in first and "transformers" not in first
    assert first[-2:] == ["--index-url", env.PYTORCH_CUDA_INDEX]
    # transformers/accelerate only need *some* torch; run first they would pull the
    # CPU wheel from PyPI and the CUDA one would never be chosen.
    assert "transformers" in second and "--index-url" not in second

    assert env.missing_packages() == []
    assert env.library_status()["state"] == "ready"


def test_an_existing_venv_picks_up_a_package_added_later(venv) -> None:
    env._install_worker()
    venv["calls"].clear()
    venv["settings"]["notebook.python.packages"] = env.DEFAULT_PACKAGES + " polars"

    assert env.missing_packages() == ["polars"]
    env._install_worker()
    assert venv["calls"] == [
        ["uv", "pip", "install", "--python", venv["python"], "polars"]
    ]


def test_a_different_torch_index_reinstalls_torch(venv) -> None:
    env._install_worker()
    venv["settings"]["notebook.python.torchIndex"] = (
        "https://download.pytorch.org/whl/cpu"
    )
    assert env.missing_packages() == ["torch"]


def test_pypi_setting_forces_the_default_index(venv, monkeypatch) -> None:
    monkeypatch.setattr(env, "_profile", lambda: _profile("windows", "cuda"))
    venv["settings"]["notebook.python.torchIndex"] = "pypi"
    assert env.torch_index() is None


def test_a_failed_install_is_reported_and_retryable(venv, monkeypatch) -> None:
    def boom(cmd, progress):
        raise RuntimeError("uv exited with 1: No solution found for torch")

    monkeypatch.setattr(env, "_run", boom)
    env._install_worker()
    status = env.library_status()
    assert status["state"] == "failed"
    assert "No solution found" in status["error"]
    assert "torch" in status["missing"]


def test_a_torch_failure_after_nothing_leaves_the_rest_unstamped(
    venv, monkeypatch
) -> None:
    calls = venv["calls"]

    def second_fails(cmd, progress):
        calls.append(cmd)
        if "transformers" in cmd:
            raise RuntimeError("network")

    monkeypatch.setattr(env, "_run", second_fails)
    env._install_worker()
    # torch was stamped before the second stage failed, so a retry skips it.
    assert "torch" not in env.missing_packages()
    assert "transformers" in env.missing_packages()


def test_an_override_interpreter_is_never_installed_into(venv) -> None:
    venv["settings"]["notebook.python"] = sys.executable
    assert env.start_library_install() is False
    assert env.library_status()["state"] == "unmanaged"
    assert venv["calls"] == []


def test_package_names_ignore_versions_and_extras() -> None:
    assert env.package_name("torch>=2.4") == "torch"
    assert env.package_name("huggingface_hub[cli]") == "huggingface-hub"


def test_env_route_reports_libraries(venv) -> None:
    from fastapi.testclient import TestClient

    from backend.app import app

    body = TestClient(app).get("/api/notebook/env").json()
    assert body["ready"] is True
    assert body["libraries"]["state"] == "idle"
    assert "torch" in body["libraries"]["missing"]
    assert Path(venv["python"]).is_file()

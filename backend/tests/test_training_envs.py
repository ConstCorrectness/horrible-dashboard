"""Which torch a training project venv gets, and whether a wrong one is replaced."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.modules.notebook.env import PYTORCH_CUDA_INDEX
from backend.modules.training import envs
from backend.modules.training.models import ProjectModel


def _profile(kind: str | None, *, certain: bool = True) -> SimpleNamespace:
    primary = (
        None
        if kind is None
        else SimpleNamespace(kind=kind, name="Card", detected_by="probe")
    )
    return SimpleNamespace(certain=certain, primary=primary)


def test_windows_nvidia_gets_the_cuda_index() -> None:
    # PyPI's Windows wheels are CPU-only: the default here installed `+cpu` on an
    # RTX 4080 while reporting that it "already targets" the card.
    url, why = envs.torch_index_url(_profile("cuda"), os_name="win32")
    assert url == PYTORCH_CUDA_INDEX
    assert "CPU-only" in why


def test_linux_nvidia_keeps_the_default_wheel() -> None:
    url, _ = envs.torch_index_url(_profile("cuda"), os_name="linux")
    assert url == ""


def test_uncertain_probe_on_windows_still_avoids_the_cpu_wheel() -> None:
    url, _ = envs.torch_index_url(_profile(None, certain=False), os_name="win32")
    assert url == PYTORCH_CUDA_INDEX
    url, _ = envs.torch_index_url(None, os_name="win32")
    assert url == PYTORCH_CUDA_INDEX


def test_certain_no_accelerator_gets_the_cpu_index() -> None:
    url, _ = envs.torch_index_url(_profile(None), os_name="win32")
    assert url.endswith("/cpu")


def _project(tmp_path: Path) -> ProjectModel:
    return ProjectModel(id="p", name="p", root=str(tmp_path), refs=[], python="3.12")


def _write_torch(tmp_path: Path, version: str) -> None:
    site = tmp_path / ".venv" / "Lib" / "site-packages"
    (site / "torch").mkdir(parents=True)
    (site / "torch" / "version.py").write_text(
        f"__version__ = '{version}'\ncuda = None\n", encoding="utf-8"
    )
    # The dist-info says plain 2.14.0 for the CPU build — which is why it is not
    # what the version is read from.
    info = site / "torch-2.14.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Version: 2.14.0\n", encoding="utf-8")


def test_installed_version_carries_the_local_label(tmp_path: Path) -> None:
    project = _project(tmp_path)
    assert envs.installed_torch_version(project) == ""
    _write_torch(tmp_path, "2.14.0+cpu")
    assert envs.installed_torch_version(project) == "2.14.0+cpu"


@pytest.fixture
def calls(monkeypatch) -> list[list[str]]:
    seen: list[list[str]] = []
    monkeypatch.setattr(envs, "install", lambda _p, pkgs, _cb: seen.append(pkgs))
    return seen


def test_a_cpu_build_is_replaced_by_the_cuda_one(
    tmp_path: Path, monkeypatch, calls
) -> None:
    # `uv pip install torch` counts any torch as satisfying the request, so the
    # venv that got the CPU wheel once would otherwise keep it forever.
    _write_torch(tmp_path, "2.14.0+cpu")
    monkeypatch.setattr(envs.sys, "platform", "win32")
    reason = envs.install_stack(
        _project(tmp_path), ["trl"], _profile("cuda"), lambda _: None
    )
    assert calls[0] == [
        "torch",
        "--index-url",
        PYTORCH_CUDA_INDEX,
        "--reinstall-package=torch",
    ]
    assert calls[1] == ["trl"]
    assert "replacing the CPU build" in reason


def test_a_cuda_build_is_left_alone(tmp_path: Path, monkeypatch, calls) -> None:
    _write_torch(tmp_path, "2.11.0+cu128")
    monkeypatch.setattr(envs.sys, "platform", "win32")
    envs.install_stack(_project(tmp_path), [], _profile("cuda"), lambda _: None)
    assert calls[0] == ["torch", "--index-url", PYTORCH_CUDA_INDEX]

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
    # The kernel packages ride along, so a venv bootstrapped before `ipywidgets`
    # joined the list picks it up here rather than never.
    assert calls[1] == [*envs.KERNEL_PACKAGES, "trl"]
    assert "replacing the CPU build" in reason


def test_a_cuda_build_is_left_alone(tmp_path: Path, monkeypatch, calls) -> None:
    _write_torch(tmp_path, "2.11.0+cu128")
    monkeypatch.setattr(envs.sys, "platform", "win32")
    envs.install_stack(_project(tmp_path), [], _profile("cuda"), lambda _: None)
    assert calls[0] == ["torch", "--index-url", PYTORCH_CUDA_INDEX]


# --- what a fine-tune starts from --------------------------------------------


def test_an_ollama_tag_is_named_as_the_wrong_kind_of_name() -> None:
    """`qwen3:0.6b` is the name this app shows everywhere else, so it is what gets
    typed — and it reached `from_pretrained` verbatim."""
    from backend.modules.training import basemodels

    (warning,) = basemodels.check("qwen3:0.6b")
    assert "Ollama" in warning
    assert "qwen3-0.6b" in warning  # the search term, offered as a guess


def test_an_empty_base_model_is_not_silently_fine() -> None:
    from backend.modules.training import basemodels

    assert basemodels.check("")


def test_a_canonical_hub_name_is_not_called_malformed(monkeypatch) -> None:
    """`gpt2` and `bert-base-uncased` have no owner. Demanding `owner/name` would
    be the form rejecting ids that work."""
    from backend.modules.training import basemodels

    asked: list[str] = []
    monkeypatch.setattr(
        basemodels, "_check_on_hub", lambda name: asked.append(name) or []
    )
    assert basemodels.check("gpt2") == []
    assert asked == ["gpt2"]


def test_an_unreachable_hub_is_not_evidence_of_a_missing_model(monkeypatch) -> None:
    from backend.modules.training import basemodels

    class Api:
        def model_info(self, name):
            raise OSError("connection refused")

    monkeypatch.setattr(basemodels, "_api", Api)
    assert basemodels.check("Qwen/Qwen3-0.6B") == []


def test_a_missing_repo_is_not_reported_as_a_licence_problem(monkeypatch) -> None:
    """The Hub's own 404 text says "private or gated", so matching on that word
    told every typo to go and accept a licence."""
    from backend.modules.training import basemodels

    class RepositoryNotFoundError(Exception):
        pass

    class Api:
        def model_info(self, name):
            raise RepositoryNotFoundError(
                "404 Client Error. Repository Not Found. If the repo is private or "
                "gated, make sure you are authenticated."
            )

    monkeypatch.setattr(basemodels, "_api", Api)
    (warning,) = basemodels.check("Nobody/nothing")
    assert "not found" in warning
    assert "Accept its licence" not in warning


def test_a_gguf_repo_is_refused_as_a_base(monkeypatch) -> None:
    from backend.modules.training import basemodels
    from types import SimpleNamespace

    info = SimpleNamespace(
        gated=False,
        config={"model_type": "qwen3"},
        siblings=[SimpleNamespace(rfilename="model-q4.gguf")],
    )
    monkeypatch.setattr(
        basemodels, "_api", lambda: SimpleNamespace(model_info=lambda name: info)
    )
    (warning,) = basemodels.check("Someone/Qwen3-0.6B-GGUF")
    assert "GGUF" in warning


def test_the_hub_is_asked_once_per_name_for_a_while(monkeypatch) -> None:
    """The recipe form is fetched on every pane open, task change and save; each
    one used to be a round-trip to the Hub — slow when reachable, a stall when not."""
    from types import SimpleNamespace

    from backend.modules.training import basemodels

    basemodels._CACHE.clear()
    calls: list[str] = []

    def api():
        return SimpleNamespace(
            model_info=lambda name: (
                calls.append(name)
                or SimpleNamespace(gated=False, config={}, siblings=[])
            )
        )

    monkeypatch.setattr(basemodels, "_api", api)
    assert basemodels.check("Qwen/Qwen3-0.6B") == []
    assert basemodels.check("Qwen/Qwen3-0.6B") == []
    assert calls == ["Qwen/Qwen3-0.6B"]

    # Not forever: accepting a licence must stop the gated warning without a
    # backend restart.
    basemodels._CACHE["Qwen/Qwen3-0.6B"] = (-basemodels._CACHE_TTL_S, [])
    basemodels.check("Qwen/Qwen3-0.6B")
    assert len(calls) == 2
    basemodels._CACHE.clear()

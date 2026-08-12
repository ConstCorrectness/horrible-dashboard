"""The hardware probe and the defaults it derives.

The test that matters most is `test_missing_nvidia_smi_is_not_no_gpu`. Every
other failure here is loud; that one is the silent kind — a machine where
`nvidia-smi` is not on PATH reports the same empty accelerator list as a machine
with no GPU, and rendering the first as "no GPU detected" is exactly the lie the
module exists to avoid.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.hardware import probe as hw


@pytest.fixture(autouse=True)
def clean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A fresh cache and an empty settings file for every test.

    The cache is process-global on purpose (the answer does not change while the
    process runs), which makes leaking it between tests the obvious hazard.
    """
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    hw.reset_cache()
    yield
    hw.reset_cache()


def fake_tools(monkeypatch: pytest.MonkeyPatch, outputs: dict[str, str | None]) -> None:
    """Replace `_run` with a table keyed by the tool name.

    A key absent from `outputs` means the tool is not installed — which is the
    state the whole "could not ask" distinction rests on.
    """

    def run(cmd: list[str]) -> str | None:
        return outputs.get(cmd[0])

    monkeypatch.setattr(hw, "_run", run)


_NVIDIA = "NVIDIA GeForce RTX 4090, 24564\n"
_NVIDIA_SMALL = "NVIDIA GeForce GTX 1050, 2048\n"


# ── the three states ─────────────────────────────────────────────────────────


def test_missing_nvidia_smi_is_not_no_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_tools(monkeypatch, {})
    monkeypatch.setattr(hw.platform, "system", lambda: "Linux")
    profile = hw.probe()

    assert profile.accelerators == ()
    # The finding is "we could not ask", and it must be legible as that.
    assert not profile.certain
    assert any(note.kind == "cuda" for note in profile.notes)

    # It still falls back to the safe build — but for a stated reason.
    tuning = hw.defaults(profile)
    assert tuning.llama_variant == "cpu"
    assert tuning.gpu_layers == 0
    assert "could not determine" in tuning.reasons["llamaVariant"]


def test_present_but_empty_is_a_real_finding(monkeypatch: pytest.MonkeyPatch) -> None:
    """`nvidia-smi` installed and reporting nothing *is* an answer."""
    fake_tools(monkeypatch, {"nvidia-smi": "\n"})
    monkeypatch.setattr(hw.platform, "system", lambda: "Linux")
    profile = hw.probe()

    assert profile.accelerators == ()
    assert profile.certain
    assert hw.defaults(profile).reasons["llamaVariant"] == "no accelerator detected"


def test_cuda_drives_variant_and_offload(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_tools(monkeypatch, {"nvidia-smi": _NVIDIA})
    # Windows, because that is the only OS upstream publishes a CUDA build for —
    # see test_linux_cuda_asks_for_vulkan_because_no_linux_cuda_build_exists.
    monkeypatch.setattr(hw.platform, "system", lambda: "Windows")
    profile = hw.probe()

    assert profile.primary is not None
    assert profile.primary.kind == "cuda"
    assert profile.primary.vram_mb == 24564
    assert profile.primary.exact

    tuning = hw.defaults(profile)
    assert tuning.llama_variant == "cuda"
    assert tuning.gpu_layers == 999


def test_linux_cuda_asks_for_vulkan_because_no_linux_cuda_build_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Upstream publishes CUDA for Windows only.

    Naming `cuda` on Linux does not produce a slow build or a suboptimal one — it
    produces no install at all ("this release publishes no cuda build for
    ubuntu/x64"). That failure only became reachable when the install default
    moved from a flat `cpu` to `auto`, so the mapping has to know about what
    upstream ships and not only about the card. Pinned upstream by
    `test_upstream_publishes_no_linux_cuda_build`.
    """
    fake_tools(monkeypatch, {"nvidia-smi": _NVIDIA})
    monkeypatch.setattr(hw.platform, "system", lambda: "Linux")
    tuning = hw.defaults(hw.probe())

    assert tuning.llama_variant == "vulkan"
    assert "no Linux CUDA build" in tuning.reasons["llamaVariant"]
    # Still full offload: the card is real, only the archive naming differs.
    assert tuning.gpu_layers == 999


def test_windows_cuda_still_asks_for_cuda(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_tools(monkeypatch, {"nvidia-smi": _NVIDIA})
    monkeypatch.setattr(hw.platform, "system", lambda: "Windows")
    assert hw.defaults(hw.probe()).llama_variant == "cuda"


def test_small_vram_keeps_layers_on_the_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 2 GB card is a GPU we found and deliberately do not offload to."""
    fake_tools(monkeypatch, {"nvidia-smi": _NVIDIA_SMALL})
    monkeypatch.setattr(hw.platform, "system", lambda: "Windows")
    tuning = hw.defaults(hw.probe())

    assert tuning.llama_variant == "cuda"
    assert tuning.gpu_layers == 0
    assert "below the" in tuning.reasons["gpuLayers"]


# ── unified memory ───────────────────────────────────────────────────────────


def test_apple_silicon_vram_is_unified_and_inexact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reporting unified memory as VRAM would make a 16 GB Mac look like a 16 GB card."""
    fake_tools(monkeypatch, {"sysctl": "Apple M3 Pro"})
    monkeypatch.setattr(hw.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(hw.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(hw, "_probe_ram", lambda: (18_000, True))
    profile = hw.probe()

    assert profile.primary is not None
    assert profile.primary.kind == "metal"
    assert profile.primary.unified
    assert not profile.primary.exact
    assert profile.primary.vram_mb == 18_000

    tuning = hw.defaults(profile)
    # There is no separate `metal` archive upstream; the macOS build carries it.
    assert tuning.llama_variant == "cpu"
    assert tuning.gpu_layers == 999


# ── de-duplication ───────────────────────────────────────────────────────────


def test_vulkan_does_not_double_count_a_cuda_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_tools(
        monkeypatch,
        {
            "nvidia-smi": _NVIDIA,
            "vulkaninfo": "deviceName = NVIDIA GeForce RTX 4090\n",
        },
    )
    monkeypatch.setattr(hw.platform, "system", lambda: "Linux")
    profile = hw.probe()

    assert [a.kind for a in profile.accelerators] == ["cuda"]


def test_vulkan_alone_reports_no_memory_rather_than_guessing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_tools(monkeypatch, {"vulkaninfo": "deviceName = Intel(R) Arc(tm) Graphics\n"})
    monkeypatch.setattr(hw.platform, "system", lambda: "Linux")
    tuning = hw.defaults(hw.probe())

    assert tuning.llama_variant == "vulkan"
    # No heap size in `--summary`; offloading would be a guess about what fits.
    assert tuning.gpu_layers == 0
    assert "no memory size" in tuning.reasons["gpuLayers"]


# ── overrides ────────────────────────────────────────────────────────────────


def test_override_declares_itself_as_an_assertion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A user's claim must never come back stamped as a measurement."""
    from backend.modules.settings.routes import set_value

    set_value("hardware.accelerator", "cuda")
    set_value("hardware.vramMb", 12_000)
    fake_tools(monkeypatch, {})
    monkeypatch.setattr(hw.platform, "system", lambda: "Windows")

    profile = hw.get_profile(refresh=True)
    assert profile.overridden
    assert profile.primary is not None
    assert profile.primary.detected_by == "override"
    assert not profile.primary.exact
    assert hw.defaults(profile).llama_variant == "cuda"


def test_override_none_silences_the_unknown_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.modules.settings.routes import set_value

    set_value("hardware.accelerator", "none")
    fake_tools(monkeypatch, {})
    monkeypatch.setattr(hw.platform, "system", lambda: "Linux")

    profile = hw.get_profile(refresh=True)
    assert profile.accelerators == ()
    # The user answered the question, so it is no longer open.
    assert profile.certain


def test_local_training_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.modules.settings.routes import set_value

    set_value("hardware.localTraining", "on")
    fake_tools(monkeypatch, {})
    monkeypatch.setattr(hw.platform, "system", lambda: "Linux")

    tuning = hw.defaults(hw.get_profile(refresh=True))
    assert tuning.local_training
    assert tuning.reasons["localTraining"] == "set in settings"


# ── RAM-derived caps ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("ram_mb", "cap"),
    [(None, 64), (8_000, 64), (16_384, 128), (32_768, 256), (128_000, 512)],
)
def test_trace_cap_tracks_ram(
    monkeypatch: pytest.MonkeyPatch, ram_mb: int | None, cap: int
) -> None:
    fake_tools(monkeypatch, {})
    monkeypatch.setattr(hw.platform, "system", lambda: "Linux")
    monkeypatch.setattr(hw, "_probe_ram", lambda: (ram_mb, ram_mb is not None))
    assert hw.defaults(hw.probe()).trace_token_cap == cap


def test_threads_leave_a_core_for_the_app(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_tools(monkeypatch, {})
    monkeypatch.setattr(hw.platform, "system", lambda: "Linux")
    monkeypatch.setattr(hw.os, "cpu_count", lambda: 8)
    assert hw.defaults(hw.probe()).threads == 7


# ── the route ────────────────────────────────────────────────────────────────


def test_route_serves_every_probed_field(monkeypatch: pytest.MonkeyPatch) -> None:
    """`response_model` filters undeclared fields silently — pin the shape."""
    fake_tools(monkeypatch, {"nvidia-smi": _NVIDIA})
    monkeypatch.setattr(hw.platform, "system", lambda: "Windows")

    with TestClient(app) as client:
        body = client.get("/api/hardware").json()

    assert body["profile"]["certain"] is True
    assert body["profile"]["primary"]["vramMb"] == 24564
    assert body["profile"]["primary"]["detectedBy"] == "nvidia-smi"
    assert body["defaults"]["llamaVariant"] == "cuda"
    # Every default carries its reason, or the pane cannot explain itself.
    for key in ("llamaVariant", "gpuLayers", "threads", "traceTokenCap"):
        assert body["defaults"]["reasons"].get(key)

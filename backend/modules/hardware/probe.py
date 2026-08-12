"""One capability probe, cached, driving defaults everywhere.

**Why this exists.** Every heavy surface in the app has been picking its own
default in isolation and picking the same one: `llama-server` spawns with
`--n-gpu-layers 0`, `binaries.install_server` defaults to the `cpu` variant, the
tracer caps tokens at a number chosen for a laptop. That is safe on the machine
without a GPU and wrong on the machine with one — and the app has no way to tell
them apart, so a 4090 runs the same CPU build as a Chromebook.

**The two failure modes are not symmetric, and neither is acceptable.** Assuming
a GPU means a CUDA build that fails to load its runtime, which looks exactly like
a broken install. Hiding one that exists means silently leaving an order of
magnitude on the table with nothing in the UI to explain it. So the probe reports
three states, not two: *found it*, *looked and it isn't there*, and **couldn't
ask** — the last being what you get when `nvidia-smi` isn't on PATH, which is not
the same fact as "no NVIDIA GPU" and must never be rendered as one.

**Honesty, the module's standing rule.** Every number here carries whether it was
measured or inferred (`exact`). Unified memory on Apple silicon is the sharp
case: the GPU's "VRAM" is the machine's RAM, so reporting it as VRAM would make a
16 GB Mac look like it has a 16 GB card. It is reported as unified and inexact.

**Sync on purpose.** These are short-lived subprocesses, and asyncio subprocess
spawning is broken under `uvicorn --reload` on Windows (SelectorEventLoop). The
probe uses `subprocess.run` with a timeout and callers reach it through
`asyncio.to_thread` — the same shape as the LSP/PTY spawns.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Accelerator kinds we can detect, in the order we prefer them when a machine
#: reports more than one (a laptop with an NVIDIA card also has a Vulkan-capable
#: integrated GPU; the discrete one is the answer).
KINDS = ("cuda", "rocm", "metal", "vulkan")

#: How long any one probe tool gets. A hung `vulkaninfo` must not hold a request.
_TIMEOUT = 6.0

#: Below this much VRAM an accelerator build buys little and risks OOM mid-load,
#: so the derived defaults keep layers on the CPU and say why.
_MIN_OFFLOAD_VRAM_MB = 3500

#: Local fine-tuning needs materially more than inference does. Under this the
#: training surface is *offered but marked*, never silently hidden — see
#: `Defaults.local_training`.
_MIN_TRAINING_VRAM_MB = 6000


@dataclass(frozen=True)
class Accelerator:
    """One GPU the machine reports.

    `exact` is about `vram_mb` only: `nvidia-smi` measures it, Apple silicon's
    unified memory is the machine's RAM shared with the CPU, and a Vulkan device
    often reports nothing at all.
    """

    kind: str
    name: str
    vram_mb: int | None
    unified: bool
    exact: bool
    detected_by: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "vramMb": self.vram_mb,
            "unified": self.unified,
            "exact": self.exact,
            "detectedBy": self.detected_by,
        }


@dataclass(frozen=True)
class ProbeNote:
    """Something we could not determine, and why.

    A note is the difference between "this machine has no NVIDIA GPU" and "we
    could not ask whether it does". Rendered in the pane; consumed by
    `Profile.certain`.
    """

    kind: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "reason": self.reason}


@dataclass(frozen=True)
class Profile:
    """What this machine is, as far as we could determine."""

    os: str
    arch: str
    cpu_count: int
    ram_mb: int | None
    ram_exact: bool
    accelerators: tuple[Accelerator, ...]
    notes: tuple[ProbeNote, ...]
    probed_at: float
    overridden: bool = False

    @property
    def primary(self) -> Accelerator | None:
        """The accelerator the defaults are derived from."""
        if not self.accelerators:
            return None
        return sorted(
            self.accelerators,
            key=lambda a: KINDS.index(a.kind) if a.kind in KINDS else 99,
        )[0]

    @property
    def certain(self) -> bool:
        """True when "no accelerator" is a finding rather than a gap.

        With no accelerators *and* a note, the honest report is "unknown" — the
        derived defaults are still the CPU ones, but the UI must not print
        "no GPU detected" as though we had looked and seen.
        """
        return bool(self.accelerators) or not self.notes

    def to_dict(self) -> dict[str, Any]:
        return {
            "os": self.os,
            "arch": self.arch,
            "cpuCount": self.cpu_count,
            "ramMb": self.ram_mb,
            "ramExact": self.ram_exact,
            "accelerators": [a.to_dict() for a in self.accelerators],
            "notes": [n.to_dict() for n in self.notes],
            "probedAt": self.probed_at,
            "overridden": self.overridden,
            "certain": self.certain,
            "primary": self.primary.to_dict() if self.primary else None,
        }


@dataclass(frozen=True)
class Defaults:
    """Defaults derived from a `Profile`, with a reason for each.

    The reasons are not decoration: this is the one object that explains why the
    app chose a CPU build on a machine whose owner knows it has a GPU, and
    without it that decision is invisible.
    """

    llama_variant: str
    gpu_layers: int
    threads: int
    trace_token_cap: int
    local_training: bool
    reasons: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "llamaVariant": self.llama_variant,
            "gpuLayers": self.gpu_layers,
            "threads": self.threads,
            "traceTokenCap": self.trace_token_cap,
            "localTraining": self.local_training,
            "reasons": dict(self.reasons),
        }


# --------------------------------------------------------------------------- #
# Individual probes. Each returns (accelerators, notes) and never raises.
# --------------------------------------------------------------------------- #


def _run(cmd: list[str]) -> str | None:
    """Stdout of `cmd`, or None when it is missing, fails, or hangs."""
    if shutil.which(cmd[0]) is None:
        return None
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("hardware probe %s failed: %s", cmd[0], exc)
        return None
    if res.returncode != 0:
        return None
    return res.stdout


def _probe_cuda() -> tuple[list[Accelerator], list[ProbeNote]]:
    out = _run(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total",
            "--format=csv,noheader,nounits",
        ]
    )
    if out is None:
        return [], [
            ProbeNote(
                "cuda",
                "nvidia-smi is not on PATH, so we could not ask whether this "
                "machine has an NVIDIA GPU. That is not the same as it having none.",
            )
        ]
    found: list[Accelerator] = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2 or not parts[0]:
            continue
        try:
            vram = int(float(parts[1]))
        except ValueError:
            continue
        found.append(
            Accelerator(
                kind="cuda",
                name=parts[0],
                vram_mb=vram,
                unified=False,
                exact=True,
                detected_by="nvidia-smi",
            )
        )
    return found, []


def _probe_rocm() -> tuple[list[Accelerator], list[ProbeNote]]:
    out = _run(["rocm-smi", "--showproductname", "--showmeminfo", "vram", "--json"])
    if out is None:
        return [], []
    try:
        data = json.loads(out)
    except ValueError:
        return [], [ProbeNote("rocm", "rocm-smi returned output we could not parse.")]
    found: list[Accelerator] = []
    for key, card in (data or {}).items():
        if not isinstance(card, dict) or not key.lower().startswith("card"):
            continue
        name = str(
            card.get("Card Series") or card.get("Card model") or "AMD GPU"
        ).strip()
        vram: int | None = None
        for field_name, value in card.items():
            if "vram" in field_name.lower() and "total" in field_name.lower():
                try:
                    vram = int(int(str(value).strip()) / (1024 * 1024))
                except (TypeError, ValueError):
                    vram = None
                break
        found.append(
            Accelerator(
                kind="rocm",
                name=name,
                vram_mb=vram,
                unified=False,
                exact=vram is not None,
                detected_by="rocm-smi",
            )
        )
    return found, []


def _probe_metal(ram_mb: int | None) -> tuple[list[Accelerator], list[ProbeNote]]:
    """Apple silicon. Metal is present on every arm64 Mac — there is nothing to
    ask. The trap is the memory number: it is the machine's RAM, shared with the
    CPU, so it is reported `unified` and `exact=False` rather than as VRAM."""
    if platform.system().lower() != "darwin":
        return [], []
    machine = platform.machine().lower()
    if machine not in {"arm64", "aarch64"}:
        return [], [
            ProbeNote(
                "metal",
                "Intel Macs have no unified-memory GPU worth offloading to.",
            )
        ]
    name = "Apple silicon GPU"
    out = _run(["sysctl", "-n", "machdep.cpu.brand_string"])
    if out and out.strip():
        name = f"{out.strip()} (integrated GPU)"
    return [
        Accelerator(
            kind="metal",
            name=name,
            vram_mb=ram_mb,
            unified=True,
            exact=False,
            detected_by="platform",
        )
    ], []


def _probe_vulkan() -> tuple[list[Accelerator], list[ProbeNote]]:
    out = _run(["vulkaninfo", "--summary"])
    if out is None:
        return [], []
    found: list[Accelerator] = []
    for match in re.finditer(r"deviceName\s*=\s*(.+)", out):
        name = match.group(1).strip()
        if not name:
            continue
        found.append(
            Accelerator(
                kind="vulkan",
                name=name,
                # vulkaninfo --summary reports no heap size; claiming one would
                # be inventing the single number the defaults key off.
                vram_mb=None,
                unified=False,
                exact=False,
                detected_by="vulkaninfo",
            )
        )
    return found, []


def _probe_ram() -> tuple[int | None, bool]:
    """Total physical RAM in MB, and whether we measured it.

    Deliberately dependency-free — three small platform reads rather than pulling
    psutil in for one number.
    """
    system = platform.system().lower()
    try:
        if system == "linux":
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                if line.startswith("MemTotal:"):
                    return int(int(line.split()[1]) / 1024), True
        elif system == "darwin":
            out = _run(["sysctl", "-n", "hw.memsize"])
            if out and out.strip().isdigit():
                return int(int(out.strip()) / (1024 * 1024)), True
        elif system.startswith("win"):
            import ctypes

            class _MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = _MemoryStatusEx()
            status.dwLength = ctypes.sizeof(_MemoryStatusEx)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.ullTotalPhys / (1024 * 1024)), True
    except (OSError, ValueError, AttributeError) as exc:
        logger.debug("RAM probe failed: %s", exc)
    return None, False


# --------------------------------------------------------------------------- #
# The probe itself
# --------------------------------------------------------------------------- #


def probe() -> Profile:
    """Run every detector. Never raises; gaps become notes."""
    ram_mb, ram_exact = _probe_ram()
    accelerators: list[Accelerator] = []
    notes: list[ProbeNote] = []
    for found, gaps in (
        _probe_cuda(),
        _probe_rocm(),
        _probe_metal(ram_mb),
        _probe_vulkan(),
    ):
        accelerators.extend(found)
        notes.extend(gaps)

    # A Vulkan device that is the same physical card as a detected CUDA/ROCm one
    # would double-count. Drop the generic entry when a specific one exists.
    if any(a.kind in {"cuda", "rocm"} for a in accelerators):
        accelerators = [a for a in accelerators if a.kind != "vulkan"]
        notes = [n for n in notes if n.kind != "vulkan"]

    return Profile(
        os=platform.system().lower(),
        arch=platform.machine().lower(),
        cpu_count=os.cpu_count() or 1,
        ram_mb=ram_mb,
        ram_exact=ram_exact,
        accelerators=tuple(accelerators),
        notes=tuple(notes),
        probed_at=time.time(),
    )


_cache: Profile | None = None
_lock = threading.Lock()


def get_profile(*, refresh: bool = False) -> Profile:
    """The cached profile, probing on first use.

    Cached because the answer does not change while the process runs (an eGPU
    unplugged mid-session is a `refresh` away) and because the probe spawns
    subprocesses — running it per request would put `vulkaninfo` in the hot path
    of every model list.
    """
    global _cache
    with _lock:
        if _cache is None or refresh:
            _cache = _apply_overrides(probe())
        return _cache


def reset_cache() -> None:
    """Drop the cached profile. For tests and for a settings change."""
    global _cache
    with _lock:
        _cache = None


# --------------------------------------------------------------------------- #
# Overrides
# --------------------------------------------------------------------------- #


def _apply_overrides(profile: Profile) -> Profile:
    """Let the user correct the probe.

    "Never assume a GPU; never hide one that exists" cuts both ways: a machine
    whose vendor tools aren't installed, or a CUDA card behind a container
    boundary, is one the user knows more about than we do. An override replaces
    the accelerator list outright and stamps `overridden`, so nothing downstream
    can present a user's assertion as a measurement.
    """
    from backend.modules.settings.routes import get_value

    kind = str(get_value("hardware.accelerator", "auto") or "auto").strip().lower()
    if kind in ("", "auto"):
        return profile
    if kind == "none":
        return Profile(
            os=profile.os,
            arch=profile.arch,
            cpu_count=profile.cpu_count,
            ram_mb=profile.ram_mb,
            ram_exact=profile.ram_exact,
            accelerators=(),
            notes=(),
            probed_at=profile.probed_at,
            overridden=True,
        )
    if kind not in KINDS:
        return profile
    try:
        vram = int(get_value("hardware.vramMb", 0) or 0)
    except (TypeError, ValueError):
        vram = 0
    return Profile(
        os=profile.os,
        arch=profile.arch,
        cpu_count=profile.cpu_count,
        ram_mb=profile.ram_mb,
        ram_exact=profile.ram_exact,
        accelerators=(
            Accelerator(
                kind=kind,
                name=f"{kind} (set in settings)",
                vram_mb=vram or None,
                unified=kind == "metal",
                exact=False,
                detected_by="override",
            ),
        ),
        notes=(),
        probed_at=profile.probed_at,
        overridden=True,
    )


# --------------------------------------------------------------------------- #
# Derived defaults
# --------------------------------------------------------------------------- #


def _variant_for(primary: Accelerator, os_name: str) -> tuple[str, str]:
    """The llama.cpp build to install for `primary` on `os_name`, and why.

    **The accelerator alone does not decide this — the OS does too**, because
    upstream does not publish the same variants everywhere. In particular there is
    **no Linux CUDA asset at all**: CUDA is Windows-only in the release artifacts
    (Linux users are expected to build it, or run the Vulkan build, which works
    fine on NVIDIA). Naming `cuda` on Linux therefore does not produce a slow
    build or a wrong one — it produces "this release publishes no cuda build for
    ubuntu/x64" and no install whatsoever.

    That failure only became reachable when the install default changed from a
    flat `cpu` to `auto`, which is exactly the kind of thing a probe that knows
    about hardware but not about what upstream ships would get wrong.
    """
    windows = os_name.startswith("win")
    detected = f"{primary.name} detected via {primary.detected_by}"

    if primary.kind == "metal":
        # Upstream's macOS builds carry Metal already — there is no separate
        # `metal` archive to ask for, and asking for one would 404.
        return (
            "cpu",
            "upstream's macOS builds include Metal; there is no separate variant",
        )
    if primary.kind == "cuda":
        if windows:
            return "cuda", detected
        return "vulkan", (
            f"{detected}, but upstream publishes no Linux CUDA build — the Vulkan "
            "build runs on NVIDIA and is the one that exists"
        )
    if primary.kind == "rocm":
        return "hip", detected
    return "vulkan", detected


def defaults(profile: Profile | None = None) -> Defaults:
    """Turn a profile into the numbers the rest of the app asks for."""
    profile = profile or get_profile()
    primary = profile.primary
    reasons: dict[str, str] = {}

    if primary is None:
        reasons["llamaVariant"] = (
            "no accelerator detected"
            if profile.certain
            else "could not determine whether this machine has a GPU, so the "
            "build that runs everywhere was chosen"
        )
        variant, gpu_layers = "cpu", 0
        reasons["gpuLayers"] = reasons["llamaVariant"]
    else:
        variant, reasons["llamaVariant"] = _variant_for(primary, profile.os)

        if primary.vram_mb is None:
            gpu_layers = 0
            reasons["gpuLayers"] = (
                f"{primary.name} reports no memory size, so offloading would be a "
                "guess about how much fits"
            )
        elif primary.unified:
            # Unified memory is the machine's RAM. Offloading everything is right
            # on Apple silicon — the weights are not copied anywhere.
            gpu_layers = 999
            reasons["gpuLayers"] = "unified memory: layers cost no extra copy"
        elif primary.vram_mb < _MIN_OFFLOAD_VRAM_MB:
            gpu_layers = 0
            reasons["gpuLayers"] = (
                f"{primary.vram_mb} MB of VRAM is below the {_MIN_OFFLOAD_VRAM_MB} MB "
                "floor where offloading pays for its OOM risk"
            )
        else:
            gpu_layers = 999
            reasons["gpuLayers"] = f"{primary.vram_mb} MB of VRAM"

    # Leave a core for the event loop and whatever else the node is doing; a
    # llama-server pinned to every core makes the whole app stutter.
    threads = max(1, min(profile.cpu_count - 1, 16))
    reasons["threads"] = f"{profile.cpu_count} logical CPUs, one left for the app"

    # A traced pass with attention on is ~1 GB; the cap is what stops a curious
    # click from filling the disk. RAM is the binding constraint, not VRAM — the
    # tracer runs on the CPU wheel.
    if profile.ram_mb is None:
        trace_cap = 64
        reasons["traceTokenCap"] = "RAM unknown, so the conservative cap was kept"
    elif profile.ram_mb >= 64_000:
        trace_cap = 512
        reasons["traceTokenCap"] = f"{profile.ram_mb // 1024} GB RAM"
    elif profile.ram_mb >= 32_000:
        trace_cap = 256
        reasons["traceTokenCap"] = f"{profile.ram_mb // 1024} GB RAM"
    elif profile.ram_mb >= 16_000:
        trace_cap = 128
        reasons["traceTokenCap"] = f"{profile.ram_mb // 1024} GB RAM"
    else:
        trace_cap = 64
        reasons["traceTokenCap"] = f"{profile.ram_mb // 1024} GB RAM"

    override = str(get_setting("hardware.localTraining", "auto")).strip().lower()
    if override in ("on", "off"):
        local_training = override == "on"
        reasons["localTraining"] = "set in settings"
    else:
        local_training = bool(
            primary
            and primary.kind in {"cuda", "rocm", "metal"}
            and (primary.vram_mb or 0) >= _MIN_TRAINING_VRAM_MB
        )
        if local_training:
            reasons["localTraining"] = f"{primary.name} with {primary.vram_mb} MB"  # type: ignore[union-attr]
        elif primary is None and not profile.certain:
            reasons["localTraining"] = (
                "could not determine this machine's GPU; local training is not "
                "recommended by default, but nothing stops you starting a run"
            )
        else:
            reasons["localTraining"] = (
                f"local fine-tuning wants at least {_MIN_TRAINING_VRAM_MB} MB of VRAM; "
                "Kaggle and Colab push are the recommended path here"
            )

    return Defaults(
        llama_variant=variant,
        gpu_layers=gpu_layers,
        threads=threads,
        trace_token_cap=trace_cap,
        local_training=local_training,
        reasons=reasons,
    )


def get_setting(key: str, default: Any) -> Any:
    """Indirection so `defaults()` stays importable without a settings file."""
    from backend.modules.settings.routes import get_value

    return get_value(key, default)

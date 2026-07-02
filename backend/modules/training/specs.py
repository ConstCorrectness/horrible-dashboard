"""Hardware snapshot for training-fabric ads.

Stdlib-only for CPU/RAM (the backend env has no torch); GPU is probed via
`nvidia-smi` (Popen, short timeout) so a node without an NVIDIA GPU — or without
the tool — simply advertises no GPU rather than failing.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from typing import Any


def _ram_gb() -> float | None:
    # os.sysconf on POSIX; fall back to nothing on platforms without it.
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return round(pages * page_size / (1024**3), 1)
    except (ValueError, AttributeError, OSError):
        pass
    # Windows: GlobalMemoryStatusEx via ctypes.
    try:
        import ctypes

        class _MemStatus(ctypes.Structure):
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

        status = _MemStatus()
        status.dwLength = ctypes.sizeof(_MemStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
            return round(status.ullTotalPhys / (1024**3), 1)
    except Exception:  # noqa: BLE001 — best-effort
        pass
    return None


def _gpus() -> list[dict[str, Any]]:
    smi = shutil.which("nvidia-smi")
    if smi is None:
        return []
    try:
        out = subprocess.run(
            [smi, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []
    gpus: list[dict[str, Any]] = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        name = parts[0]
        vram_gb = None
        if len(parts) > 1:
            try:
                vram_gb = round(float(parts[1]) / 1024, 1)
            except ValueError:
                pass
        gpus.append({"name": name, "vram_gb": vram_gb})
    return gpus


def snapshot() -> dict[str, Any]:
    """A JSON-able hardware summary for a training ad."""
    gpus = _gpus()
    return {
        "platform": platform.system(),
        "cpu": platform.processor() or platform.machine(),
        "cpu_count": os.cpu_count(),
        "ram_gb": _ram_gb(),
        "gpus": gpus,
        "gpu": gpus[0]["name"] if gpus else None,
        "vram_gb": gpus[0]["vram_gb"] if gpus else None,
    }

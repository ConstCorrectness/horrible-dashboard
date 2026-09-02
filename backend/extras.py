"""Which optional extras are installed here — asked the same way every time.

This repo has seven `uv sync --extra …` groups, and several are genuinely painful
to install: `voice` pulls torch (1–2 GB), `llamacpp` builds llama.cpp from an sdist
and needs cmake plus a C++ compiler, `browser-engine` fetches ~150 MB of Chromium,
`clip` downloads ~350 MB of weights on first use. A laptop that lacks them should
be able to borrow a desktop that has them, and that starts with being able to ask
the question in one shape.

Before this, every extra answered differently: `karaoke.downloader.available()`
and `library.clip.clip_installed()` returned a bare `bool`,
`llamacpp.trace_runner.available()` returned `(bool, reason)`, and the voice
services were lazy imports turned into a 503 at the route. None of them could say
the third thing.

**Three states, not two** — the rule `hardware/probe.py` exists to enforce:

| state | `available` | `certain` | means |
| --- | --- | --- | --- |
| installed | True | True | it imported |
| absent | False | True | `ImportError` — it is genuinely not here |
| unknown | False | False | it is here but would not load, or we could not look |

The third row is the one that matters. A native library that fails to load is
**not** the same fact as a package that was never installed, and reporting it as
"not installed" sends someone to reinstall a package already sitting on disk. So
an `ImportError` is the only thing read as absence; anything else is reported as
what it was, with the error attached.

This module deliberately does **not** decide policy. Whether an absent extra means
a 503, a disabled setting, or a request to borrow a peer's is the caller's
business — see `borrow_or_hint`.
"""

from __future__ import annotations

import importlib
import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Availability:
    """Whether one extra can be used here.

    `available` is only meaningful when `certain` is True. When `certain` is False
    the honest rendering is "unknown", never "missing".
    """

    extra: str
    available: bool
    certain: bool
    reason: str = ""
    install: str = ""

    @property
    def state(self) -> str:
        if self.available:
            return "installed"
        return "absent" if self.certain else "unknown"

    def to_dict(self) -> dict[str, object]:
        return {
            "extra": self.extra,
            "available": self.available,
            "certain": self.certain,
            "state": self.state,
            "reason": self.reason,
            "install": self.install,
        }


@dataclass(frozen=True)
class ExtraSpec:
    """How to test for one extra, and what to tell someone who lacks it."""

    name: str
    #: Imported in *this* process. Cheap, and it gives a real reason rather than a
    #: subprocess that dies two seconds after the user clicks something.
    modules: tuple[str, ...] = ()
    install: str = ""
    summary: str = ""
    #: For extras that are a binary on PATH rather than a Python package.
    binary: str = ""
    #: Escape hatch for anything neither an import nor a binary.
    check: Callable[[], Availability] | None = None


EXTRAS: dict[str, ExtraSpec] = {
    "voice": ExtraSpec(
        name="voice",
        # Whisper here is `openai/whisper-tiny.en` run through **transformers**
        # (see `agent/stt_service.py`), not the `openai-whisper` package — which
        # this repo has never depended on and `uv.lock` has never contained.
        # Probing for a module named `whisper` therefore reported the extra
        # absent with `certain=True` no matter what was installed, and told the
        # user to run the very `uv sync --extra voice` they had already run.
        modules=("torch", "transformers", "edge_tts"),
        install="uv sync --extra voice",
        summary="Whisper speech-to-text and Edge text-to-speech",
    ),
    "clip": ExtraSpec(
        name="clip",
        modules=("onnxruntime", "tokenizers"),
        install="uv sync --extra clip",
        summary="CLIP visual search for the library",
    ),
    "llamacpp": ExtraSpec(
        name="llamacpp",
        modules=("llama_cpp",),
        install="uv sync --extra llamacpp",
        summary="activation tracing (llama-cpp-python)",
    ),
    "browser-engine": ExtraSpec(
        name="browser-engine",
        modules=("playwright",),
        install="uv sync --extra browser-engine && uv run playwright install chromium",
        summary="real headless Chromium for the browser module",
    ),
    "games-native": ExtraSpec(
        name="games-native",
        modules=("vizdoom",),
        install="uv sync --extra games-native",
        summary="the native ViZDoom engine",
    ),
    "geoip": ExtraSpec(
        name="geoip",
        modules=("maxminddb",),
        install="uv sync --extra geoip",
        summary="GeoIP lookup for traceroute hops",
    ),
    "webrtc": ExtraSpec(
        name="webrtc",
        modules=("aiortc",),
        install="uv sync --extra webrtc",
        summary="the WebRTC peer transport and share relay",
    ),
    "ffmpeg": ExtraSpec(
        name="ffmpeg",
        binary="ffmpeg",
        install="install ffmpeg and put it on PATH",
        summary="karaoke pitch shifting and audio decoding",
    ),
    "yt-dlp": ExtraSpec(
        name="yt-dlp",
        modules=("yt_dlp",),
        install="uv sync",
        summary="karaoke search and download",
    ),
}

#: Probing imports a package, which is not free the first time. Results are cached
#: for the process; `reset_cache()` exists for tests and for after an install.
_cache: dict[str, Availability] = {}


def _probe_modules(spec: ExtraSpec) -> Availability:
    for module in spec.modules:
        try:
            importlib.import_module(module)
        except ImportError as exc:
            # The only exception read as absence. Anything else means something is
            # there but unhappy, which is a different problem with a different fix.
            return Availability(
                extra=spec.name,
                available=False,
                certain=True,
                reason=f"{module} is not installed ({exc})",
                install=spec.install,
            )
        except Exception as exc:  # noqa: BLE001
            # A broken native load, a missing DLL, an OSError from a data file.
            # Reporting this as "not installed" would send someone to reinstall a
            # package that is already on disk.
            return Availability(
                extra=spec.name,
                available=False,
                certain=False,
                reason=f"{module} is present but would not load: {exc}",
                install=spec.install,
            )
    return Availability(extra=spec.name, available=True, certain=True)


def _probe_binary(spec: ExtraSpec) -> Availability:
    if shutil.which(spec.binary):
        return Availability(extra=spec.name, available=True, certain=True)
    return Availability(
        extra=spec.name,
        available=False,
        certain=True,
        reason=f"{spec.binary} is not on PATH",
        install=spec.install,
    )


def probe(extra: str, *, refresh: bool = False) -> Availability:
    """Whether `extra` can be used in this process."""
    if not refresh:
        cached = _cache.get(extra)
        if cached is not None:
            return cached

    spec = EXTRAS.get(extra)
    if spec is None:
        # An unknown name is our bug, not the user's environment. `certain=False`
        # so nothing downstream renders it as "the user has not installed this".
        return Availability(
            extra=extra,
            available=False,
            certain=False,
            reason=f"no extra named {extra!r} is declared",
        )

    try:
        if spec.check is not None:
            result = spec.check()
        elif spec.binary:
            result = _probe_binary(spec)
        else:
            result = _probe_modules(spec)
    except Exception as exc:  # noqa: BLE001 - a probe must never raise at a caller
        logger.exception("extras: probing %s failed", extra)
        result = Availability(
            extra=extra,
            available=False,
            certain=False,
            reason=f"the probe itself failed: {exc}",
            install=spec.install,
        )

    _cache[extra] = result
    return result


def available(extra: str) -> bool:
    """Convenience for the common case. Callers that need to *explain* an absence
    should use `probe` and read `certain`."""
    return probe(extra).available


def snapshot() -> dict[str, Availability]:
    return {name: probe(name) for name in EXTRAS}


def installed_names() -> list[str]:
    return sorted(name for name, a in snapshot().items() if a.available)


def reset_cache() -> None:
    _cache.clear()

"""What the installed `llama-server` build can actually do, asked rather than assumed.

Two things make this necessary, and both fail *silently* without it.

**Flag names move.** In 2026 upstream renamed the speculative-decoding flags under
a `--spec-` prefix: `--draft-max` became `--spec-draft-n-max`, `--draft-min`
became `--spec-draft-n-min`. Passing the old spelling to a new build does not warn
— the server exits during load, and `wait_ready` reports it as the generic "did
not start", which reads as a broken model rather than a bad flag. Hardcoding
either spelling is therefore wrong for half the builds in the wild.

**Features are compile-time.** `--rpc` (distributed ggml offload) exists only in a
build configured with `-DGGML_RPC=ON`. Whether upstream's *release* binaries carry
it has changed over time -- b10453-cuda, the build this module downloads, does --
which is exactly why this is probed rather than asserted from documentation. A
constant here would have been written down once, been true for a while, and then
quietly stopped being true.

The probe answers in **three states**, following `hardware/probe.py`: the flag is
there, it is not, or we could not ask (no install, `--help` failed, a timeout).
"Could not ask" is not "absent" — reporting it as absent would have the UI tell a
user their build lacks a feature when the truth is that nobody looked.

Cached by binary path **and mtime**, so reinstalling over the same path re-probes.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

HELP_TIMEOUT_S = 15.0

#: Speculative decoding. The draft model itself has kept `-md`/`--model-draft`
#: across the rename; only the tuning knobs moved.
DRAFT_MODEL_FLAGS = ("--model-draft", "-md")
#: Newest spelling first: if a build somehow accepts both, we want the current one.
DRAFT_MAX_FLAGS = ("--spec-draft-n-max", "--draft-max")
DRAFT_MIN_FLAGS = ("--spec-draft-n-min", "--draft-min")
DRAFT_P_MIN_FLAGS = ("--spec-draft-p-min", "--draft-p-min")
#: Layers of the *draft* model to offload; distinct from `-ngl`.
DRAFT_NGL_FLAGS = ("-ngld", "--n-gpu-layers-draft")
RPC_FLAGS = ("--rpc",)


@dataclass(frozen=True)
class Feature:
    """One capability of this build.

    `supported` is only meaningful when `certain` is True. When `certain` is False
    the answer is "we could not ask", and `reason` says why -- which is a different
    fact from "this build does not have it" and must be rendered differently.
    """

    name: str
    supported: bool
    certain: bool
    #: The spelling this build wants, when there is one. Callers compose flags from
    #: this rather than from a constant, which is the entire point of the probe.
    flag: str = ""
    reason: str = ""


@dataclass(frozen=True)
class Features:
    binary: str
    draft_model: Feature
    draft_max: Feature
    draft_min: Feature
    draft_p_min: Feature
    draft_ngl: Feature
    rpc: Feature

    @property
    def speculative(self) -> bool:
        """Whether this build can do speculative decoding at all.

        The draft *model* flag is the necessary one; the tuning knobs are optional
        and their absence only means defaults.
        """
        return self.draft_model.supported

    def to_dict(self) -> dict[str, object]:
        return {
            "binary": self.binary,
            "speculative": self.speculative,
            "features": {
                f.name: {
                    "supported": f.supported,
                    "certain": f.certain,
                    "flag": f.flag,
                    "reason": f.reason,
                }
                for f in (
                    self.draft_model,
                    self.draft_max,
                    self.draft_min,
                    self.draft_p_min,
                    self.draft_ngl,
                    self.rpc,
                )
            },
        }


_cache: dict[tuple[str, float], Features] = {}


def _unknown(name: str, reason: str) -> Feature:
    return Feature(name=name, supported=False, certain=False, reason=reason)


def _detect(help_text: str, name: str, candidates: tuple[str, ...]) -> Feature:
    """Find which spelling of a flag this build advertises.

    Matched against the help text with a delimiter check rather than a bare
    substring: `--draft-max` is a substring of `--spec-draft-n-max`'s help line in
    some builds' prose, and a bare `in` would report the retired spelling as
    supported and then fail at load.
    """
    for flag in candidates:
        for suffix in (" ", ",", "\n", "=", "\t"):
            if f"{flag}{suffix}" in help_text:
                return Feature(name=name, supported=True, certain=True, flag=flag)
    return Feature(
        name=name,
        supported=False,
        certain=True,
        reason="this build does not advertise the flag",
    )


def _server_binary() -> Path | None:
    from backend.modules.llamacpp import binaries

    install = binaries.newest_install()
    return install.binary if install else None


def probe_flags(binary: str | Path | None = None) -> Features:
    """Ask `llama-server --help` what it supports."""
    path = Path(binary) if binary else _server_binary()
    if path is None:
        reason = "no llama.cpp build is installed"
        return _all_unknown("", reason)
    try:
        mtime = path.stat().st_mtime
    except OSError as exc:
        return _all_unknown(str(path), f"cannot stat the binary: {exc}")

    key = (str(path), mtime)
    cached = _cache.get(key)
    if cached is not None:
        return cached

    try:
        proc = subprocess.run(  # noqa: S603 - our own downloaded binary
            [str(path), "--help"],
            capture_output=True,
            text=True,
            timeout=HELP_TIMEOUT_S,
            # `--help` on some builds writes to stderr and exits non-zero; both
            # streams are joined below and the exit code is deliberately ignored.
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return _all_unknown(str(path), f"could not run --help: {exc}")

    help_text = f"{proc.stdout}\n{proc.stderr}"
    if len(help_text.strip()) < 40:
        # An empty help output means the probe failed, not that the build supports
        # nothing -- reporting "absent" here would be the fiction this avoids.
        return _all_unknown(str(path), "--help produced no output")

    features = Features(
        binary=str(path),
        draft_model=_detect(help_text, "draftModel", DRAFT_MODEL_FLAGS),
        draft_max=_detect(help_text, "draftMax", DRAFT_MAX_FLAGS),
        draft_min=_detect(help_text, "draftMin", DRAFT_MIN_FLAGS),
        draft_p_min=_detect(help_text, "draftPMin", DRAFT_P_MIN_FLAGS),
        draft_ngl=_detect(help_text, "draftGpuLayers", DRAFT_NGL_FLAGS),
        rpc=_detect(help_text, "rpc", RPC_FLAGS),
    )
    _cache[key] = features
    return features


def _all_unknown(binary: str, reason: str) -> Features:
    return Features(
        binary=binary,
        draft_model=_unknown("draftModel", reason),
        draft_max=_unknown("draftMax", reason),
        draft_min=_unknown("draftMin", reason),
        draft_p_min=_unknown("draftPMin", reason),
        draft_ngl=_unknown("draftGpuLayers", reason),
        rpc=_unknown("rpc", reason),
    )


def reset_cache() -> None:
    _cache.clear()


def speculative_args(
    draft_path: str,
    *,
    draft_max: int | None = None,
    draft_min: int | None = None,
    draft_p_min: float | None = None,
    draft_gpu_layers: int | None = None,
    features: Features | None = None,
) -> list[str]:
    """Compose the command line for speculative decoding on *this* build.

    Raises if the build cannot do it, rather than returning flags that will make
    the server exit during load -- a failure that surfaces only as "did not start"
    and reads as a broken model.

    Tuning knobs the build does not advertise are **dropped**, not guessed at with
    the other spelling: their absence means the build uses its own defaults, which
    is a working configuration, where a wrong flag is not.
    """
    info = features if features is not None else probe_flags()
    if not info.draft_model.supported:
        detail = info.draft_model.reason or "no draft-model flag"
        raise RuntimeError(
            f"this llama.cpp build cannot do speculative decoding: {detail}"
        )

    args = [info.draft_model.flag, draft_path]
    for value, feature in (
        (draft_max, info.draft_max),
        (draft_min, info.draft_min),
        (draft_gpu_layers, info.draft_ngl),
    ):
        # `is not None`, never falsiness: an explicit 0 for draft GPU layers means
        # "keep the draft on the CPU", which is a real and useful choice.
        if value is not None and feature.supported:
            args += [feature.flag, str(value)]
    if draft_p_min is not None and info.draft_p_min.supported:
        args += [info.draft_p_min.flag, str(draft_p_min)]
    return args

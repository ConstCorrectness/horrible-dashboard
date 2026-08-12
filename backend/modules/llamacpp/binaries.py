"""Fetching and installing upstream `llama-server` builds.

**Why a downloaded binary and not a Python wheel.** The chat path must not depend
on `llama-cpp-python`: the wheel exists to expose the C API (and, later, the ggml
eval callback the tracer needs), it lags upstream, and a build failure on it would
take chat down with it. Upstream publishes a prebuilt `llama-server` for every
platform on every release; that is what serves the OpenAI API we talk to.

**Asset selection is matched, not hardcoded.** Release asset names have changed
shape several times (`llama-b4567-bin-win-cpu-x64.zip` today), so pinning exact
strings guarantees a break on some future release. `select_asset` matches on the
tokens that actually carry meaning — OS, arch, accelerator — and, when nothing
matches, reports the names it *did* see rather than failing blind.

**Verification is honest about what it knows.** GitHub reports a `digest` for
release assets; when it is present a mismatch aborts the install and nothing is
written. When it is absent we record the digest we computed and mark the install
`verified: false` — which the UI shows. Recording a self-computed hash and calling
it verification would be theatre.
"""

from __future__ import annotations

import hashlib
import logging
import os
import platform
import shutil
import zipfile
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

RELEASES_URL = "https://api.github.com/repos/ggml-org/llama.cpp/releases"
REPO_URL = "https://github.com/ggml-org/llama.cpp"

#: Accelerator builds we know how to ask for. `cpu` is the default on every OS —
#: it is the only variant guaranteed to run on the machine that downloads it, and
#: a GPU build that fails to load its runtime looks exactly like a broken install.
VARIANTS = ("cpu", "cuda", "vulkan", "hip", "sycl")

#: Tokens that mark an accelerator build. Used both to *find* a variant and to
#: *exclude* one: a plain `cpu` request must not match the CUDA archive, whose
#: name contains every token a cpu name does plus one.
_ACCEL_TOKENS = ("cuda", "vulkan", "hip", "rocm", "sycl", "musa", "cann")


@dataclass(frozen=True)
class Install:
    """One unpacked `llama-server` build under the data dir."""

    tag: str
    variant: str
    path: Path
    binary: Path
    size_bytes: int
    sha256: str
    verified: bool
    asset: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "tag": self.tag,
            "variant": self.variant,
            "path": str(self.path),
            "binary": str(self.binary),
            "sizeBytes": self.size_bytes,
            "sha256": self.sha256,
            "verified": self.verified,
            "asset": self.asset,
        }


def bin_root() -> Path:
    return Path(os.environ.get("HORRIBLE_DATA_DIR", ".data")) / "llamacpp" / "bin"


def platform_tokens() -> tuple[str, str]:
    """(os token, arch token) as they appear in upstream asset names."""
    system = platform.system().lower()
    if system.startswith("win"):
        os_token = "win"
    elif system == "darwin":
        os_token = "macos"
    else:
        os_token = "ubuntu"
    machine = platform.machine().lower()
    if machine in {"arm64", "aarch64"}:
        arch = "arm64"
    else:
        arch = "x64"
    return os_token, arch


def server_filename() -> str:
    return (
        "llama-server.exe"
        if platform.system().lower().startswith("win")
        else "llama-server"
    )


def select_asset(
    names: Iterable[str], os_token: str, arch: str, variant: str = "cpu"
) -> str | None:
    """The release asset to download, or None when the release has nothing for us.

    Deliberately tolerant on arch and strict on accelerator. macOS arm64 archives
    have carried the arch in two different spellings and Linux ones have sometimes
    omitted it entirely, so a name that matches the OS and the accelerator but says
    nothing about the arch is still a candidate — whereas a CUDA archive is never
    an acceptable substitute for the `cpu` build that was asked for.
    """
    wrong_arch = "arm64" if arch == "x64" else "x64"
    candidates: list[str] = []
    for name in names:
        lower = name.lower()
        # `llama-<tag>-bin-<os>-<variant>-<arch>.zip`. The prefix is not decoration:
        # releases also carry `cudart-llama-bin-win-cu12.4-x64.zip`, the CUDA runtime
        # redistributable, which matches every other token here and contains no
        # server at all.
        if not lower.startswith("llama-"):
            continue
        if not lower.endswith(".zip") or "-bin-" not in lower:
            continue
        if os_token not in lower:
            continue
        if wrong_arch in lower:
            continue
        accel = [token for token in _ACCEL_TOKENS if token in lower]
        if variant == "cpu":
            if accel:
                continue
        elif not any(
            token in lower
            for token in (variant, "rocm" if variant == "hip" else variant)
        ):
            continue
        candidates.append(name)
    if not candidates:
        return None
    # Prefer a name that names our arch explicitly, then the shortest — the shortest
    # match is the plain build, longer ones carry extra qualifiers (a toolkit
    # version, a second accelerator).
    candidates.sort(key=lambda n: (arch not in n.lower(), len(n), n))
    return candidates[0]


async def fetch_release(
    client: httpx.AsyncClient, tag: str = "latest"
) -> dict[str, Any]:
    """The GitHub release payload for `tag` ("latest" for the newest)."""
    url = (
        f"{RELEASES_URL}/latest"
        if tag in ("", "latest")
        else f"{RELEASES_URL}/tags/{tag}"
    )
    res = await client.get(url, headers={"Accept": "application/vnd.github+json"})
    res.raise_for_status()
    data = res.json()
    if not isinstance(data, dict):
        raise RuntimeError("unexpected release payload from GitHub")
    return data


def _expected_digest(asset: dict[str, Any]) -> str:
    """The sha256 GitHub publishes for an asset, or "" when it publishes none."""
    digest = str(asset.get("digest") or "")
    prefix = "sha256:"
    return digest[len(prefix) :] if digest.startswith(prefix) else ""


def install_dir(tag: str, variant: str) -> Path:
    return bin_root() / f"{tag}-{variant}"


def _find_server(root: Path) -> Path | None:
    wanted = server_filename()
    for path in root.rglob(wanted):
        if path.is_file():
            return path
    return None


def read_install(path: Path) -> Install | None:
    """An installed build read back off disk, or None when the directory is not one.

    The marker file is the source of truth for provenance (which asset, which
    digest, whether it was verified); the binary existing is what makes it usable.
    Either missing means "not installed" — a half-extracted directory must not
    present itself as a build you can run.
    """
    import json

    marker = path / "install.json"
    if not marker.is_file():
        return None
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    binary = path / str(data.get("binary") or "")
    if not binary.is_file():
        binary_found = _find_server(path)
        if binary_found is None:
            return None
        binary = binary_found
    return Install(
        tag=str(data.get("tag") or path.name),
        variant=str(data.get("variant") or "cpu"),
        path=path,
        binary=binary,
        size_bytes=int(data.get("sizeBytes") or 0),
        sha256=str(data.get("sha256") or ""),
        verified=bool(data.get("verified")),
        asset=str(data.get("asset") or ""),
    )


def list_installs() -> list[Install]:
    root = bin_root()
    if not root.is_dir():
        return []
    installs = [
        read_install(child) for child in sorted(root.iterdir()) if child.is_dir()
    ]
    return [i for i in installs if i is not None]


def newest_install() -> Install | None:
    """The install to use when the caller didn't name one.

    Sorted by the release tag, which upstream increments monotonically (`b4567`),
    so "newest" is a real ordering rather than a directory-listing accident.
    """
    installs = list_installs()
    if not installs:
        return None
    return sorted(installs, key=lambda i: (_tag_key(i.tag), i.tag))[-1]


def _tag_key(tag: str) -> tuple[int, str]:
    digits = "".join(ch for ch in tag if ch.isdigit())
    return (int(digits) if digits else 0, tag)


def _safe_extract(archive: zipfile.ZipFile, dest: Path) -> None:
    """Extract, refusing any member that would land outside `dest`.

    `ZipFile.extractall` sanitizes absolute paths but a crafted archive is still
    the classic way to write somewhere you didn't intend, and this one arrives over
    the network.
    """
    dest_resolved = dest.resolve()
    for member in archive.infolist():
        target = (dest / member.filename).resolve()
        if not str(target).startswith(str(dest_resolved)):
            raise RuntimeError(
                f"refusing archive member outside the install dir: {member.filename}"
            )
    archive.extractall(dest)


async def install_server(
    tag: str = "latest",
    variant: str = "cpu",
    *,
    client: httpx.AsyncClient | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Download and unpack a `llama-server` build, yielding progress events.

    Events are `{status, ...}` dicts in the shape the pane and `/agent/pull`
    already speak: `resolving` → `downloading` (with completed/total) →
    `verifying` → `extracting` → `done`, or a terminal `{error}`.
    """
    if variant not in VARIANTS:
        yield {"error": f"unknown variant '{variant}' (known: {', '.join(VARIANTS)})"}
        return

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=None, follow_redirects=True)
    try:
        yield {"status": "resolving", "tag": tag, "variant": variant}
        try:
            release = await fetch_release(client, tag)
        except httpx.HTTPError as exc:
            yield {"error": f"could not reach the llama.cpp releases API: {exc}"}
            return

        resolved_tag = str(release.get("tag_name") or tag)
        assets = [a for a in release.get("assets") or [] if isinstance(a, dict)]
        os_token, arch = platform_tokens()
        name = select_asset(
            (str(a.get("name") or "") for a in assets), os_token, arch, variant
        )
        if name is None:
            available = ", ".join(sorted(str(a.get("name") or "") for a in assets)[:12])
            yield {
                "error": (
                    f"release {resolved_tag} publishes no {variant} build for "
                    f"{os_token}/{arch}. Assets seen: {available or 'none'}"
                )
            }
            return
        asset = next(a for a in assets if str(a.get("name") or "") == name)
        url = str(asset.get("browser_download_url") or "")
        expected = _expected_digest(asset)
        total = int(asset.get("size") or 0)

        dest = install_dir(resolved_tag, variant)
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        dest.mkdir(parents=True, exist_ok=True)
        archive_path = dest / name

        yield {"status": "downloading", "asset": name, "total": total, "completed": 0}
        digest = hashlib.sha256()
        completed = 0
        try:
            async with client.stream("GET", url) as res:
                res.raise_for_status()
                with open(archive_path, "wb") as handle:
                    async for chunk in res.aiter_bytes(1 << 20):
                        handle.write(chunk)
                        digest.update(chunk)
                        completed += len(chunk)
                        yield {
                            "status": "downloading",
                            "asset": name,
                            "total": total,
                            "completed": completed,
                        }
        except httpx.HTTPError as exc:
            shutil.rmtree(dest, ignore_errors=True)
            yield {"error": f"download failed: {exc}"}
            return

        actual = digest.hexdigest()
        yield {"status": "verifying", "sha256": actual, "expected": expected}
        if expected and actual != expected:
            shutil.rmtree(dest, ignore_errors=True)
            yield {
                "error": (
                    "sha256 mismatch — the download does not match the digest GitHub "
                    f"published for {name} (expected {expected[:16]}…, got {actual[:16]}…). "
                    "Nothing was installed."
                )
            }
            return

        yield {"status": "extracting", "asset": name}
        try:
            with zipfile.ZipFile(archive_path) as archive:
                _safe_extract(archive, dest)
        except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
            shutil.rmtree(dest, ignore_errors=True)
            yield {"error": f"could not unpack {name}: {exc}"}
            return
        archive_path.unlink(missing_ok=True)

        binary = _find_server(dest)
        if binary is None:
            shutil.rmtree(dest, ignore_errors=True)
            yield {"error": f"{name} contains no {server_filename()}"}
            return
        if not platform.system().lower().startswith("win"):
            # The zip loses the executable bit; without this the spawn fails with a
            # bare PermissionError that reads like a sandbox problem.
            binary.chmod(binary.stat().st_mode | 0o111)

        import json

        (dest / "install.json").write_text(
            json.dumps(
                {
                    "tag": resolved_tag,
                    "variant": variant,
                    "asset": name,
                    "binary": str(binary.relative_to(dest)),
                    "sizeBytes": completed,
                    "sha256": actual,
                    "verified": bool(expected),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        yield {
            "status": "done",
            "tag": resolved_tag,
            "variant": variant,
            "binary": str(binary),
            "verified": bool(expected),
            "sha256": actual,
        }
    finally:
        if owns_client:
            await client.aclose()


def remove_install(tag: str, variant: str) -> bool:
    path = install_dir(tag, variant)
    if not path.is_dir():
        return False
    shutil.rmtree(path, ignore_errors=True)
    return True

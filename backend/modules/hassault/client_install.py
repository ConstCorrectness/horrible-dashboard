"""Fetching and installing prebuilt `hassault-native` clients.

**Why a download and not a bundled binary.** `hassault.nativeClient` is on by
default, so the native window is the way in — but it only ever existed for people
with a Rust toolchain: `launch_native` probes `apps/native-fps/target/**` and
otherwise tells you to run `cargo build`, which is not an instruction a player can
follow. Bundling it into the installer is the obvious alternative and does not work
yet, because the desktop shell does not package the **backend** either
(`apps/desktop/src-tauri/src/backend.rs`) — a bundled client would sit beside an app
with no route to launch it. So this follows `llamacpp/binaries.py`: CI publishes a
per-platform asset on the release, and the node fetches the one it needs into
`$HORRIBLE_DATA_DIR`. It works on a checkout today and needs no change the day the
backend is packaged.

**The version is the compatibility check.** The client and the match server are two
halves of one wire protocol built from one tag, so the install is keyed by *this
build's* version rather than by a floating "latest" — the same rule as
`traces.matches_run`, where a trace may only be overlaid on a chat turn when both
halves came from the same run.

**A bare binary, not an archive.** `llama-server` arrives zipped because upstream
zips it; we control our own assets, and skipping the archive skips the whole
extraction surface with it — member-path escapes, tar symlinks, and the per-OS
"which reader" branch. The one thing extraction gave away for free is the executable
bit, which is set here instead.

**Verification is honest about what it knows**, exactly as in `binaries.py`: GitHub
publishes a `digest` for release assets, a mismatch aborts and writes nothing, and
an absent digest records `verified: false` rather than dressing a self-computed hash
up as verification.
"""

from __future__ import annotations

import hashlib
import json
import logging
import platform
import shutil
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from backend import paths
from backend.version import app_version

logger = logging.getLogger(__name__)

#: The repository CI publishes client assets to. The same one `updater.rs` reads
#: its signed manifest from — these assets ride along on the very same releases.
REPO = "horriblecpp/horrible-dashboard"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases"

#: Asset stem. The CI job in `.github/workflows/release.yml` writes exactly this,
#: and `asset_name` below is its only reader — the two are one contract and drift
#: silently if they are ever spelled apart, which is what `test_client_install.py`
#: pins.
ASSET_STEM = "hassault-native"


@dataclass(frozen=True)
class ClientInstall:
    """One downloaded client under the data dir."""

    version: str
    path: Path
    binary: Path
    size_bytes: int
    sha256: str
    verified: bool
    asset: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "path": str(self.path),
            "binary": str(self.binary),
            "sizeBytes": self.size_bytes,
            "sha256": self.sha256,
            "verified": self.verified,
            "asset": self.asset,
        }


def bin_root() -> Path:
    """Where downloaded clients live.

    Through `paths`, never `os.environ.get("HORRIBLE_DATA_DIR", ".data")` — that
    inline default resolves against whichever launcher's cwd started the backend,
    which is how an install can simply vanish between `pnpm dev` and the Tauri
    supervisor.
    """
    return paths.data_dir() / "hassault" / "bin"


def install_dir(version: str) -> Path:
    return bin_root() / version


def platform_tokens() -> tuple[str, str]:
    """(os token, arch token) as they appear in *our* asset names.

    Deliberately not `llamacpp.binaries.platform_tokens`, which answers `ubuntu`
    for Linux because that is the word upstream llama.cpp puts in its filenames.
    Ours say `linux`. Sharing the function would mean one of the two callers
    quietly asking for an asset nobody publishes.
    """
    system = platform.system().lower()
    if system.startswith("win"):
        os_token = "win"
    elif system == "darwin":
        os_token = "macos"
    else:
        os_token = "linux"
    machine = platform.machine().lower()
    arch = "arm64" if machine in {"arm64", "aarch64"} else "x64"
    return os_token, arch


def binary_filename() -> str:
    if platform.system().lower().startswith("win"):
        return f"{ASSET_STEM}.exe"
    return ASSET_STEM


def asset_name(os_token: str, arch: str) -> str:
    """The release asset this platform needs.

    Computed rather than matched — the opposite of `binaries.select_asset`, and for
    a reason that does not generalise: llama.cpp's asset names have changed shape
    several times and we get no say in it, whereas these are written by our own CI
    job three files away. Matching loosely over names we control would only turn a
    naming mistake into a wrong download instead of a clear miss.
    """
    suffix = ".exe" if os_token == "win" else ""
    return f"{ASSET_STEM}-{os_token}-{arch}{suffix}"


def read_install(version: str) -> ClientInstall | None:
    """The recorded install for `version`, or None.

    The binary existing is what makes it usable; `install.json` only carries what
    cannot be recovered from the file itself (which asset it came from, whether the
    digest was verified). A record whose binary has been deleted is not an install.
    """
    dest = install_dir(version)
    record = dest / "install.json"
    try:
        data = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    binary = dest / str(data.get("binary") or binary_filename())
    if not binary.is_file():
        return None
    return ClientInstall(
        version=str(data.get("version") or version),
        path=dest,
        binary=binary,
        size_bytes=int(data.get("sizeBytes") or 0),
        sha256=str(data.get("sha256") or ""),
        verified=bool(data.get("verified")),
        asset=str(data.get("asset") or ""),
    )


def installed_binary(version: str | None = None) -> Path | None:
    """The downloaded client for this build, if one is installed."""
    install = read_install(version or app_version())
    return install.binary if install else None


def list_installs() -> list[ClientInstall]:
    root = bin_root()
    if not root.is_dir():
        return []
    installs: list[ClientInstall] = []
    for child in sorted(root.iterdir()):
        if child.is_dir():
            install = read_install(child.name)
            if install:
                installs.append(install)
    return installs


async def fetch_release(client: httpx.AsyncClient, version: str) -> dict[str, Any]:
    """The release for `version`, asked for by tag.

    Tags are `v`-prefixed (`v0.2.0`) while the app's version is not, and the
    release workflow is triggered by the tag — so the tag is derived here rather
    than stored as a second field that could disagree with the version beside it.
    """
    res = await client.get(f"{RELEASES_API}/tags/v{version}")
    res.raise_for_status()
    data = res.json()
    return data if isinstance(data, dict) else {}


def _expected_digest(asset: dict[str, Any]) -> str:
    """The sha256 GitHub publishes for an asset, or "" when it publishes none."""
    digest = str(asset.get("digest") or "")
    prefix = "sha256:"
    return digest[len(prefix) :] if digest.startswith(prefix) else ""


async def install_client(
    version: str | None = None,
    *,
    client: httpx.AsyncClient | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Download the native client for `version`, yielding progress events.

    Events are `{status, ...}` dicts in the shape the panes already speak:
    `resolving` -> `downloading` (with completed/total) -> `verifying` -> `done`,
    or a terminal `{error}`. Every failure path removes the whole install
    directory: a half-downloaded binary that is launched anyway is worse than no
    binary at all, and `launch_native` has no way to tell one from the other by
    looking at it.
    """
    version = version or app_version()
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=None, follow_redirects=True)
    dest = install_dir(version)
    try:
        yield {"status": "resolving", "version": version}
        try:
            release = await fetch_release(client, version)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                yield {
                    "error": (
                        f"no release is published for v{version}, so there is no "
                        "prebuilt client to install. Build it from the checkout "
                        "instead: cargo build --release --manifest-path "
                        "apps/native-fps/Cargo.toml"
                    )
                }
            else:
                yield {"error": f"could not reach the releases API: {exc}"}
            return
        except httpx.HTTPError as exc:
            yield {"error": f"could not reach the releases API: {exc}"}
            return

        assets = [a for a in release.get("assets") or [] if isinstance(a, dict)]
        os_token, arch = platform_tokens()
        name = asset_name(os_token, arch)
        asset = next((a for a in assets if str(a.get("name") or "") == name), None)
        if asset is None:
            # The names it *did* see, not a bare "not found": when this fires it is
            # almost always our own CI having published under a different spelling,
            # and the list is the whole diagnosis.
            available = ", ".join(sorted(str(a.get("name") or "") for a in assets)[:12])
            yield {
                "error": (
                    f"release v{version} publishes no client for {os_token}/{arch} "
                    f"({name}). Assets seen: {available or 'none'}"
                )
            }
            return

        url = str(asset.get("browser_download_url") or "")
        expected = _expected_digest(asset)
        total = int(asset.get("size") or 0)

        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        dest.mkdir(parents=True, exist_ok=True)
        binary = dest / binary_filename()

        yield {"status": "downloading", "asset": name, "total": total, "completed": 0}
        digest = hashlib.sha256()
        completed = 0
        try:
            async with client.stream("GET", url) as res:
                res.raise_for_status()
                with open(binary, "wb") as handle:
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
                    "sha256 mismatch - the download does not match the digest GitHub "
                    f"published for {name} (expected {expected[:16]}, got "
                    f"{actual[:16]}). Nothing was installed."
                )
            }
            return

        if not platform.system().lower().startswith("win"):
            # Nothing carries the executable bit for us — there is no archive to
            # have preserved it. Without this the spawn fails with a bare
            # PermissionError that reads like a sandbox problem rather than a
            # missing chmod.
            try:
                binary.chmod(binary.stat().st_mode | 0o111)
            except OSError as exc:
                shutil.rmtree(dest, ignore_errors=True)
                yield {"error": f"could not make {name} executable: {exc}"}
                return

        (dest / "install.json").write_text(
            json.dumps(
                {
                    "version": version,
                    "asset": name,
                    "binary": binary.name,
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
            "version": version,
            "binary": str(binary),
            "verified": bool(expected),
            "sha256": actual,
            "sizeBytes": completed,
        }
    finally:
        if owns_client:
            await client.aclose()


def remove_install(version: str) -> bool:
    path = install_dir(version)
    if not path.is_dir():
        return False
    shutil.rmtree(path, ignore_errors=True)
    return True

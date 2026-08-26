"""The prebuilt-client download, and the tier it must never win.

The load-bearing test here is `test_a_downloaded_client_does_not_beat_a_local_build`.
Everything else is the download being careful; that one is the whole reason the
install is a separate tier instead of another entry in `pick_binary`'s candidate
list.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.hassault import client_install
from backend.version import FALLBACK_VERSION, app_version

REPO_ROOT = Path(__file__).resolve().parents[2]


# --- the CI contract ---------------------------------------------------------


def test_fallback_version_matches_pyproject():
    """The literal in `version.py` is the fourth copy; this is what keeps it honest.

    It only ever fires as a last resort, so nothing else would notice it drifting —
    which is exactly how `APP_VERSION` reached `0.1.0` while everything around it
    said `0.2.0`.
    """
    import tomllib

    with open(REPO_ROOT / "pyproject.toml", "rb") as handle:
        assert tomllib.load(handle)["project"]["version"] == FALLBACK_VERSION


def test_version_resolves_without_a_git_directory():
    """A packaged runtime has `pyproject.toml` beside `backend/` and no `.git`.

    This is the shape `scripts/build-backend-runtime.mjs` produces, and routing the
    lookup through `paths.repo_root()` — which requires `.git`, because it answers a
    question about where *data* belongs — would send every packaged install to
    `FALLBACK_VERSION`. That is correct only until the literal drifts, at which point
    every prebuilt-client download 404s against a tag that was never published.
    """
    import subprocess
    import sys
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "backend").mkdir()
        for name in ("__init__.py", "version.py"):
            (root / "backend" / name).write_bytes(
                (REPO_ROOT / "backend" / name).read_bytes()
            )
        (root / "pyproject.toml").write_text(
            '[project]\nname = "horrible-dashboard"\nversion = "9.9.9"\n',
            encoding="utf-8",
        )
        out = subprocess.run(
            [
                sys.executable,
                "-c",
                "from backend.version import app_version; print(app_version())",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            # An empty env would lose PATH and break the interpreter launch on Windows;
            # what matters is only that `cwd` has no `.git` anywhere above it.
            check=True,
        )
    assert out.stdout.strip() == "9.9.9", out.stderr


def test_release_workflow_publishes_the_assets_the_installer_asks_for():
    """The CI job's asset names and `asset_name` are one contract.

    They live in two files in two languages and nothing connects them at runtime:
    a rename on either side yields "release vX publishes no client for win/x64" on
    every machine, which reads like a build that failed rather than a name that
    moved.
    """
    workflow = (REPO_ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    published = set(
        re.findall(r"hassault-native-[a-z0-9]+-[a-z0-9]+(?:\.exe)?", workflow)
    )
    expected = {
        client_install.asset_name(os_token, arch)
        for os_token, arch in (
            ("win", "x64"),
            ("linux", "x64"),
            ("macos", "x64"),
            ("macos", "arm64"),
        )
    }
    assert expected <= published, (
        f"missing from release.yml: {sorted(expected - published)}"
    )


# --- the tier that must not win ----------------------------------------------


def _fake_install(version: str, *, mtime: float | None = None) -> Path:
    """A complete, resolvable downloaded install."""
    dest = client_install.install_dir(version)
    dest.mkdir(parents=True, exist_ok=True)
    binary = dest / client_install.binary_filename()
    binary.write_bytes(b"downloaded")
    (dest / "install.json").write_text(
        json.dumps(
            {
                "version": version,
                "asset": "hassault-native-test",
                "binary": binary.name,
                "sizeBytes": 10,
                "sha256": "0" * 64,
                "verified": True,
            }
        ),
        encoding="utf-8",
    )
    if mtime is not None:
        os.utime(binary, (mtime, mtime))
    return binary


def test_local_candidates_never_include_the_downloaded_install():
    """The structural half of the rule, independent of any mtime.

    `pick_binary` picks the newest of what it is handed, so the guarantee cannot
    be "the download happens to be older" — it has to be "the download is never
    handed to it at all".
    """
    from backend.modules.hassault.routes import _local_client_candidates

    bin_root = str(client_install.bin_root())
    assert all(not c.startswith(bin_root) for c in _local_client_candidates(REPO_ROOT))


def test_a_downloaded_client_does_not_beat_a_local_build(tmp_path):
    """A fresh install must not hijack a developer's older `target/debug` build.

    This is the regression. `pick_binary` deliberately prefers the newest build on
    disk — because a stale `target/release` silently beating a fresh `target/debug`
    is how a client with no weapon view model kept launching after the view model
    was written. Put the download in that comparison and the same bug returns
    through a different door: install once, and every launch afterwards runs the
    release instead of the edit under test.
    """
    fake_repo = tmp_path / "repo"
    debug = fake_repo / "apps" / "native-fps" / "target" / "debug"
    debug.mkdir(parents=True)
    local = debug / "hassault-native.exe"
    local.write_bytes(b"local build")
    old = time.time() - 3600
    os.utime(local, (old, old))

    # Downloaded *after* the local build, which is the case that used to break.
    _fake_install(app_version())

    from backend.modules.hassault.routes import _local_client_candidates, pick_binary

    chosen = pick_binary("", _local_client_candidates(fake_repo))
    assert chosen == str(local)

    # And with no local build at all, the download is what answers.
    local.unlink()
    assert pick_binary("", _local_client_candidates(fake_repo)) is None
    assert client_install.installed_binary() is not None


def test_status_reports_which_tier_answered():
    with TestClient(app) as client:
        before = client.get("/api/hassault/client/status").json()
        assert before["installed"] is False
        assert before["version"] == app_version()

        _fake_install(app_version())
        after = client.get("/api/hassault/client/status").json()
        assert after["installed"] is True
        assert after["verified"] is True
        # `source` is whatever this checkout actually has; the claim under test is
        # only that a build, when present, is not displaced by the install.
        assert after["source"] in {"build", "download", "setting"}


def test_remove_install():
    version = app_version()
    _fake_install(version)
    assert client_install.read_install(version) is not None
    assert client_install.remove_install(version) is True
    assert client_install.read_install(version) is None
    assert client_install.remove_install(version) is False


def test_a_record_without_its_binary_is_not_an_install():
    version = app_version()
    binary = _fake_install(version)
    binary.unlink()
    assert client_install.read_install(version) is None


# --- the download ------------------------------------------------------------

PAYLOAD = b"a native client, allegedly" * 64
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()


def _release(asset_name: str, *, digest: str | None) -> dict:
    asset: dict = {
        "name": asset_name,
        "size": len(PAYLOAD),
        "browser_download_url": "https://example.invalid/asset",
    }
    if digest is not None:
        asset["digest"] = f"sha256:{digest}"
    return {"tag_name": "v0.0.0-test", "assets": [asset]}


def _client(release: dict, *, status: int = 200) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/asset") or "example.invalid" in str(request.url):
            return httpx.Response(200, content=PAYLOAD)
        if status != 200:
            return httpx.Response(status, json={"message": "nope"})
        return httpx.Response(200, json=release)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _drain(version: str, client: httpx.AsyncClient) -> list[dict]:
    return [
        event async for event in client_install.install_client(version, client=client)
    ]


@pytest.mark.anyio
async def test_install_writes_a_verified_record():
    name = client_install.asset_name(*client_install.platform_tokens())
    async with _client(_release(name, digest=DIGEST)) as http:
        events = await _drain("9.9.9", http)

    assert events[-1]["status"] == "done"
    assert events[-1]["verified"] is True
    install = client_install.read_install("9.9.9")
    assert install is not None
    assert install.sha256 == DIGEST
    assert install.binary.read_bytes() == PAYLOAD


@pytest.mark.anyio
async def test_a_digest_mismatch_installs_nothing():
    """Abort and leave no trace — a partial client is worse than no client.

    `launch_native` cannot tell a truncated binary from a good one by looking, so
    the only safe failure is one that leaves nothing behind to be found.
    """
    name = client_install.asset_name(*client_install.platform_tokens())
    async with _client(_release(name, digest="f" * 64)) as http:
        events = await _drain("9.9.9", http)

    assert "sha256 mismatch" in events[-1]["error"]
    assert client_install.read_install("9.9.9") is None
    assert not client_install.install_dir("9.9.9").exists()


@pytest.mark.anyio
async def test_an_unpublished_digest_records_verified_false():
    """Not an error, and not a claim of verification either.

    Recording a hash we computed ourselves and calling it verified would be
    theatre; the record says `false` and the UI shows it.
    """
    name = client_install.asset_name(*client_install.platform_tokens())
    async with _client(_release(name, digest=None)) as http:
        events = await _drain("9.9.9", http)

    assert events[-1]["status"] == "done"
    assert events[-1]["verified"] is False
    install = client_install.read_install("9.9.9")
    assert install is not None and install.verified is False


@pytest.mark.anyio
async def test_a_release_without_our_platform_lists_what_it_did_have():
    async with _client(
        _release("hassault-native-solaris-sparc", digest=DIGEST)
    ) as http:
        events = await _drain("9.9.9", http)

    error = events[-1]["error"]
    assert "publishes no client" in error
    assert "hassault-native-solaris-sparc" in error


@pytest.mark.anyio
async def test_a_missing_release_says_to_build_it_instead():
    async with _client({}, status=404) as http:
        events = await _drain("9.9.9", http)

    assert "no release is published for v9.9.9" in events[-1]["error"]
    assert "cargo build" in events[-1]["error"]

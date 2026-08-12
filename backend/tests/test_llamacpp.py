"""llama.cpp provider: binary selection/install, the GGUF catalog, and the server.

The tests that matter most here are the ones pinning things that fail *silently*:
`--jinja` missing from the spawn (tool calls stop happening, with a 200 and a
fluent answer), a per-agent provider override that doesn't reach a call site (the
turn runs on the wrong server), and a delete route that can be walked out of the
managed directory.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.agent import roster
from backend.modules.agent.models import AgentConfig
from backend.modules.llamacpp import binaries, catalog
from backend.modules.llamacpp.server import LlamaServerManager


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    return tmp_path


# ── asset selection ──────────────────────────────────────────────────────────

# The asset listing of release b10373, verbatim except for the tag. It used to be
# a hand-written approximation that spelled every name `.zip`, and that single
# inaccuracy hid the fact that `select_asset` matched nothing at all off Windows:
# upstream ships `.zip` for Windows and `.tar.gz` everywhere else. A fixture that
# is *nearly* the real thing tests the fixture.
_ASSETS = [
    "cudart-llama-bin-win-cuda-12.4-x64.zip",
    "cudart-llama-bin-win-cuda-13.3-x64.zip",
    "llama-b4567-bin-android-arm64.tar.gz",
    "llama-b4567-bin-macos-arm64.tar.gz",
    "llama-b4567-bin-macos-x64.tar.gz",
    "llama-b4567-bin-ubuntu-arm64.tar.gz",
    "llama-b4567-bin-ubuntu-rocm-7.14-x64.tar.gz",
    "llama-b4567-bin-ubuntu-sycl-fp16-x64.tar.gz",
    "llama-b4567-bin-ubuntu-vulkan-x64.tar.gz",
    "llama-b4567-bin-ubuntu-x64.tar.gz",
    "llama-b4567-bin-win-cpu-x64.zip",
    "llama-b4567-bin-win-cuda-12.4-x64.zip",
    "llama-b4567-bin-win-rocm-7.14-x64.zip",
    "llama-b4567-bin-win-vulkan-x64.zip",
    "llama-b4567-src.zip",
]


def test_select_asset_picks_the_plain_cpu_build() -> None:
    assert (
        binaries.select_asset(_ASSETS, "win", "x64", "cpu")
        == "llama-b4567-bin-win-cpu-x64.zip"
    )
    assert (
        binaries.select_asset(_ASSETS, "ubuntu", "x64", "cpu")
        == "llama-b4567-bin-ubuntu-x64.tar.gz"
    )
    assert (
        binaries.select_asset(_ASSETS, "macos", "arm64", "cpu")
        == "llama-b4567-bin-macos-arm64.tar.gz"
    )


def test_select_asset_handles_the_tar_gz_platforms() -> None:
    """Matching only `.zip` reduced this module to a Windows feature, and said so
    in the voice of an upstream problem: "this release publishes no build for
    ubuntu/x64"."""
    assert (
        binaries.select_asset(_ASSETS, "ubuntu", "x64", "vulkan")
        == "llama-b4567-bin-ubuntu-vulkan-x64.tar.gz"
    )
    assert (
        binaries.select_asset(_ASSETS, "ubuntu", "x64", "hip")
        == "llama-b4567-bin-ubuntu-rocm-7.14-x64.tar.gz"
    )


def test_upstream_publishes_no_linux_cuda_build() -> None:
    """Pins the fact `_variant_for` exists for: CUDA is Windows-only upstream.

    If this ever starts failing because a `ubuntu-cuda-*` asset appeared, the
    special case in the hardware probe can go — this is the assertion that says
    so out loud rather than leaving it as folklore.
    """
    assert binaries.select_asset(_ASSETS, "ubuntu", "x64", "cuda") is None


def test_select_asset_never_substitutes_an_accelerator_build() -> None:
    """A CUDA archive matches every token a cpu one does, plus one. Asking for
    `cpu` and getting CUDA yields a build that fails to load its runtime on a
    machine with no NVIDIA driver — which looks like a corrupt download."""
    only_gpu = [a for a in _ASSETS if "cpu" not in a]
    assert binaries.select_asset(only_gpu, "win", "x64", "cpu") is None
    assert (
        binaries.select_asset(_ASSETS, "win", "x64", "cuda")
        == "llama-b4567-bin-win-cuda-12.4-x64.zip"
    )


def test_select_asset_ignores_the_cuda_runtime_package() -> None:
    """`cudart-…` is shorter than the real CUDA build and matches the same tokens,
    so a shortest-name tiebreak alone would pick it."""
    assert binaries.select_asset(_ASSETS, "win", "x64", "cuda") != _ASSETS[0]


def test_select_asset_rejects_the_wrong_arch() -> None:
    assert (
        binaries.select_asset(_ASSETS, "macos", "arm64", "cpu")
        != "llama-b4567-bin-macos-x64.tar.gz"
    )


# ── install ──────────────────────────────────────────────────────────────────


def _release(tmp_path: Path, digest: str | None) -> tuple[dict[str, Any], bytes]:
    """A one-asset release payload plus the zip bytes it points at."""
    archive = tmp_path / "src.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("build/bin/llama-server", "#!/bin/sh\n")
        zf.writestr("build/bin/llama-server.exe", "MZ")
    payload = archive.read_bytes()
    asset: dict[str, Any] = {
        "name": "llama-b1-bin-win-cpu-x64.zip",
        "browser_download_url": "https://example.invalid/llama.zip",
        "size": len(payload),
    }
    if digest:
        asset["digest"] = f"sha256:{digest}"
    return {"tag_name": "b1", "assets": [asset]}, payload


def _tar_release(tmp_path: Path) -> tuple[dict[str, Any], bytes]:
    """The same, as the `.tar.gz` every non-Windows platform actually gets."""
    import io
    import tarfile

    archive = tmp_path / "src.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        for name, body in (
            ("build/bin/llama-server", b"#!/bin/sh\n"),
            ("build/bin/llama-server.exe", b"MZ"),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(body)
            info.mode = 0o755
            tf.addfile(info, io.BytesIO(body))
    payload = archive.read_bytes()
    return {
        "tag_name": "b1",
        "assets": [
            {
                "name": "llama-b1-bin-ubuntu-x64.tar.gz",
                "browser_download_url": "https://example.invalid/llama.tar.gz",
                "size": len(payload),
            }
        ],
    }, payload


class _FakeStream:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    async def __aenter__(self) -> "_FakeStream":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    async def aiter_bytes(self, size: int = 0):  # noqa: ANN201 — httpx's shape
        yield self._payload


class _FakeClient:
    """Just enough httpx.AsyncClient for the install path."""

    def __init__(self, release: dict[str, Any], payload: bytes) -> None:
        self._release = release
        self._payload = payload

    async def get(self, url: str, **kw: Any):  # noqa: ANN201
        class Res:
            def __init__(self, data: dict[str, Any]) -> None:
                self._data = data

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, Any]:
                return self._data

        return Res(self._release)

    def stream(self, method: str, url: str, **kw: Any) -> _FakeStream:
        return _FakeStream(self._payload)

    async def aclose(self) -> None:
        return None


async def _run_install(client: Any, variant: str = "cpu") -> list[dict[str, Any]]:
    return [e async for e in binaries.install_server("latest", variant, client=client)]


@pytest.mark.anyio
async def test_install_unpacks_and_records_a_verified_digest(data_dir: Path) -> None:
    import hashlib

    release, payload = _release(data_dir, None)
    digest = hashlib.sha256(payload).hexdigest()
    release, payload = _release(data_dir, digest)

    events = await _run_install(_FakeClient(release, payload))
    assert events[-1]["status"] == "done", events[-1]
    assert events[-1]["verified"] is True

    install = binaries.newest_install()
    assert install is not None
    assert install.tag == "b1"
    assert install.binary.is_file()
    marker = json.loads((install.path / "install.json").read_text())
    assert marker["sha256"] == digest


@pytest.mark.anyio
async def test_install_unpacks_a_tar_gz(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Linux/macOS half of the install path, which never worked: the matcher
    demanded `.zip` and the extractor was `zipfile`, so every non-Windows install
    failed as though upstream published nothing for the platform."""
    monkeypatch.setattr(binaries, "platform_tokens", lambda: ("ubuntu", "x64"))
    release, payload = _tar_release(data_dir)

    events = await _run_install(_FakeClient(release, payload))
    assert events[-1]["status"] == "done", events[-1]

    install = binaries.newest_install()
    assert install is not None
    assert install.binary.is_file()


@pytest.mark.anyio
async def test_install_refuses_a_digest_mismatch_and_writes_nothing(
    data_dir: Path,
) -> None:
    release, payload = _release(data_dir, "0" * 64)
    events = await _run_install(_FakeClient(release, payload))
    assert "error" in events[-1]
    assert "sha256 mismatch" in events[-1]["error"]
    # The whole point: a failed verification must leave no install behind that a
    # later `newest_install()` would happily hand to the spawner.
    assert binaries.newest_install() is None


@pytest.mark.anyio
async def test_install_marks_an_unverifiable_asset_as_such(data_dir: Path) -> None:
    """GitHub does not publish a digest for every asset. Recording the hash we
    computed ourselves and calling it verification would be theatre — so the
    install is marked unverified and the UI says so."""
    release, payload = _release(data_dir, None)
    events = await _run_install(_FakeClient(release, payload))
    assert events[-1]["status"] == "done"
    assert events[-1]["verified"] is False
    install = binaries.newest_install()
    assert install is not None and install.verified is False


# ── the server process ───────────────────────────────────────────────────────


class _FakeProc:
    def __init__(self, cmd: list[str]) -> None:
        self.cmd = cmd
        self.pid = 4321
        self.stdout = iter(["loading model\n"])
        self._alive = True

    def poll(self) -> int | None:
        return None if self._alive else 0

    def terminate(self) -> None:
        self._alive = False

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        self._alive = False


def test_spawn_always_passes_jinja(data_dir: Path) -> None:
    """`--jinja` selects the model's own chat template, which carries its tool-call
    syntax. Upstream currently defaults it on, but the default has flipped before,
    `--no-jinja` exists, and `LLAMA_ARG_JINJA` in the environment can disable it —
    so it is passed explicitly rather than inherited. Losing it is silent: 200, a
    fluent answer, and every tool never called."""
    captured: list[list[str]] = []
    manager = LlamaServerManager(
        launcher=lambda cmd: captured.append(cmd) or _FakeProc(cmd)
    )
    gguf = data_dir / "model.gguf"
    gguf.write_bytes(b"GGUF")

    manager.spawn(str(gguf), alias="tiny", context_size=2048)
    cmd = captured[0]
    assert "--jinja" in cmd
    assert "--alias" in cmd and "tiny" in cmd
    assert "-c" in cmd and "2048" in cmd
    assert manager.running()
    assert manager.model_path == str(gguf)
    assert manager.alias == "tiny"

    manager.stop()
    assert not manager.running()
    # A stopped server owns no model — `resolve_model_path` keys on this, and a
    # stale path would hand the model explorer the wrong file's tensors.
    assert manager.model_path is None


def test_spawn_refuses_a_missing_gguf(data_dir: Path) -> None:
    manager = LlamaServerManager(launcher=lambda cmd: _FakeProc(cmd))
    with pytest.raises(RuntimeError, match="no GGUF"):
        manager.spawn(str(data_dir / "nope.gguf"))


# ── the catalog ──────────────────────────────────────────────────────────────


def _write_gguf(path: Path, size: int = 4096) -> None:
    """A file the header reader will reject. The catalog must still list it — an
    unreadable header is a *note on a row*, not a reason to hide a file the user
    can see in their own directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"GGUF" + b"\0" * size)


def test_catalog_lists_managed_models_and_skips_projectors(data_dir: Path) -> None:
    root = catalog.models_root()
    _write_gguf(root / "acme--tiny" / "tiny-Q4.gguf")
    _write_gguf(root / "acme--tiny" / "mmproj-tiny.gguf")

    models = catalog.list_models(include_external=False)
    names = [Path(m.path).name for m in models]
    assert "tiny-Q4.gguf" in names
    # A vision projector is a real GGUF that parses cleanly and cannot be served as
    # a chat model — offering it in the model picker is a guaranteed bad spawn.
    assert "mmproj-tiny.gguf" not in names
    assert models[0].origin == "managed"
    assert models[0].to_dict()["deletable"] is True


def test_delete_refuses_a_path_outside_the_managed_directory(data_dir: Path) -> None:
    outside = data_dir / "elsewhere" / "someone-elses.gguf"
    _write_gguf(outside)
    with pytest.raises(ValueError, match="managed"):
        catalog.delete_model(str(outside))
    assert outside.is_file()

    # …including one that walks out of it, which is how a delete route becomes an
    # arbitrary-file-delete route.
    traversal = catalog.models_root() / ".." / "elsewhere" / "someone-elses.gguf"
    with pytest.raises(ValueError, match="managed"):
        catalog.delete_model(str(traversal))
    assert outside.is_file()


def test_delete_removes_a_managed_model(data_dir: Path) -> None:
    target = catalog.models_root() / "acme--tiny" / "tiny-Q4.gguf"
    _write_gguf(target)
    catalog.delete_model(str(target))
    assert not target.exists()


@pytest.mark.anyio
async def test_download_refuses_to_blow_the_disk_budget(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Checked against the declared size before a byte is written. Checking as we
    go means discovering the disk is full 30 GB into a 40 GB download."""
    monkeypatch.setattr(catalog, "disk_budget_bytes", lambda: 1024)
    monkeypatch.setattr(catalog, "_hf_token", _none)

    class _Client:
        async def head(self, url: str, **kw: Any):  # noqa: ANN201
            class Res:
                headers = {"content-length": "999999"}

                def raise_for_status(self) -> None:
                    return None

            return Res()

        async def aclose(self) -> None:
            return None

    events = [
        e
        async for e in catalog.download_model(
            "acme/tiny-GGUF", "tiny-Q4.gguf", client=_Client()
        )
    ]
    assert "error" in events[-1]
    assert "budget" in events[-1]["error"]
    assert not list(catalog.models_root().rglob("*.gguf"))


async def _none() -> None:
    return None


# ── per-agent provider resolution ────────────────────────────────────────────


def test_resolve_provider_falls_back_to_the_saved_config(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        roster, "agent_setting", lambda agent_id, key, default=None: default
    )
    config = AgentConfig(model="m", provider="ollama", endpoint="http://ollama.test")
    info, endpoint = roster.resolve_provider(config, "coder")
    assert info.kind == "ollama"
    assert endpoint == "http://ollama.test"


def test_resolve_provider_honours_a_per_agent_override(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`provider`/`endpoint` were global while `model` was per-agent, which made
    "run the coder on the node's llama.cpp server" unexpressible: a model *name*
    means nothing on a server that doesn't have that model."""
    overrides = {("coder", "provider"): "llamacpp"}
    monkeypatch.setattr(
        roster,
        "agent_setting",
        lambda agent_id, key, default=None: overrides.get((agent_id, key), default),
    )
    config = AgentConfig(model="m", provider="ollama", endpoint="http://ollama.test")

    info, endpoint = roster.resolve_provider(config, "coder")
    assert info.kind == "llamacpp"
    # Not the saved endpoint: that one belongs to the globally configured provider.
    assert endpoint != "http://ollama.test"

    # The unoverridden agent is untouched.
    assert roster.resolve_provider(config, "dba")[0].kind == "ollama"


def test_resolve_provider_ignores_an_unknown_override(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale settings value must degrade to the configured provider, not take
    the agent down."""
    monkeypatch.setattr(
        roster,
        "agent_setting",
        lambda agent_id, key, default=None: (
            "gone-provider" if key == "provider" else default
        ),
    )
    config = AgentConfig(model="m", provider="ollama", endpoint="http://ollama.test")
    assert roster.resolve_provider(config, "coder")[0].kind == "ollama"


# ── routes ───────────────────────────────────────────────────────────────────


def test_status_and_models_routes(data_dir: Path) -> None:
    client = TestClient(app)
    status = client.get("/api/llamacpp/status")
    assert status.status_code == 200
    body = status.json()
    assert body["installed"] is False
    assert body["running"] is False

    _write_gguf(catalog.models_root() / "acme--tiny" / "tiny-Q4.gguf")
    models = client.get("/api/llamacpp/models")
    assert models.status_code == 200
    payload = models.json()
    assert any(m["name"] for m in payload["models"])
    assert payload["budgetBytes"] > 0
    # The starter list exists so first run is not an empty box demanding a repo id
    # the user has no way to guess.
    assert payload["suggested"]


def test_delete_route_refuses_an_unmanaged_path(data_dir: Path) -> None:
    outside = data_dir / "elsewhere" / "x.gguf"
    _write_gguf(outside)
    res = TestClient(app).post(
        "/api/llamacpp/models/delete", json={"path": str(outside)}
    )
    assert res.status_code == 403
    assert outside.is_file()


def test_llamacpp_is_an_openai_dialect_provider() -> None:
    """Not a new dialect: `llama-server` speaks OpenAI, and a bespoke dialect would
    silently lose the `tool_choice="required"` retry, which is gated on
    `info.dialect == "openai"`."""
    from backend.modules.agent import providers as P

    info = P.provider_for("llamacpp")
    assert info.kind == "llamacpp"
    assert info.dialect == "openai"
    assert info.can_spawn is True
    assert info.can_pull is False

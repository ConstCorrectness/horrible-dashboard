"""Borrowing `llama-server` to score a local GGUF.

The whole point of this path is "compare my fine-tune against its base", and the
server holds one model at a time — so a sweep has to evict whatever is loaded and
put it back. Everything pinned here is silent when broken: a restore that loses the
user's tuning, two targets racing over one process, or a load failure that scores as
a model getting every answer wrong.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from backend.modules.evals import llama_target
from backend.modules.llamacpp.server import LlamaServerManager


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


@pytest.fixture
def manager(monkeypatch: pytest.MonkeyPatch) -> LlamaServerManager:
    """A manager whose processes are fakes and whose health gate always passes."""
    spawns: list[list[str]] = []
    mgr = LlamaServerManager(launcher=lambda cmd: spawns.append(cmd) or _FakeProc(cmd))

    async def _ready(timeout: float = 300.0) -> bool:
        return True

    monkeypatch.setattr(mgr, "wait_ready", _ready)
    monkeypatch.setattr("backend.modules.llamacpp.server.llama_manager", mgr)

    class _Tuning:
        gpu_layers = 33
        threads = 8

    monkeypatch.setattr(
        "backend.modules.hardware.probe.defaults", lambda *a, **k: _Tuning()
    )
    mgr.spawns = spawns  # type: ignore[attr-defined]
    return mgr


def _gguf(tmp_path: Path, name: str) -> str:
    path = tmp_path / name
    path.write_bytes(b"GGUF")
    return str(path)


def test_restores_the_previous_server_exactly(
    manager: LlamaServerManager, tmp_path: Path
) -> None:
    """The evicted server is put back from its recorded spec, not from defaults.

    Restoring from defaults is the silent failure: the user's chat server comes back
    with `gpu_layers` reset and `extra_args` gone, so their machine is slower and
    nothing says why.
    """
    chat = _gguf(tmp_path, "chat.gguf")
    scored = _gguf(tmp_path, "finetune.gguf")
    manager.spawn(
        chat,
        alias="my-chat",
        context_size=16384,
        gpu_layers=99,
        threads=6,
        extra_args=["--flash-attn"],
    )

    async def go() -> None:
        async with llama_target.serving(scored) as endpoint:
            assert endpoint == manager.endpoint
            assert manager.model_path == scored

    asyncio.run(go())

    spec = manager.spawn_spec
    assert spec is not None
    assert manager.model_path == chat
    assert spec.alias == "my-chat"
    assert spec.context_size == 16384
    assert spec.gpu_layers == 99
    assert spec.threads == 6
    assert spec.extra_args == ("--flash-attn",)


def test_uses_an_already_loaded_model_in_place(
    manager: LlamaServerManager, tmp_path: Path
) -> None:
    """No stop, no reload, and no restore — nothing was taken."""
    loaded = _gguf(tmp_path, "already.gguf")
    manager.spawn(loaded, alias="already")
    before = len(manager.spawns)  # type: ignore[attr-defined]

    async def go() -> None:
        async with llama_target.serving(loaded):
            assert manager.model_path == loaded

    asyncio.run(go())

    assert len(manager.spawns) == before  # type: ignore[attr-defined]
    assert manager.running()
    assert manager.model_path == loaded


def test_restores_even_when_nothing_was_running(
    manager: LlamaServerManager, tmp_path: Path
) -> None:
    """A sweep on an idle node leaves the node idle again."""
    scored = _gguf(tmp_path, "scored.gguf")

    async def go() -> None:
        async with llama_target.serving(scored):
            assert manager.running()

    asyncio.run(go())
    assert not manager.running()


def test_a_load_that_never_becomes_ready_raises(
    manager: LlamaServerManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`wait_ready` returns False for a model too large to load. That has to reach
    the caller as a failed *run*, not as a model that answered everything wrong."""
    chat = _gguf(tmp_path, "chat.gguf")
    manager.spawn(chat, alias="my-chat")

    async def _never(timeout: float = 300.0) -> bool:
        return False

    async def go() -> None:
        async with llama_target.serving(_gguf(tmp_path, "huge.gguf")):
            pass  # pragma: no cover — the enter must raise

    monkeypatch.setattr(manager, "wait_ready", _never)
    with pytest.raises(RuntimeError, match="could not serve"):
        asyncio.run(go())

    # …and the user's own server is still put back.
    assert manager.model_path == chat


def test_a_missing_gguf_is_refused_before_anything_is_stopped(
    manager: LlamaServerManager, tmp_path: Path
) -> None:
    chat = _gguf(tmp_path, "chat.gguf")
    manager.spawn(chat, alias="my-chat")

    async def go() -> None:
        async with llama_target.serving(str(tmp_path / "nope.gguf")):
            pass  # pragma: no cover

    with pytest.raises(ValueError, match="no GGUF"):
        asyncio.run(go())
    assert manager.model_path == chat


def test_two_targets_never_share_the_process(
    manager: LlamaServerManager, tmp_path: Path
) -> None:
    """A sweep runs targets concurrently. For every other provider that is free
    parallelism; here it would have each target scoring the other's weights."""
    a = _gguf(tmp_path, "a.gguf")
    b = _gguf(tmp_path, "b.gguf")
    seen: list[tuple[str, Any]] = []

    async def one(path: str) -> None:
        async with llama_target.serving(path):
            seen.append(("enter", manager.model_path))
            await asyncio.sleep(0.01)
            # Still ours: nobody swapped the model underneath this target.
            assert manager.model_path == path
            seen.append(("exit", manager.model_path))

    async def go() -> None:
        await asyncio.gather(one(a), one(b))

    asyncio.run(go())

    # Strictly interleaved enter/exit pairs — never enter, enter, exit, exit.
    assert [kind for kind, _ in seen] == ["enter", "exit", "enter", "exit"]

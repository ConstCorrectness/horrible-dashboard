"""Serving a local GGUF for the duration of one eval target.

`llama-server` holds **one** model at a time (`spawn` refuses while a server is
running), so "score my fine-tune against its base" — the reason this module exists —
cannot be two models answering in parallel. It is one model, then the other, with a
load in between.

Three rules follow from that, and each of them is a bug if dropped:

- **One llama.cpp target at a time.** A sweep runs targets concurrently
  (`_target_semaphore`), which for every other provider is free parallelism. Here two
  of them would race over a single process and each would score the other's weights.
  The lock is held for the whole target, not just the load.
- **Put back what you evicted.** The user's own server may be their chat provider. It
  is restored from the recorded `SpawnSpec`, not from defaults — restoring from
  defaults would quietly change `gpu_layers`/`context_size`/`extra_args` and leave
  their machine slower with nothing said.
- **A load that fails must fail the run.** `wait_ready` returns False rather than
  raising when a model is too large to load, and a target that scored zero because no
  server ever came up must not look like a model that got everything wrong.

A target already served by the running server is used in place — no stop, no reload,
and no restore afterwards, because nothing was taken.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.modules.llamacpp.server import LlamaServerManager, SpawnSpec

logger = logging.getLogger(__name__)

#: Serializes every llama.cpp target across all sweeps — there is one process.
_llama_lock = asyncio.Lock()


def _same_model(a: str, b: str) -> bool:
    """Whether two GGUF paths name the same file, case-insensitively on Windows."""
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return a == b


@asynccontextmanager
async def serving(model_path: str) -> AsyncIterator[str]:
    """Yield the endpoint of a `llama-server` serving `model_path`.

    Loads it if the running server has something else (restoring that afterwards),
    and raises if it cannot be loaded. The endpoint is read *after* the spawn: the
    port is chosen then, and a server that fell back to an ephemeral port is exactly
    the case a saved `:8080` gets wrong.
    """
    from backend.modules.llamacpp.server import llama_manager

    path = str(Path(model_path).expanduser())
    if not Path(path).is_file():
        raise ValueError(f"no GGUF at {path}")

    async with _llama_lock:
        current = llama_manager.model_path
        if current and _same_model(current, path):
            # Already serving it. Make sure it has finished loading — a sweep can
            # start while the user's own server is still reading a 20 GB file.
            if not await llama_manager.wait_ready():
                raise RuntimeError(llama_manager.status().get("error") or "not ready")
            yield llama_manager.endpoint
            return

        prior = llama_manager.spawn_spec
        if prior is not None:
            await asyncio.to_thread(llama_manager.stop)
        try:
            await _spawn(path, llama_manager)
            yield llama_manager.endpoint
        finally:
            await _restore(prior, llama_manager)


async def _spawn(path: str, manager: LlamaServerManager) -> None:
    """Start a server for `path` with the machine's own tuning, and wait it out.

    `hardware.defaults()` rather than the `spawn` signature's zeros: nobody chose
    these, so the probe's answer is the only honest one — and a sweep silently
    running on CPU because `gpu_layers` defaulted to 0 would make every timing in the
    results meaningless.
    """
    from backend.modules.hardware import probe as hardware

    tuning = hardware.defaults()
    await asyncio.to_thread(
        manager.spawn, path, gpu_layers=tuning.gpu_layers, threads=tuning.threads
    )
    if not await manager.wait_ready():
        error = manager.status().get("error") or "llama-server did not start"
        await asyncio.to_thread(manager.stop)
        raise RuntimeError(f"could not serve {Path(path).name}: {error}")


async def _restore(prior: SpawnSpec | None, manager: LlamaServerManager) -> None:
    """Stop the sweep's server and put the user's back, if there was one."""
    await asyncio.to_thread(manager.stop)
    if prior is None:
        return
    try:
        await asyncio.to_thread(
            manager.spawn,
            prior.model_path,
            alias=prior.alias,
            port=prior.port,
            context_size=prior.context_size,
            gpu_layers=prior.gpu_layers,
            threads=prior.threads,
            extra_args=list(prior.extra_args),
        )
        await manager.wait_ready()
    except Exception:  # noqa: BLE001
        # Logged, never raised: the sweep's results are real and must survive a
        # failure to put the user's chat server back. The llama.cpp pane shows the
        # server as stopped, which is at least true.
        logger.exception("evals: could not restore the previous llama-server")

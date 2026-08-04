"""Run blocking kernel calls on threads the runtime never joins.

Every call into `jupyter_client` blocks, so it has to leave the event loop. The
obvious way — `asyncio.to_thread` — is the wrong one here, for a reason that only
shows up when a kernel misbehaves.

`to_thread` runs on the loop's **default executor**, and that executor is joined
*unconditionally* in two places nobody can opt out of:

- `asyncio.run` teardown, via `Runner.close()` → `loop.shutdown_default_executor()`;
- `concurrent.futures.thread._python_exit`, registered with `threading._register_atexit`.

Neither join takes a timeout. So a single blocking call that never returns stops
being a stuck *kernel* and becomes a process that will not exit — and the traceback
points at loop teardown or at interpreter exit, nowhere near the kernel. Worse, it
is un-catchable from the caller's side: `asyncio.wait_for` cancels the *await*, not
the thread, so the orphaned worker sits in the executor and the join finds it anyway.

That failure is not hypothetical: `KernelManager.shutdown_kernel(now=True)` on a
kernel that was just restarted occasionally never returns on Windows. It is an
upstream kernel-lifecycle bug and it is **not fixed here**. What is fixed is the
blast radius: on a detached daemon thread, a wedged call costs one parked coroutine
(cancellable, with a real error message) instead of a hung process.

A private `ThreadPoolExecutor` would *not* be enough — it fixes the `asyncio.run`
teardown join but is still joined by the interpreter-exit hook, which under pytest
just moves the hang from one test to the end of the session.

The trade is real and deliberate: nothing bounds these threads, so a leaked one
lives until the process dies. Use this for kernel calls specifically, where the
alternative is worse; `asyncio.to_thread` remains right for work that can be
trusted to return (see `library/clip.py` for the other precedent — a dedicated
executor, used there to avoid *starving* the default one rather than outliving it).
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")


def _label(fn: Callable[..., Any], name: str | None) -> str:
    return name or f"kernel-{getattr(fn, '__name__', 'call')}"


def _settle(
    loop: asyncio.AbstractEventLoop,
    future: asyncio.Future[Any],
    error: BaseException | None,
    result: Any,
) -> None:
    """Hand the outcome back across the thread boundary, if anyone still wants it."""

    def apply() -> None:
        if future.done():  # the awaiter gave up (cancelled / timed out)
            return
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(result)

    try:
        loop.call_soon_threadsafe(apply)
    except RuntimeError:
        # The loop closed while we were blocked; whoever was awaiting is long gone.
        pass


async def run_detached(fn: Callable[..., T], *args: Any, name: str | None = None) -> T:
    """`asyncio.to_thread`, on a daemon thread nobody joins. See the module docstring.

    Awaiting is still the caller's contract — the result and any exception come back
    exactly as `to_thread` would deliver them. The difference only shows when the
    await is abandoned: the thread is then orphaned rather than owned, which is the
    entire point.
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future[T] = loop.create_future()

    def worker() -> None:
        try:
            result = fn(*args)
        except BaseException as exc:  # noqa: BLE001 — relayed to the awaiter verbatim
            _settle(loop, future, exc, None)
        else:
            _settle(loop, future, None, result)

    threading.Thread(target=worker, daemon=True, name=_label(fn, name)).start()
    return await future


def fire_and_forget(fn: Callable[[], Any], name: str | None = None) -> None:
    """Start `fn` on a detached daemon thread and never look back.

    For lifecycle calls no caller wants the result of (restart, shutdown). A bare
    `create_task` would not do: the loop holds tasks only weakly, so it could be
    garbage-collected mid-flight.
    """
    threading.Thread(target=fn, daemon=True, name=_label(fn, name)).start()

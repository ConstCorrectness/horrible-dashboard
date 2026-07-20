"""HTTP surface for the editor's Python environment resolution.

The `lsp` WS channel stays a dumb JSON-RPC pipe; this router exposes the *environment*
the editor needs to decide things the browser can't (filesystem + subprocess): which
interpreter basedpyright should analyze against, the project root that anchors one
shared server per project, and the installed versions of the framework packages the
completion layer tracks. Results are cached in `pyenv` (per interpreter / directory),
so opening many files in a project resolves once.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from backend.modules.lsp import pyenv, symbol_index, symbol_store

router = APIRouter(prefix="/editor", tags=["editor"])

logger = logging.getLogger(__name__)

# Strong references to the detached index builds. `asyncio.create_task` only keeps a
# weak reference, so without this the auto-kick could be garbage-collected mid-build —
# which is one reason the packages corpus silently stayed empty.
_kicks: set[asyncio.Task[Any]] = set()


def _kick_symbol_index(interpreter: str) -> None:
    """Start a background build of any completion corpus that's still empty.

    The editor's completion index is only as good as these two corpora — the stdlib
    (`os`, `pathlib`, `json`, …) and the installed framework packages. Both are built
    once, in the background, the first time a Python buffer resolves its environment.
    Failures are logged rather than swallowed."""
    from backend.modules.symdex.index import symdex_index

    status = symdex_index.status()
    if status["building"]:
        return
    kinds = [k for k in ("stdlib", "packages") if not status["counts"].get(k)]
    if not kinds:
        return
    logger.info("symdex auto-kick: building %s for %s", kinds, interpreter)
    task = asyncio.create_task(symdex_index.reindex(kinds, interpreter))
    _kicks.add(task)

    def _done(t: asyncio.Task[Any]) -> None:
        _kicks.discard(t)
        try:
            result = t.result()
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001 — a build failure must not be silent
            logger.exception("symdex auto-kick failed")
            return
        if not result.get("started"):
            logger.warning("symdex auto-kick did not run: %s", result.get("reason"))
        else:
            logger.info("symdex auto-kick finished: %s", result.get("counts"))

    task.add_done_callback(_done)


class PythonEnv(BaseModel):
    """The resolved Python environment for a directory: the interpreter to analyze
    with (or None → basedpyright's default), the project root that anchors the shared
    server pool, and the installed framework-package versions (`{dist: version}`)."""

    interpreter: str | None
    root: str
    packages: dict[str, str]


@router.get("/python-env")
async def python_env(path: str = "") -> PythonEnv:
    """Resolve the interpreter, project root, and installed framework versions for a
    file/directory. Offloaded (the interpreter probe shells out) and cached in `pyenv`.
    Also the symdex package-index auto-kick: the first resolve with an interpreter
    and an empty packages corpus starts a detached background build."""
    start = path or None
    interpreter = await asyncio.to_thread(pyenv.resolve_python_interpreter, start)
    root = await asyncio.to_thread(pyenv.resolve_project_root, start)
    packages = await asyncio.to_thread(pyenv.installed_versions, interpreter)
    if interpreter:
        _kick_symbol_index(interpreter)
    return PythonEnv(
        interpreter=interpreter,
        root=root or (start if start and os.path.isdir(start) else ""),
        packages=dict(packages),
    )


# --- Completion symbol index -------------------------------------------------
#
# The editor's "intellisense" is a prefix lookup into a DB symbol index, not a
# model. The client pushes each open buffer's harvested symbols here, then queries
# `/editor/complete` on every keystroke (a plain indexed SQL prefix scan). See
# backend/modules/lsp/symbol_store.py and docs/modules/editor.mdx.


class IndexRequest(BaseModel):
    """A buffer to (re)index: `source` is the buffer URI (the index key), `lang` the
    LSP language id (only `python` is harvested in v1), `text` its current content."""

    source: str
    lang: str
    text: str


class IndexResult(BaseModel):
    count: int


class CompletionItem(BaseModel):
    symbol: str
    kind: str
    detail: str = ""
    module: str = ""
    # First docstring paragraph for indexed package symbols (symdex projection);
    # empty for buffer-local symbols.
    doc: str = ""
    # The module to import the symbol from, when accepting the completion should also
    # insert `from <imp> import <symbol>`. Empty for methods and buffer-local symbols.
    imp: str = ""


class CompletionResult(BaseModel):
    items: list[CompletionItem]


@router.post("/symbols/index")
async def index_symbols(req: IndexRequest) -> IndexResult:
    """Harvest a buffer's symbols and replace its rows in the index. Offloaded (parse
    + SQLite writes) so the event loop stays free."""
    if req.lang == "python":
        rows = await asyncio.to_thread(symbol_index.harvest_python, req.text)
    else:
        rows = []
    count = await asyncio.to_thread(
        symbol_store.replace_source, req.source, req.lang, rows
    )
    return IndexResult(count=count)


@router.get("/complete")
async def complete(
    lang: str, prefix: str, limit: int = 25, member_of: str = ""
) -> CompletionResult:
    """The hot completion path: ranked prefix matches from the symbol index. No model."""
    items = await asyncio.to_thread(
        symbol_store.query, lang, prefix, limit, member_of or None
    )
    return CompletionResult(items=[CompletionItem(**it) for it in items])

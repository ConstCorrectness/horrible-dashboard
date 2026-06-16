"""A persistent Python REPL kernel: one namespace per session.

A *cell* (one `exec` event from the pane) may hold several statements; if the last
one is a bare expression its `repr` is echoed, like an interactive prompt. State
persists across cells because the same namespace dict is reused. The kernel is pure
(no I/O of its own) — the caller passes writable streams for stdout/stderr and runs
`exec_cell` in a worker thread. See backend/modules/repl/manager.py.
"""

from __future__ import annotations

import ast
import sys
import traceback
from dataclasses import dataclass
from contextlib import redirect_stderr, redirect_stdout
from typing import Any, TextIO


@dataclass
class CellResult:
    """Outcome of one executed cell."""

    ok: bool
    value_repr: str | None = None  # repr of a trailing expression, if any
    error: str | None = None  # formatted traceback when ok is False


class ReplKernel:
    """Owns one session's globals. Not thread-safe across concurrent cells — the
    manager serializes a session's cells with a lock."""

    def __init__(self, namespace: dict[str, Any] | None = None) -> None:
        self.namespace: dict[str, Any] = namespace if namespace is not None else {}
        self.namespace.setdefault("__name__", "__repl__")
        self.namespace.setdefault("__builtins__", __builtins__)

    def exec_cell(self, code: str, stdout: TextIO, stderr: TextIO) -> CellResult:
        """Compile and run `code`, echoing a trailing expression's repr. Captures
        output into the given streams; never raises — failures come back as a
        formatted traceback on the result."""
        with redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                tree = ast.parse(code, "<repl>", "exec")
            except SyntaxError:
                return CellResult(ok=False, error=_format_syntax_error())
            if not tree.body:
                return CellResult(ok=True)
            # IPython-style echo: split off a trailing bare expression so its value
            # can be `repr`d, exec the rest as statements.
            tail = tree.body.pop() if isinstance(tree.body[-1], ast.Expr) else None
            try:
                if tree.body:
                    exec(compile(tree, "<repl>", "exec"), self.namespace)
                if tail is not None:
                    expr = ast.Expression(tail.value)  # type: ignore[attr-defined]
                    value = eval(compile(expr, "<repl>", "eval"), self.namespace)
                    if value is not None:
                        return CellResult(ok=True, value_repr=repr(value))
            except SystemExit:
                # `exit()`/`quit()` in a cell shouldn't take the server down.
                return CellResult(ok=True, value_repr="(SystemExit ignored)")
            except BaseException:  # noqa: BLE001 — any user error is reported, not raised
                return CellResult(ok=False, error=_format_user_error())
            return CellResult(ok=True)


def _format_user_error() -> str:
    """Format the current exception, dropping the kernel's own `exec_cell` frame so
    the traceback starts at the user's `<repl>` code."""
    exc_type, exc, tb = sys.exc_info()
    user_tb = tb.tb_next if tb is not None else None
    return "".join(traceback.format_exception(exc_type, exc, user_tb))


def _format_syntax_error() -> str:
    exc_type, exc, _ = sys.exc_info()
    return "".join(traceback.format_exception_only(exc_type, exc))

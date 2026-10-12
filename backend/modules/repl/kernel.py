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

    def complete(self, code: str, cursor_pos: int) -> dict[str, Any]:
        """Return completion candidates for `code` at 0-indexed `cursor_pos`."""
        if not code:
            return {"source": "kernel", "matches": [], "cursor_start": 0, "cursor_end": 0}
        pos = max(0, min(cursor_pos, len(code)))
        line, col = _pos_to_line_col(code, pos)
        matches: list[dict[str, Any]] = []
        cursor_start = pos
        try:
            import jedi

            interp = jedi.Interpreter(code, namespaces=[self.namespace])
            comps = interp.complete(line, col)
            for c in comps:
                sig = ""
                try:
                    sigs = c.get_signatures()
                    if sigs:
                        sig = sigs[0].to_string()
                except Exception:
                    pass
                matches.append({
                    "text": c.name,
                    "type": c.type or "variable",
                    "signature": sig,
                    "doc": c.docstring() or "",
                })
                # Reconstruct cursor_start from the first match prefix
                if cursor_start == pos and hasattr(c, "complete"):
                    prefix_len = len(c.name) - len(c.complete)
                    cursor_start = max(0, pos - prefix_len)
        except Exception:
            # Fallback: simple prefix match against namespace globals
            prefix = _token_at(code, pos)
            cursor_start = pos - len(prefix)
            for key, val in self.namespace.items():
                if key.startswith(prefix) and not (key.startswith("_") and not prefix.startswith("_")):
                    matches.append({
                        "text": key,
                        "type": "function" if callable(val) else "variable",
                        "signature": "",
                        "doc": getattr(val, "__doc__", "") or "",
                    })
        return {
            "source": "kernel",
            "matches": matches,
            "cursor_start": cursor_start,
            "cursor_end": pos,
        }

    def inspect(self, code: str, cursor_pos: int) -> dict[str, Any] | None:
        """Inspect the symbol at `cursor_pos` in `code`, returning docstring and signature."""
        if not code:
            return None
        pos = max(0, min(cursor_pos, len(code)))
        line, col = _pos_to_line_col(code, pos)
        symbol = _token_at(code, pos) or _token_around(code, pos)
        try:
            import jedi

            interp = jedi.Interpreter(code, namespaces=[self.namespace])
            helps = interp.help(line, col)
            if helps:
                h = helps[0]
                sig = ""
                try:
                    sigs = h.get_signatures()
                    if sigs:
                        sig = sigs[0].to_string()
                except Exception:
                    pass
                doc = h.docstring() or ""
                return {
                    "source": "kernel",
                    "title": h.name,
                    "signature": sig,
                    "body": doc,
                }
            infers = interp.infer(line, col)
            if infers:
                inf = infers[0]
                return {
                    "source": "kernel",
                    "title": inf.name,
                    "signature": f"type: {inf.name}",
                    "body": inf.docstring() or "",
                }
        except Exception:
            pass

        # Fallback: check namespace directly
        if symbol and symbol in self.namespace:
            val = self.namespace[symbol]
            val_type = type(val).__name__
            doc = getattr(val, "__doc__", "") or repr(val)
            return {
                "source": "kernel",
                "title": symbol,
                "signature": f"{symbol}: {val_type}",
                "body": str(doc),
            }
        return None


def _format_user_error() -> str:
    """Format the current exception, dropping the kernel's own `exec_cell` frame so
    the traceback starts at the user's `<repl>` code."""
    exc_type, exc, tb = sys.exc_info()
    user_tb = tb.tb_next if tb is not None else None
    return "".join(traceback.format_exception(exc_type, exc, user_tb))


def _format_syntax_error() -> str:
    exc_type, exc, _ = sys.exc_info()
    return "".join(traceback.format_exception_only(exc_type, exc))


def _pos_to_line_col(code: str, pos: int) -> tuple[int, int]:
    """Convert 0-indexed char offset into 1-indexed line and 0-indexed column."""
    clamped = max(0, min(pos, len(code)))
    lines = code[:clamped].split("\n")
    return len(lines), len(lines[-1])


def _token_at(code: str, pos: int) -> str:
    """Return the identifier prefix ending at `pos`."""
    clamped = max(0, min(pos, len(code)))
    start = clamped
    while start > 0 and (code[start - 1].isalnum() or code[start - 1] == "_"):
        start -= 1
    return code[start:clamped]


def _token_around(code: str, pos: int) -> str:
    """Return the dotted identifier around `pos`."""
    clamped = max(0, min(pos, len(code)))
    start = clamped
    end = clamped
    while start > 0 and (code[start - 1].isalnum() or code[start - 1] in "._"):
        start -= 1
    while end < len(code) and (code[end].isalnum() or code[end] in "._"):
        end += 1
    token = code[start:end].strip(".")
    return token

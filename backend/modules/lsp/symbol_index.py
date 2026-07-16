"""AST-based symbol harvesting for the completion index.

Parse only — never import — so indexing an open buffer has no side effects and
stays fast and safe. Feeds `symbol_store.replace_source`. See docs/modules/editor.mdx.
"""

from __future__ import annotations

import ast


def harvest_python(text: str) -> list[dict[str, object]]:
    """Extract completable identifiers from Python source: defs, classes, params,
    assigned names, and imported names. Returns one row per distinct symbol."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        # A buffer mid-edit is often unparsable; just skip this pass.
        return []

    out: dict[str, dict[str, object]] = {}

    def add(name: str, kind: str, detail: str = "", module: str = "") -> None:
        if not name or name.startswith("__"):
            return
        cur = out.get(name)
        if cur is None:
            out[name] = {
                "symbol": name,
                "kind": kind,
                "detail": detail,
                "module": module,
                "freq": 1,
            }
        else:
            # Seen again → rank it a little higher; keep the first kind/detail.
            cur["freq"] = int(cur["freq"]) + 1

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            add(node.name, "function", _signature(node))
            args = node.args
            for a in (*args.posonlyargs, *args.args, *args.kwonlyargs):
                add(a.arg, "variable")
            if args.vararg:
                add(args.vararg.arg, "variable")
            if args.kwarg:
                add(args.kwarg.arg, "variable")
        elif isinstance(node, ast.ClassDef):
            add(node.name, "class")
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            add(node.id, "variable")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                add(
                    (alias.asname or alias.name).split(".")[0],
                    "module",
                    module=alias.name,
                )
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    continue
                add(alias.asname or alias.name, "module", module=node.module or "")

    return list(out.values())


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """A compact `name(arg, arg)` detail string; best-effort."""
    try:
        names = [a.arg for a in (*node.args.posonlyargs, *node.args.args)]
        if node.args.vararg:
            names.append("*" + node.args.vararg.arg)
        if node.args.kwarg:
            names.append("**" + node.args.kwarg.arg)
        return f"{node.name}({', '.join(names)})"
    except Exception:
        return node.name

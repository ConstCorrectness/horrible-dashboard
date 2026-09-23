"""The dashboard's own Python APIs as a symdex corpus (`sdk:`).

Three surfaces a notebook or the REPL is written against, none of which lives in
site-packages, so neither the package nor the stdlib harvest ever saw them:

- **`dash`** — the REPL handle (`backend/modules/repl/sdk.py`) and every
  `dash.<facade>` a module registers (`registry.dash_facades["lens"] = _Lens` in a
  `dash_facade.py`). `dash` is a *global in the REPL*, not an importable module, so
  its rows carry **no import path** — completion must never offer
  `from dash.lens import grid`, which would fail.
- **`backend.sdk`** — the backend plugin contract. Importable; rows carry `imp`.
- **`horrible_train`** — the helper every training notebook imports (`ht`).

AST only, like the other harvests: the REPL module imports the relay and the
facades import their modules' stores, and indexing must not start any of that.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from backend.modules.symdex.extract_packages import (
    PackageHarvest,
    SymbolDoc,
    _first_doc_paragraph,
    _harvest_file,
    _signature,
)

ID_PREFIX = "sdk:"

_BACKEND = Path(__file__).resolve().parents[2]
_FACADE_RE = re.compile(r"dash_facades\[\s*[\"'](\w+)[\"']\s*\]\s*=\s*(\w+)")


def _method_rows(
    cls: ast.ClassDef, module: str, dist: str, out: list[SymbolDoc]
) -> None:
    """A facade's public methods, filed under `dash.<facade>` as callables."""
    for node in cls.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name.startswith("_"):
            continue
        signature = _signature(node).replace("(self, ", "(").replace("(self)", "()")
        doc = _first_doc_paragraph(node)
        full = ast.get_docstring(node, clean=True) or ""
        out.append(
            SymbolDoc(
                id=f"{ID_PREFIX}{dist}:{module}.{node.name}",
                text=f"function {module}.{node.name}{signature}"
                + (f"\n{doc}" if doc else ""),
                metadata={
                    "dist": dist,
                    "module": module,
                    "qualname": node.name,
                    "kind": "function",
                    "signature": signature,
                    "doc": doc,
                    # The whole docstring, for the reference pane and the generated
                    # MDX — `doc` is the first paragraph, as everywhere in the index.
                    "fullDoc": full,
                },
                symbol=node.name,
                kind="function",
                detail=signature,
                module=module,
                doc=doc,
                freq=2,
                imp="",  # not importable — `dash` is a REPL global
            )
        )


def _classes(path: Path) -> dict[str, ast.ClassDef]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError, ValueError):
        return {}
    return {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}


def harvest_dash(backend: Path = _BACKEND) -> list[SymbolDoc]:
    out: list[SymbolDoc] = []
    sdk = backend / "modules" / "repl" / "sdk.py"
    classes = _classes(sdk)
    handle = classes.get("Dash")
    if handle is not None:
        _method_rows(handle, "dash", "dash", out)
        # `self.panes = _Panes(call)` in `Dash.__init__` names each sub-handle.
        for node in ast.walk(handle):
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target, value = node.targets[0], node.value
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
                and isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id in classes
            ):
                _method_rows(classes[value.func.id], f"dash.{target.attr}", "dash", out)
    for facade in sorted(backend.glob("modules/*/dash_facade.py")):
        text = facade.read_text(encoding="utf-8", errors="replace")
        found = _classes(facade)
        for name, cls_name in _FACADE_RE.findall(text):
            if cls_name in found:
                _method_rows(found[cls_name], f"dash.{name}", "dash", out)
    return out


def extract_sdk(backend: Path = _BACKEND) -> list[PackageHarvest]:
    harvests = [PackageHarvest(dist="dash", docs=harvest_dash(backend))]

    plugin: list[SymbolDoc] = []
    for py in sorted((backend / "sdk").glob("*.py")):
        if py.name.startswith("_") and py.name != "__init__.py":
            continue
        module = "backend.sdk" if py.name == "__init__.py" else f"backend.sdk.{py.stem}"
        _harvest_file(py, module, "backend.sdk", plugin, id_prefix=ID_PREFIX)
    harvests.append(PackageHarvest(dist="backend.sdk", docs=plugin))

    helper = (
        backend / "modules" / "training" / "helper" / "horrible_train" / "__init__.py"
    )
    train: list[SymbolDoc] = []
    if helper.is_file():
        _harvest_file(
            helper, "horrible_train", "horrible_train", train, id_prefix=ID_PREFIX
        )
    harvests.append(PackageHarvest(dist="horrible_train", docs=train))
    return [h for h in harvests if h.docs]

"""Static package-symbol extraction for the symdex index.

Walks the curated framework packages (`lsp.pyenv.FRAMEWORK_PACKAGES`) inside the
resolved interpreter's site-packages with stdlib **ast only — never importing**
the package (imports run arbitrary side effects and can take seconds each).
Harvests module/class/function symbols with an `ast.unparse`d signature and the
first docstring paragraph, emitting both the embeddable `SymbolDoc`s and the
relational `code_symbols` rows (source `pkg:<dist>`) in one pass. Bounded by
per-package file/symbol caps so a giant dist (torch) stays a few seconds, not
minutes. The one subprocess here (the site-packages probe) is run by the caller
on a thread — the Windows `--reload` loop can't spawn asyncio subprocesses (see
lsp/manager.py).
"""

from __future__ import annotations

import ast
import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.modules.lsp.pyenv import FRAMEWORK_PACKAGES

logger = logging.getLogger(__name__)

# Cost controls: per-package caps + skip lists. Private modules (`_*`), vendored
# trees, and tests carry noise, not API surface.
MAX_FILES_PER_PACKAGE = 200
MAX_SYMBOLS_PER_PACKAGE = 1500
MAX_FILE_BYTES = 500_000
DOC_CAP = 500
_SKIP_DIRS = {"tests", "test", "testing", "_vendor", "vendor", "__pycache__"}


@dataclass
class SymbolDoc:
    """One embeddable symbol: the vector-store document plus its projection into
    the `code_symbols` prefix index."""

    id: str
    text: str
    metadata: dict[str, Any]
    # code_symbols row fields
    symbol: str
    kind: str
    detail: str
    module: str
    doc: str
    freq: int = 1

    def store_row(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "kind": self.kind,
            "detail": self.detail,
            "module": self.module,
            "doc": self.doc,
            "freq": self.freq,
        }


@dataclass
class PackageHarvest:
    dist: str
    docs: list[SymbolDoc] = field(default_factory=list)


def site_packages_for(interpreter: str) -> list[Path]:
    """The interpreter's site-packages dirs, probed with one short subprocess.
    Sync (subprocess.run) — call it on a thread."""
    code = (
        "import sysconfig, json\n"
        "paths = sysconfig.get_paths()\n"
        "print(json.dumps(sorted({paths.get('purelib', ''), paths.get('platlib', '')})))\n"
    )
    try:
        out = subprocess.run(
            [interpreter, "-c", code],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        ).stdout.strip()
        return [Path(p) for p in json.loads(out) if p and Path(p).is_dir()]
    except (OSError, subprocess.SubprocessError, ValueError):
        logger.warning("site-packages probe failed for %s", interpreter)
        return []


def _first_doc_paragraph(node: ast.AST) -> str:
    doc = ast.get_docstring(node, clean=True) or ""
    if not doc:
        return ""
    return doc.split("\n\n", 1)[0].strip()[:DOC_CAP]


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    try:
        sig = f"({ast.unparse(node.args)})"
    except Exception:  # noqa: BLE001 — never let one odd signature kill a harvest
        sig = "(…)"
    if node.returns is not None:
        try:
            sig += f" -> {ast.unparse(node.returns)}"
        except Exception:  # noqa: BLE001
            pass
    return sig


def _module_name(py_file: Path, package_root: Path, import_name: str) -> str:
    """Dotted module path for a file under the package dir (`__init__` collapses
    onto its package)."""
    rel = py_file.relative_to(package_root.parent)
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    dotted = ".".join(parts)
    return dotted or import_name


def _harvest_file(py_file: Path, module: str, dist: str, out: list[SymbolDoc]) -> None:
    try:
        source = py_file.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
    except (OSError, SyntaxError, ValueError):
        return
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("_"):
                continue
            _emit(out, dist, module, node.name, "function", _signature(node), node)
        elif isinstance(node, ast.ClassDef):
            if node.name.startswith("_"):
                continue
            _emit(out, dist, module, node.name, "class", "", node, freq=3)
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if sub.name.startswith("_") and sub.name != "__init__":
                        continue
                    _emit(
                        out,
                        dist,
                        module,
                        f"{node.name}.{sub.name}",
                        "method",
                        _signature(sub),
                        sub,
                        symbol=sub.name,
                        member_of=node.name,
                    )


def _emit(
    out: list[SymbolDoc],
    dist: str,
    module: str,
    qualname: str,
    kind: str,
    signature: str,
    node: ast.AST,
    *,
    symbol: str | None = None,
    member_of: str | None = None,
    freq: int = 1,
) -> None:
    doc = _first_doc_paragraph(node)
    text = f"{kind} {module}.{qualname}{signature}"
    if doc:
        text += f"\n{doc}"
    out.append(
        SymbolDoc(
            id=f"pkg:{dist}:{module}.{qualname}",
            text=text,
            metadata={
                "dist": dist,
                "module": module,
                "qualname": qualname,
                "kind": kind,
                "signature": signature,
                "doc": doc,
                **({"member_of": member_of} if member_of else {}),
            },
            symbol=symbol or qualname,
            kind=kind,
            detail=signature or kind,
            # Methods index under their class so member queries can scope; plain
            # symbols under their module.
            module=member_of or module,
            doc=doc,
            freq=2 if kind == "class" else freq,
        )
    )


def extract_packages(interpreter: str) -> list[PackageHarvest]:
    """Harvest every installed curated package in `interpreter`'s environment.
    Sync and CPU/file-system bound — call it on a thread."""
    roots = site_packages_for(interpreter)
    harvests: list[PackageHarvest] = []
    if not roots:
        return harvests
    for import_name, dist in sorted(FRAMEWORK_PACKAGES.items()):
        top = import_name.split(".", 1)[0]
        pkg_dir = next((r / top for r in roots if (r / top).is_dir()), None)
        if pkg_dir is None:
            # Single-module dists (rare among the curated set) — a bare top.py.
            mod_file = next(
                (r / f"{top}.py" for r in roots if (r / f"{top}.py").is_file()), None
            )
            if mod_file is None:
                continue
            docs: list[SymbolDoc] = []
            _harvest_file(mod_file, top, dist, docs)
            harvests.append(PackageHarvest(dist=dist, docs=docs))
            continue
        docs = []
        files = 0
        for py_file in sorted(pkg_dir.rglob("*.py")):
            if files >= MAX_FILES_PER_PACKAGE or len(docs) >= MAX_SYMBOLS_PER_PACKAGE:
                break
            parts = py_file.relative_to(pkg_dir).parts
            if any(p in _SKIP_DIRS for p in parts[:-1]):
                continue
            # Private modules stay out (but a package's own __init__ stays in).
            if any(p.startswith("_") and p != "__init__.py" for p in parts):
                continue
            try:
                if py_file.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            files += 1
            _harvest_file(py_file, _module_name(py_file, pkg_dir, top), dist, docs)
        del docs[MAX_SYMBOLS_PER_PACKAGE:]
        harvests.append(PackageHarvest(dist=dist, docs=docs))
    return harvests

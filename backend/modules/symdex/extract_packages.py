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
import inspect
import json
import logging
import re
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

#: Bigger budgets for the packages a research notebook lives in. torch alone has
#: ~830 public `.py` files; the default cap reaches a quarter of them.
PACKAGE_FILE_CAPS: dict[str, int] = {"torch": 450, "transformers": 400}
PACKAGE_SYMBOL_CAPS: dict[str, int] = {"torch": 5000, "transformers": 4000}

#: Subtrees walked **first**, in this order (paths relative to the package dir,
#: `/`-separated, no `.py`). The cap is a budget, and the walk used to spend it
#: alphabetically: on torch that was 123 files of `torch.ao` (quantization) before
#: anything else, and `torch.nn`, `torch.optim` and `torch.utils.data` were never
#: reached at all. A more specific entry must precede its parent (`utils/data`
#: before `utils`) — the first match wins.
PRIORITY_SUBTREES: dict[str, tuple[str, ...]] = {
    "torch": (
        "functional",
        "serialization",
        "random",
        "nn/modules",
        "nn/functional",
        "nn/init",
        "nn/parameter",
        "nn/utils",
        "nn",
        "optim",
        "utils/data",
        "utils/checkpoint",
        "autograd",
        "amp",
        "cuda",
        "linalg",
        "fft",
        "special",
        "distributions",
        "func",
        "compiler",
        "export",
        "profiler",
        "backends",
        "utils",
    ),
    "transformers": (
        "trainer",
        "training_args",
        "modeling_utils",
        "configuration_utils",
        "tokenization_utils_base",
        "tokenization_utils",
        "generation",
        "pipelines",
        "trainer_callback",
        "data",
        "models/auto",
        "models/llama",
        "models/qwen2",
        "models/gemma",
        "models/mistral",
        "models/gpt2",
        "models/bert",
    ),
    "peft": ("peft_model", "config", "mapping", "tuners/lora", "utils"),
    "trl": ("trainer",),
    "datasets": (
        "arrow_dataset",
        "load",
        "dataset_dict",
        "features",
        "iterable_dataset",
    ),
    "accelerate": ("accelerator", "utils"),
}

#: Subtrees walked **last** — real but rarely what a notebook reaches for, and big
#: enough to swallow the budget. `transformers/models` is ~400 model directories;
#: the handful people use are named in `PRIORITY_SUBTREES` above.
DEFERRED_SUBTREES: dict[str, tuple[str, ...]] = {
    "torch": (
        "ao",
        "distributed",
        "onnx",
        "fx/experimental",
        "nn/quantized",
        "nn/qat",
        "nn/intrinsic",
        "nn/quantizable",
        "utils/benchmark",
        "utils/tensorboard",
        "jit",
        "package",
        "testing",
    ),
    "transformers": ("models", "commands", "onnx", "kernels"),
}

#: Private files that are nonetheless the documentation of a package's public API.
#: torch writes the docs of every C-implemented function (`torch.matmul`,
#: `Tensor.view`, …) as `add_docstr(...)` calls in these files — ~970 of them — and
#: the walk skips `_*` files, so a parse-only harvest saw none of them.
DOCSTR_FILES: dict[str, tuple[str, ...]] = {
    "torch": ("_torch_docs.py", "_tensor_docs.py"),
}
_ADD_DOCSTR = {"add_docstr", "_add_docstr"}
_ADD_DOCSTR_ALL = {"add_docstr_all"}


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
    # The module the symbol can be imported *from* (`from <imp> import <symbol>`),
    # so accepting a completion can insert the import line. Distinct from `module`,
    # which for a method holds its owning class (that's the member-scoping key, not
    # something importable) — methods therefore carry an empty `imp`.
    imp: str = ""

    def store_row(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "kind": self.kind,
            "detail": self.detail,
            "module": self.module,
            "doc": self.doc,
            "freq": self.freq,
            "imp": self.imp,
        }


@dataclass
class PackageHarvest:
    dist: str
    docs: list[SymbolDoc] = field(default_factory=list)


def site_packages_for(interpreter: str) -> list[Path]:
    """The interpreter's site-packages dirs, probed with one short subprocess.
    Sync (subprocess.run) — call it on a thread.

    Includes the **user** site-packages, not just the prefix ones. `sysconfig` only
    reports the prefix scheme, so on a `pip install --user` layout (the Windows
    default for a non-venv interpreter) it points at a near-empty directory and every
    installed package — torch, transformers, the lot — is invisible to the harvest."""
    code = (
        "import sysconfig, site, json\n"
        "paths = sysconfig.get_paths()\n"
        "dirs = {paths.get('purelib', ''), paths.get('platlib', '')}\n"
        "try:\n"
        "    dirs.update(site.getsitepackages())\n"
        "except Exception:\n"
        "    pass\n"
        "try:\n"
        "    if site.ENABLE_USER_SITE is not False:\n"
        "        dirs.add(site.getusersitepackages())\n"
        "except Exception:\n"
        "    pass\n"
        "print(json.dumps(sorted(d for d in dirs if d)))\n"
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


def _harvest_file(
    py_file: Path,
    module: str,
    dist: str,
    out: list[SymbolDoc],
    id_prefix: str = "pkg:",
) -> None:
    """Harvest one file's public API. `id_prefix` namespaces the emitted document
    ids so a second corpus (the stdlib, `std:`) can reuse this walk without its
    rows colliding with the packages corpus."""
    try:
        source = py_file.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
    except (OSError, SyntaxError, ValueError):
        return
    before = len(out)
    for node in tree.body:
        # `conv1d = _add_docstr(torch.conv1d, r"""...""")` — how torch.nn.functional,
        # torch.linalg, torch.fft and torch.special document C functions. An
        # assignment, not a def, so the branches below never see it.
        docstr = _assigned_docstr(node)
        if docstr is not None:
            name, text = docstr
            if not name.startswith("_"):
                _emit_docstr(out, dist, module, name, text, id_prefix=id_prefix)
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("_"):
                continue
            _emit(
                out,
                dist,
                module,
                node.name,
                "function",
                _signature(node),
                node,
                id_prefix=id_prefix,
            )
        elif isinstance(node, ast.ClassDef):
            if node.name.startswith("_"):
                continue
            _emit(
                out,
                dist,
                module,
                node.name,
                "class",
                "",
                node,
                freq=3,
                id_prefix=id_prefix,
            )
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
                        id_prefix=id_prefix,
                    )
    _apply_assigned_docs(tree, out, before)
    _emit_reexports(tree, out, before, dist, module, id_prefix)


def _apply_assigned_docs(tree: ast.Module, out: list[SymbolDoc], before: int) -> None:
    """`AdamW.__doc__ = (...)` after the class body — how torch.optim documents
    every optimizer, so `ast.get_docstring` on the class itself finds nothing."""
    by_name = {d.metadata.get("qualname"): d for d in out[before:]}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not (
            isinstance(target, ast.Attribute)
            and target.attr == "__doc__"
            and isinstance(target.value, ast.Name)
        ):
            continue
        doc = by_name.get(target.value.id)
        text = _literal_text(node.value)
        if doc is None or doc.doc or not text:
            continue
        para = inspect.cleandoc(text).split("\n\n", 1)[0].strip()[:DOC_CAP]
        doc.doc = para
        doc.metadata["doc"] = para
        doc.text = f"{doc.text}\n{para}"


def _emit_reexports(
    tree: ast.Module,
    out: list[SymbolDoc],
    before: int,
    dist: str,
    module: str,
    id_prefix: str,
) -> None:
    """Emit the names in a module's `__all__` that the AST walk couldn't see.

    A parse-only harvest misses two very common cases: symbols implemented in C
    (`collections.defaultdict`, `datetime.datetime`) and symbols re-exported from a
    submodule (`pandas.DataFrame`, `typing.List`). Both are exactly the names people
    import, and both are listed in `__all__` — so take that list at its word. These
    rows carry no signature or docstring, but they carry the right import module,
    which is what makes the completion insert `from collections import defaultdict`."""
    names: list[str] = []
    for node in tree.body:
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target]
            if isinstance(node, ast.AnnAssign)
            else []
        )
        if not any(isinstance(t, ast.Name) and t.id == "__all__" for t in targets):
            continue
        if isinstance(node.value, (ast.List, ast.Tuple)):
            names = [
                e.value
                for e in node.value.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)
            ]
    if not names:
        return
    seen = {d.symbol for d in out[before:]}
    for name in names:
        if name in seen or name.startswith("_"):
            continue
        seen.add(name)
        out.append(
            SymbolDoc(
                id=f"{id_prefix}{dist}:{module}.{name}",
                text=f"{module}.{name}",
                metadata={
                    "dist": dist,
                    "module": module,
                    "qualname": name,
                    "kind": "variable",
                    "signature": "",
                    "doc": "",
                },
                symbol=name,
                kind="variable",
                detail=module,
                module=module,
                doc="",
                imp=module,
            )
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
    id_prefix: str = "pkg:",
) -> None:
    doc = _first_doc_paragraph(node)
    text = f"{kind} {module}.{qualname}{signature}"
    if doc:
        text += f"\n{doc}"
    out.append(
        SymbolDoc(
            id=f"{id_prefix}{dist}:{module}.{qualname}",
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
            imp="" if member_of else module,
        )
    )


def _literal_text(node: ast.AST | None) -> str | None:
    """The text of a docstring expression, statically.

    torch builds these as a raw string literal, a literal with
    `.format(**common_args)` applied, or a `+` of the two. The `.format` arguments are dicts built at import time, which a
    parse-only walk cannot evaluate, so `{input}` is rendered as the bare name — a
    reader sees *which* argument the sentence is about, and nothing is invented."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _literal_text(node.left), _literal_text(node.right)
        if left is None or right is None:
            return None
        return left + right
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "format"
    ):
        base = _literal_text(node.func.value)
        if base is None:
            return None
        text = _FORMAT_FIELD.sub(lambda m: m.group(1), base)
        return text.replace("{{", "{").replace("}}", "}")
    if isinstance(node, ast.JoinedStr):
        return "".join(
            v.value
            for v in node.values
            if isinstance(v, ast.Constant) and isinstance(v.value, str)
        )
    return None


_FORMAT_FIELD = re.compile(r"(?<!\{)\{(\w+)\}(?!\})")


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
    return ""


def _assigned_docstr(node: ast.stmt) -> tuple[str, str] | None:
    """`name = _add_docstr(target, text)` gives `(name, text)`."""
    if not isinstance(node, ast.Assign) or len(node.targets) != 1:
        return None
    target = node.targets[0]
    if not isinstance(target, ast.Name) or _call_name(node.value) not in _ADD_DOCSTR:
        return None
    call = node.value
    assert isinstance(call, ast.Call)
    if len(call.args) < 2:
        return None
    text = _literal_text(call.args[1])
    return (target.id, text) if text is not None else None


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        head = _dotted(node.value)
        return f"{head}.{node.attr}" if head else ""
    return ""


def _split_docstr(name: str, text: str) -> tuple[str, str, str]:
    """(signature, first paragraph, full text) from a torch-style docstring.

    torch's convention is a first line restating the call —
    `matmul(input, other, *, out=None) -> Tensor` — then a blank line, then prose.
    That line *is* the signature (the function is C, so there is nothing else to
    unparse), and it is dropped from the doc so the popup does not say it twice."""
    body = text.strip("\n")
    lines = body.strip().splitlines()
    signature = ""
    if lines:
        first = lines[0].strip()
        bare = name.rsplit(".", 1)[-1]
        # `linalg.cholesky(A, ...)` restates the call with its namespace.
        if re.match(rf"(?:\w+\.)*{re.escape(bare)}\(", first):
            signature = first[first.index("(") :]
            body = "\n".join(lines[1:])
    full = inspect.cleandoc(body) if body.strip() else ""
    para = full.split("\n\n", 1)[0].strip()[:DOC_CAP] if full else ""
    return signature, para, full


def _emit_docstr(
    out: list[SymbolDoc],
    dist: str,
    module: str,
    qualname: str,
    text: str,
    *,
    member_of: str | None = None,
    id_prefix: str = "pkg:",
) -> None:
    signature, para, _full = _split_docstr(qualname, text)
    kind = "method" if member_of else "function"
    full_qual = f"{member_of}.{qualname}" if member_of else qualname
    body = f"{kind} {module}.{full_qual}{signature}"
    if para:
        body += f"\n{para}"
    out.append(
        SymbolDoc(
            id=f"{id_prefix}{dist}:{module}.{full_qual}",
            text=body,
            metadata={
                "dist": dist,
                "module": module,
                "qualname": full_qual,
                "kind": kind,
                "signature": signature,
                "doc": para,
                **({"member_of": member_of} if member_of else {}),
            },
            symbol=qualname,
            kind=kind,
            detail=signature or kind,
            module=member_of or module,
            doc=para,
            freq=2,
            imp="" if member_of else module,
        )
    )


def harvest_docstr_file(
    py_file: Path, top: str, dist: str, out: list[SymbolDoc], id_prefix: str = "pkg:"
) -> None:
    """Harvest a file of bare `add_docstr(torch.X, ...)` / `add_docstr_all("x", ...)`
    statements (see `DOCSTR_FILES`). The *target* names the symbol, not the file:
    `torch.abs` lands in module `torch`, and `add_docstr_all("view", ...)` is
    `Tensor.view`, because that is what the helper does with its string."""
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError, ValueError):
        return
    for node in tree.body:
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        name = _call_name(call)
        if len(call.args) < 2:
            continue
        text = _literal_text(call.args[1])
        if text is None:
            continue
        if name in _ADD_DOCSTR_ALL:
            method = call.args[0]
            if isinstance(method, ast.Constant) and isinstance(method.value, str):
                if not method.value.startswith("_"):
                    _emit_docstr(
                        out,
                        dist,
                        top,
                        method.value,
                        text,
                        member_of="Tensor",
                        id_prefix=id_prefix,
                    )
            continue
        if name not in _ADD_DOCSTR:
            continue
        dotted = _dotted(call.args[0])
        parts = dotted.split(".")
        if len(parts) < 2 or parts[0] != top or any(p.startswith("_") for p in parts):
            continue
        module, qualname = ".".join(parts[:-1]), parts[-1]
        member_of = None
        if module.endswith(".Tensor"):
            module, member_of = module.rsplit(".", 1)
        _emit_docstr(
            out, dist, module, qualname, text, member_of=member_of, id_prefix=id_prefix
        )


def walk_rank(rel: str, top: str) -> tuple[int, int, str]:
    """Sort key for one file in a package walk (`rel` is `/`-separated, no `.py`).

    Deferred subtrees go last and priority subtrees go first, in their listed order;
    everything else sits between, **shallow before deep** — the top of a package is
    its API (`transformers/trainer.py`), the bottom is its implementation."""
    if rel == "__init__":
        return (0, 0, rel)

    def hit(prefix: str) -> bool:
        return rel == prefix or rel.startswith(prefix + "/")

    depth = rel.count("/")
    for prefix in DEFERRED_SUBTREES.get(top, ()):
        if hit(prefix):
            return (10_000, depth, rel)
    priority = PRIORITY_SUBTREES.get(top, ())
    for i, prefix in enumerate(priority):
        if hit(prefix):
            return (1 + i, depth, rel)
    return (1 + len(priority), depth, rel)


def _dedupe(docs: list[SymbolDoc]) -> list[SymbolDoc]:
    """One row per id, keeping the one that says the most. The `__all__` pass emits
    a bare `torch.abs` with no doc; the `add_docstr` pass emits the real one."""
    best: dict[str, SymbolDoc] = {}
    order: list[str] = []
    for doc in docs:
        prev = best.get(doc.id)
        if prev is None:
            order.append(doc.id)
            best[doc.id] = doc
        elif (bool(doc.doc), bool(doc.metadata.get("signature"))) > (
            bool(prev.doc),
            bool(prev.metadata.get("signature")),
        ):
            best[doc.id] = doc
    return [best[i] for i in order]


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
        harvests.append(
            PackageHarvest(dist=dist, docs=harvest_package_dir(pkg_dir, top, dist))
        )
    return harvests


def harvest_package_dir(pkg_dir: Path, top: str, dist: str) -> list[SymbolDoc]:
    """Walk one package directory in `walk_rank` order under its caps, then add the
    `DOCSTR_FILES` it documents C functions in."""
    file_cap = PACKAGE_FILE_CAPS.get(top, MAX_FILES_PER_PACKAGE)
    symbol_cap = PACKAGE_SYMBOL_CAPS.get(top, MAX_SYMBOLS_PER_PACKAGE)
    candidates: list[tuple[tuple[int, int, str], Path]] = []
    for py_file in pkg_dir.rglob("*.py"):
        parts = py_file.relative_to(pkg_dir).parts
        if any(p in _SKIP_DIRS for p in parts[:-1]):
            continue
        # Private modules stay out (but a package's own __init__ stays in).
        if any(p.startswith("_") and p != "__init__.py" for p in parts):
            continue
        rel = "/".join(parts)[:-3]
        if rel.endswith("/__init__"):
            rel = rel[: -len("/__init__")]
        candidates.append((walk_rank(rel, top), py_file))
    candidates.sort(key=lambda c: c[0])
    docs: list[SymbolDoc] = []
    files = 0
    for _rank, py_file in candidates:
        if files >= file_cap or len(docs) >= symbol_cap:
            break
        try:
            if py_file.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        files += 1
        _harvest_file(py_file, _module_name(py_file, pkg_dir, top), dist, docs)
    del docs[symbol_cap:]
    # Outside the cap on purpose: these are the core API's only documentation, and
    # a budget spent before reaching them is exactly the bug the ranking fixes.
    for name in DOCSTR_FILES.get(top, ()):
        path = pkg_dir / name
        if path.is_file():
            harvest_docstr_file(path, top, dist, docs)
    return _dedupe(docs)

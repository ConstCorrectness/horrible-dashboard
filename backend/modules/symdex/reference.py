"""The Python reference: read a symbol's whole documentation, for the reference pane.

`code_symbols` holds the **first paragraph** of every docstring — right for a
completion popup, and the reason the reference pane cannot read from it alone:
`torch.nn.Linear`'s shape table, `json.dumps`'s argument list and every "Example"
section are past the first blank line. This resolves the full text on demand, one
symbol at a time, by parsing the defining file — **never importing it**, the rule
every symdex harvest follows (an import runs the package, and torch's takes
seconds and a GPU probe).

Where the text is not in a `def`/`class` at all — torch documents its C functions
with `add_docstr(torch.matmul, ...)` in `_torch_docs.py` — the same parser the
harvest uses reads it from there (`extract_packages.DOCSTR_FILES`).

Every answer carries an **upstream** link to the version that is actually
installed: the docs for torch 2.8 are not the docs for torch 2.4, and a link to
"latest" quietly shows arguments the user's install does not have.
"""

from __future__ import annotations

import ast
import inspect
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote

from backend.modules.symdex import extract_packages as ep
from backend.modules.symdex.extract_stdlib import stdlib_dir_for

_BACKEND = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=8)
def python_version(interpreter: str) -> str:
    """`3.13` for the interpreter the index was built from. Sync; call on a thread."""
    try:
        return subprocess.run(
            [interpreter, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "3"


@lru_cache(maxsize=8)
def _roots(interpreter: str) -> tuple[Path | None, tuple[Path, ...]]:
    return stdlib_dir_for(interpreter), tuple(ep.site_packages_for(interpreter))


def _module_file(base: Path, module: str) -> Path | None:
    parts = module.split(".")
    as_file = base.joinpath(*parts).with_suffix(".py")
    if as_file.is_file():
        return as_file
    as_pkg = base.joinpath(*parts, "__init__.py")
    return as_pkg if as_pkg.is_file() else None


def locate(source: str, module: str, interpreter: str | None) -> Path | None:
    """The file that defines `module` for this corpus. `sdk:` modules are files in
    this repo; the other two resolve against the indexed interpreter."""
    corpus = source.split(":", 1)[0]
    if corpus == "sdk":
        if module == "horrible_train":
            path = _BACKEND / "modules/training/helper/horrible_train/__init__.py"
            return path if path.is_file() else None
        if module.startswith("backend.sdk"):
            return _module_file(_BACKEND.parent, module)
        return None  # `dash.*` handles are resolved by class, below
    if not interpreter:
        return None
    stdlib, sites = _roots(interpreter)
    if corpus == "std" and stdlib is not None:
        return _module_file(stdlib, module)
    for root in sites:
        found = _module_file(root, module)
        if found is not None:
            return found
    return None


def _find(tree: ast.Module, dotted: str) -> ast.AST | None:
    """`Linear` or `Linear.forward` inside a parsed module."""
    body: list[ast.stmt] = tree.body
    node: ast.AST | None = None
    for part in dotted.split("."):
        node = next(
            (
                n
                for n in body
                if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name == part
            ),
            None,
        )
        if node is None:
            return None
        body = getattr(node, "body", [])
    return node


def _assigned_doc(tree: ast.Module, name: str) -> str:
    """`AdamW.__doc__ = (...)` or `conv1d = _add_docstr(...)` at module level."""
    for stmt in tree.body:
        pair = ep._assigned_docstr(stmt)
        if pair and pair[0] == name:
            return ep._split_docstr(name, pair[1])[2]
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            t = stmt.targets[0]
            if (
                isinstance(t, ast.Attribute)
                and t.attr == "__doc__"
                and isinstance(t.value, ast.Name)
                and t.value.id == name
            ):
                text = ep._literal_text(stmt.value)
                if text:
                    return inspect.cleandoc(text)
    return ""


def _docstr_table(top: str, root: Path, module: str, name: str) -> tuple[str, str]:
    """(signature, full text) from a torch-style `add_docstr` file, if it lists
    `module.name`. Resolved the way `harvest_docstr_file` files each call: the target
    `torch.matmul` is module `torch`, and `add_docstr_all("view", ...)` is
    `Tensor.view` in module `torch`."""
    member = name.split(".")[-1]
    want_tensor = name == f"Tensor.{member}"
    for file_name in ep.DOCSTR_FILES.get(top, ()):
        path = root / file_name
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError, ValueError):
            continue
        for stmt in tree.body:
            if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
                continue
            call = stmt.value
            if len(call.args) < 2:
                continue
            kind = ep._call_name(call)
            first = call.args[0]
            if kind in ep._ADD_DOCSTR_ALL:
                hit = (
                    want_tensor
                    and module == top
                    and isinstance(first, ast.Constant)
                    and first.value == member
                )
            elif kind in ep._ADD_DOCSTR:
                target = ep._dotted(first)
                expected = f"{module}.{name}" if not want_tensor else f"{top}.Tensor.{member}"
                hit = target == expected
            else:
                hit = False
            if hit:
                text = ep._literal_text(call.args[1]) or ""
                sig, _para, full = ep._split_docstr(member, text)
                return sig, full
    return "", ""


def _reexport_origin(tree: ast.Module, module: str, name: str) -> str | None:
    """Where `from .clip_grad import clip_grad_norm_` says `name` really lives.

    `__all__` re-exports are how big packages assemble their public surface, and
    the index files each such name under the package that re-exports it — with no
    docstring, because there is no definition there to read. Following the import
    one hop is what turns that dead end into the page for the real function.
    """
    top_level = module.rsplit(".", 1)[0] if "." in module else module
    for stmt in tree.body:
        if not isinstance(stmt, ast.ImportFrom):
            continue
        if not any((a.asname or a.name) == name for a in stmt.names):
            continue
        base = stmt.module or ""
        if stmt.level:
            # `module` is the package itself when the file is its `__init__`.
            anchor = module.split(".")
            anchor = anchor[: len(anchor) - (stmt.level - 1)]
            return ".".join([*anchor, base] if base else anchor)
        return base or top_level
    return None


def full_doc(
    source: str, module: str, name: str, interpreter: str | None, _hops: int = 0
) -> dict[str, Any]:
    """Signature + whole docstring + where it was read from. Sync; call on a thread."""
    path = locate(source, module, interpreter)
    signature, doc, line = "", "", 0
    if path is not None:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError, ValueError):
            tree = None
        if tree is not None:
            node = _find(tree, name)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                signature = ep._signature(node)
            if node is not None:
                doc = ast.get_docstring(node, clean=True) or ""
                line = int(getattr(node, "lineno", 0))
            if not doc:
                doc = _assigned_doc(tree, name.split(".")[-1])
            if node is None and not doc and "." not in name and _hops < 3:
                origin = _reexport_origin(tree, module, name)
                if origin and origin != module:
                    followed = full_doc(source, origin, name, interpreter, _hops + 1)
                    if followed["doc"] or followed["signature"]:
                        followed["definedIn"] = origin
                        return followed
    corpus, _, dist = source.partition(":")
    if not doc and corpus == "pkg" and interpreter:
        top = module.split(".")[0]
        _std, sites = _roots(interpreter)
        for root in sites:
            if (root / top).is_dir():
                sig, text = _docstr_table(top, root / top, module, name)
                if text:
                    signature, doc = signature or sig, text
                    path = None
                break
    if not doc and corpus == "sdk" and module.startswith("dash"):
        doc = _dash_doc(module, name)
    return {
        "source": source,
        "module": module,
        "name": name,
        "signature": signature,
        "doc": inspect.cleandoc(doc) if doc else "",
        "file": str(path) if path else "",
        "line": line,
    }


def _dash_doc(module: str, name: str) -> str:
    """A `dash.<handle>.<method>` docstring, found the way the harvest found it."""
    from backend.modules.symdex import extract_sdk

    for doc in extract_sdk.harvest_dash(_BACKEND):
        if doc.module == module and doc.symbol == name:
            return str(doc.metadata.get("fullDoc") or doc.doc)
    return ""


# --- upstream -----------------------------------------------------------------


def _minor(version: str) -> str:
    """`2.8.0+cu128` -> `2.8`; docs sites are versioned by minor release."""
    core = version.split("+", 1)[0]
    return ".".join(core.split(".")[:2])


def upstream(
    source: str,
    module: str,
    name: str,
    *,
    versions: dict[str, str],
    python: str,
) -> dict[str, str] | None:
    """A link to the upstream documentation **for the installed version**, labelled
    with what kind of link it is — an exact anchor, a versioned search, or just the
    release's page. None for our own SDKs, which are documented in this repo."""
    corpus, _, dist = source.partition(":")
    leaf = name.split(".")[0]
    if corpus == "std":
        page = module.lower()
        return {
            "url": f"https://docs.python.org/{python}/library/{page}.html#{module}.{name}",
            "label": f"Python {python} docs",
            "exact": "anchor",
        }
    if corpus != "pkg":
        return None
    version = versions.get(dist, "")
    if dist == "torch" and version:
        v = _minor(version)
        return {
            "url": f"https://docs.pytorch.org/docs/{v}/search.html?q={quote(leaf)}",
            "label": f"PyTorch {v} docs (search)",
            "exact": "search",
        }
    if (
        dist in {"transformers", "datasets", "peft", "trl", "accelerate", "tokenizers"}
        and version
    ):
        return {
            "url": f"https://huggingface.co/docs/{dist}/v{version}/en/index",
            "label": f"{dist} v{version} docs",
            "exact": "release",
        }
    if dist == "numpy" and version:
        v = _minor(version)
        return {
            "url": f"https://numpy.org/doc/{v}/search.html?q={quote(leaf)}",
            "label": f"NumPy {v} docs (search)",
            "exact": "search",
        }
    if dist == "pandas" and version:
        return {
            "url": f"https://pandas.pydata.org/pandas-docs/version/{version}/search.html?q={quote(leaf)}",
            "label": f"pandas {version} docs (search)",
            "exact": "search",
        }
    project = dist
    return {
        "url": f"https://pypi.org/project/{project}/{version}/"
        if version
        else f"https://pypi.org/project/{project}/",
        "label": f"{project} {version} on PyPI".strip(),
        "exact": "release",
    }

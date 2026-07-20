"""Static Python **standard library** symbol extraction for the symdex index.

The counterpart to `extract_packages`: same AST-only walk (never importing), but
pointed at the interpreter's own stdlib directory rather than site-packages. This
is what makes `os.path.join`, `pathlib.Path`, `json.dumps`, `asyncio.gather`, …
appear in editor completion with a real signature and docstring — previously the
only stdlib knowledge in the index was the 185 bare `builtins` names seeded by
`lsp/symbol_store.py`, with no signature and no doc.

**Two projections, two scopes.** The relational `code_symbols` projection covers
the *whole* stdlib (it's a cheap SQLite prefix scan, and completion must be
exhaustive). The embedding projection covers only `STDLIB_EMBED` — the modules
worth spending vectors on for agent semantic retrieval. Embedding every stdlib
symbol would be tens of thousands of vectors against a local embedder for very
little retrieval gain; see docs/modules/symdex.mdx.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from backend.modules.symdex.extract_packages import (
    MAX_FILE_BYTES,
    PackageHarvest,
    SymbolDoc,
    _harvest_file,
    _module_name,
)

logger = logging.getLogger(__name__)

# The stdlib id namespace (`KIND_PREFIXES["stdlib"]`), kept here so the extractor
# and the index agree on one spelling.
ID_PREFIX = "std:"

# Modules whose symbols also get **embedded** for semantic search. Everything else
# is still fully harvested into the relational prefix index — this list only bounds
# the vector cost. Chosen as the modules an agent actually reasons about.
STDLIB_EMBED: frozenset[str] = frozenset(
    {
        "argparse",
        "asyncio",
        "base64",
        "collections",
        "contextlib",
        "csv",
        "dataclasses",
        "datetime",
        "enum",
        "functools",
        "hashlib",
        "http",
        "inspect",
        "io",
        "itertools",
        "json",
        "logging",
        "math",
        "os",
        "pathlib",
        "random",
        "re",
        "shutil",
        "socket",
        "sqlite3",
        "statistics",
        "string",
        "subprocess",
        "tempfile",
        "textwrap",
        "threading",
        "time",
        "typing",
        "unittest",
        "urllib",
        "uuid",
    }
)

# Directories under the stdlib root that carry no API surface worth indexing:
# the test suite, the editor, vendored/legacy trees, and site-packages (that's the
# packages corpus, harvested separately and curated).
_SKIP_TOP = {
    "site-packages",
    "dist-packages",
    "test",
    "tests",
    "idlelib",
    "lib2to3",
    "turtledemo",
    "__pycache__",
    "config",
    "venv",
    "ensurepip",
    "pydoc_data",
    "encodings",
}

# Per-module caps. The stdlib has a long tail of large single files (typing.py,
# argparse.py); these keep a full harvest in the low seconds.
MAX_FILES_PER_MODULE = 60
MAX_SYMBOLS_PER_MODULE = 600


def stdlib_dir_for(interpreter: str) -> Path | None:
    """The interpreter's stdlib directory, probed with one short subprocess (the
    running backend's own stdlib is not necessarily the one the user's code targets).
    Sync (subprocess.run) — call it on a thread."""
    code = (
        "import sysconfig, json\n"
        "print(json.dumps(sysconfig.get_paths().get('stdlib', '')))\n"
    )
    try:
        out = subprocess.run(
            [interpreter, "-c", code],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        ).stdout.strip()
        path = Path(json.loads(out))
        return path if path.is_dir() else None
    except (OSError, subprocess.SubprocessError, ValueError):
        logger.warning("stdlib probe failed for %s", interpreter)
        return None


def _harvest_module_dir(pkg_dir: Path, top: str) -> list[SymbolDoc]:
    """Harvest a stdlib *package* (a directory), bounded by the per-module caps."""
    docs: list[SymbolDoc] = []
    files = 0
    for py_file in sorted(pkg_dir.rglob("*.py")):
        if files >= MAX_FILES_PER_MODULE or len(docs) >= MAX_SYMBOLS_PER_MODULE:
            break
        parts = py_file.relative_to(pkg_dir).parts
        if any(p in _SKIP_TOP for p in parts[:-1]):
            continue
        # Private submodules stay out; a package's own __init__ stays in.
        if any(p.startswith("_") and p != "__init__.py" for p in parts):
            continue
        try:
            if py_file.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        files += 1
        _harvest_file(
            py_file, _module_name(py_file, pkg_dir, top), top, docs, id_prefix=ID_PREFIX
        )
    del docs[MAX_SYMBOLS_PER_MODULE:]
    return docs


def extract_stdlib(interpreter: str) -> list[PackageHarvest]:
    """Harvest the whole importable standard library of `interpreter`, one
    `PackageHarvest` per top-level module (`dist` is the module name, so the
    relational rows land under source `std:<module>`). Sync and file-system bound
    — call it on a thread."""
    root = stdlib_dir_for(interpreter)
    if root is None:
        return []
    harvests: list[PackageHarvest] = []
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return []
    for entry in entries:
        top = entry.name[:-3] if entry.name.endswith(".py") else entry.name
        # Private and dunder modules carry no public API surface.
        if top.startswith("_") or top in _SKIP_TOP:
            continue
        if entry.is_dir():
            if not (entry / "__init__.py").is_file():
                continue
            docs = _harvest_module_dir(entry, top)
        elif entry.name.endswith(".py"):
            try:
                if entry.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            docs = []
            _harvest_file(entry, top, top, docs, id_prefix=ID_PREFIX)
            del docs[MAX_SYMBOLS_PER_MODULE:]
        else:
            continue
        if docs:
            harvests.append(PackageHarvest(dist=top, docs=docs))
    return harvests

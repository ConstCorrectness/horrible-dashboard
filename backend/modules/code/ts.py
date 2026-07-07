"""Tree-sitter adapter + definition extraction.

The `tree-sitter-language-pack` build on this platform exposes an **all-methods**
node API (`node.kind()`, `node.start_position()`, `node.named_child(i)`, …) that
differs from property-based `py-tree-sitter`. `_get` reads an accessor whether it's
a method or a property, so extraction survives either binding. Parsing takes a
`str`; a node's name text is sliced from the UTF-8 bytes by its byte range (this
binding has no `node.text`).

Defs only, by design (see docs/modules/code.mdx) — functions, methods, classes,
interfaces, type aliases, enums. That's what the outline and symbol search need;
the index stays cheap and doesn't try to model references or call graphs.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from tree_sitter_language_pack import get_parser

from backend.modules.code.models import Position, Range, Symbol

_EXT_LANG: dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
    ".jsx": "tsx",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
}


def lang_for_path(path: Path | str) -> str | None:
    """The tree-sitter language for a file path, or None if we don't index it."""
    return _EXT_LANG.get(Path(path).suffix.lower())


_local = threading.local()


def _parser(lang: str) -> Any:
    """A parser for `lang`, cached **per thread**. The native tree-sitter `Parser`
    is unsendable across threads (pyo3 panics if one is used off the thread that
    created it), and FastAPI runs sync route handlers in a threadpool — so a single
    shared/`lru_cache`d parser crashes. A thread-local cache keeps each parser on the
    thread that made it while still avoiding a fresh parser per call."""
    cache: dict[str, Any] | None = getattr(_local, "parsers", None)
    if cache is None:
        cache = {}
        _local.parsers = cache
    parser = cache.get(lang)
    if parser is None:
        parser = get_parser(lang)  # type: ignore[arg-type]  # runtime str is fine
        cache[lang] = parser
    return parser


def _get(obj: Any, attr: str, *args: Any) -> Any:
    """Read a tree-sitter accessor whether the binding exposes it as a method or a
    property (this platform's build makes them all methods)."""
    v = getattr(obj, attr)
    return v(*args) if callable(v) else v


# node kind -> our friendly symbol kind
_PY_KINDS = {"function_definition": "function", "class_definition": "class"}
_TS_KINDS = {
    "function_declaration": "function",
    "generator_function_declaration": "function",
    "class_declaration": "class",
    "abstract_class_declaration": "class",
    "method_definition": "method",
    "interface_declaration": "interface",
    "type_alias_declaration": "type",
    "enum_declaration": "enum",
}
# `const x = () => …` / `const f = function() {}` — a def hiding in a declarator.
_TS_FUNC_VALUES = {
    "arrow_function",
    "function",
    "function_expression",
    "generator_function",
}
# kinds whose name scopes their descendants (so nested defs get a container)
_CONTAINER_KINDS = {"class", "function", "method"}


def _name(node: Any, src: bytes) -> str | None:
    nm = _get(node, "child_by_field_name", "name")
    if nm is None:
        return None
    return src[_get(nm, "start_byte") : _get(nm, "end_byte")].decode("utf-8", "replace")


def _range(node: Any) -> Range:
    s = _get(node, "start_position")
    e = _get(node, "end_position")
    return Range(
        start=Position(line=_get(s, "row") + 1, column=_get(s, "column") + 1),
        end=Position(line=_get(e, "row") + 1, column=_get(e, "column") + 1),
    )


def _def_of(node: Any, is_python: bool, src: bytes) -> tuple[str, str] | None:
    """(friendly_kind, name) for a definition node, else None."""
    kind = _get(node, "kind")
    table = _PY_KINDS if is_python else _TS_KINDS
    if kind in table:
        name = _name(node, src)
        return (table[kind], name) if name else None
    if not is_python and kind == "variable_declarator":
        value = _get(node, "child_by_field_name", "value")
        if value is not None and _get(value, "kind") in _TS_FUNC_VALUES:
            name = _name(node, src)
            return ("function", name) if name else None
    return None


def extract_symbols(lang: str, text: str) -> list[Symbol]:
    """Parse `text` and return its definitions in document order, each tagged with
    its enclosing class/function name (`container`)."""
    is_python = lang == "python"
    src = text.encode("utf-8")
    tree = _parser(lang).parse(text)
    root = tree.root_node
    root = root() if callable(root) else root
    out: list[Symbol] = []

    def walk(node: Any, container: str | None) -> None:
        child_container = container
        hit = _def_of(node, is_python, src)
        if hit is not None:
            kind, name = hit
            out.append(
                Symbol(name=name, kind=kind, range=_range(node), container=container)
            )
            if kind in _CONTAINER_KINDS:
                child_container = name
        for i in range(_get(node, "named_child_count")):
            walk(_get(node, "named_child", i), child_container)

    walk(root, None)
    return out

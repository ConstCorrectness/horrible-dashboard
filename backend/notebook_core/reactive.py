"""Marimo-style reactive dataflow for notebook cells.

Each code cell is statically analyzed for the module-level names it **defines** and
the free names it **references**; a dependency DAG is built (cell B depends on cell A
when B reads a name A defines). Running a cell then re-runs its transitive dependents
in topological order, so editing an upstream value propagates automatically.

Analysis uses the stdlib `symtable`, which resolves scoping (functions, classes,
comprehensions, `global`/`nonlocal`, walrus) correctly — a name read inside a nested
function that resolves to a module global is a real cross-cell dependency, while a
comprehension target or closure variable is not. This is far more robust than a
hand-rolled AST scope walk.

Rules enforced (surfaced as diagnostics, not exceptions): a variable defined by more
than one cell (`multiple_defs`) and dependency cycles (`cycle`). Cells that fail to
parse (`syntax`) are excluded from the graph.
"""

from __future__ import annotations

import ast
import builtins
import symtable
from collections import deque
from dataclasses import dataclass, field

_BUILTINS = frozenset(dir(builtins))


@dataclass
class CellAnalysis:
    defs: set[str] = field(default_factory=set)  # module-level names this cell binds
    refs: set[str] = field(default_factory=set)  # free names it reads (dependencies)
    star_import: bool = False  # `from x import *` — defines unknowable names
    parse_error: str | None = None  # SyntaxError message; excluded from the graph


@dataclass
class Diagnostic:
    cellId: str
    kind: str  # 'multiple_defs' | 'cycle' | 'syntax'
    message: str
    names: list[str] = field(default_factory=list)


def _walk(
    table: symtable.SymbolTable, defs: set[str], refs: set[str], module: bool
) -> None:
    for sym in table.get_symbols():
        name = sym.get_name()
        if module:
            if sym.is_assigned() or sym.is_imported():
                defs.add(name)
            elif sym.is_referenced() and not sym.is_parameter():
                refs.add(name)
        else:
            # Inside a nested scope, a name that resolves to a module global (implicit
            # or via `global`) is a cross-cell dependency; free/closure vars are not.
            if sym.is_referenced() and sym.is_global():
                refs.add(name)
    for child in table.get_children():
        _walk(child, defs, refs, module=False)


def analyze(source: str) -> CellAnalysis:
    """Static defs/refs for one code cell. Never raises."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return CellAnalysis(parse_error=str(exc))
    try:
        table = symtable.symtable(source, "<cell>", "exec")
    except SyntaxError as exc:  # pragma: no cover — ast.parse would have caught it
        return CellAnalysis(parse_error=str(exc))

    defs: set[str] = set()
    refs: set[str] = set()
    _walk(table, defs, refs, module=True)

    # Python 3.12 inlines comprehensions (PEP 709), so `symtable` surfaces their
    # `for`-targets at the enclosing scope — but they're semantically isolated and
    # never real module bindings. Strip them (walrus targets stay: they DO bind).
    comp_targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.comprehension):
            for t in ast.walk(node.target):
                if isinstance(t, ast.Name):
                    comp_targets.add(t.id)
    defs -= comp_targets

    star = any(
        isinstance(node, ast.ImportFrom)
        and any(alias.name == "*" for alias in node.names)
        for node in ast.walk(tree)
    )
    # A cell never depends on itself, and builtins are not cross-cell deps.
    refs -= defs
    refs -= _BUILTINS
    return CellAnalysis(defs=defs, refs=refs, star_import=star)


class ReactiveGraph:
    """A dependency DAG over code cells, in document order."""

    def __init__(self) -> None:
        self.analyses: dict[str, CellAnalysis] = {}
        self.provider: dict[str, str] = {}  # name -> the single cell that defines it
        self.edges: dict[str, set[str]] = {}  # cell -> dependent cells
        self.order: list[str] = []  # topological order of code cells
        self.diagnostics: list[Diagnostic] = []

    @classmethod
    def build(cls, cells: list[tuple[str, str]]) -> ReactiveGraph:
        """`cells` = [(cell_id, source)] for code cells in document order."""
        g = cls()
        g.order = [cid for cid, _ in cells]
        g.edges = {cid: set() for cid, _ in cells}
        g.analyses = {cid: analyze(src) for cid, src in cells}

        # Provider map + multiple-definition detection.
        conflicts: dict[str, list[str]] = {}
        for cid, _ in cells:
            a = g.analyses[cid]
            if a.parse_error:
                g.diagnostics.append(Diagnostic(cid, "syntax", a.parse_error))
                continue
            for name in a.defs:
                if name in g.provider and g.provider[name] != cid:
                    conflicts.setdefault(name, [g.provider[name]]).append(cid)
                else:
                    g.provider[name] = cid
        for name, owners in conflicts.items():
            # An ambiguous name creates no edges; flag every cell that defines it.
            for cid in owners:
                g.diagnostics.append(
                    Diagnostic(
                        cid,
                        "multiple_defs",
                        f"'{name}' is defined in multiple cells",
                        [name],
                    )
                )
            g.provider.pop(name, None)

        # Edges: A -> B when B reads a name A provides.
        for cid, _ in cells:
            a = g.analyses[cid]
            if a.parse_error:
                continue
            for name in a.refs:
                owner = g.provider.get(name)
                if owner is not None and owner != cid:
                    g.edges[owner].add(cid)

        g._topo_sort()
        return g

    def _topo_sort(self) -> None:
        """Kahn topo sort; cells left in a cycle get a `cycle` diagnostic."""
        indeg = {cid: 0 for cid in self.order}
        for deps in self.edges.values():
            for d in deps:
                indeg[d] += 1
        # Preserve document order among ready cells for stable output.
        ready = deque(cid for cid in self.order if indeg[cid] == 0)
        out: list[str] = []
        while ready:
            cid = ready.popleft()
            out.append(cid)
            for d in self.order:  # stable: iterate in document order
                if d in self.edges[cid]:
                    indeg[d] -= 1
                    if indeg[d] == 0:
                        ready.append(d)
        if len(out) != len(self.order):
            in_cycle = [cid for cid in self.order if cid not in out]
            for cid in in_cycle:
                self.diagnostics.append(
                    Diagnostic(cid, "cycle", "cell is part of a dependency cycle")
                )
            out.extend(in_cycle)  # keep them runnable (they'll just error)
        self.order = out

    def downstream(self, cell_id: str) -> set[str]:
        """All transitive dependents of `cell_id` (excluding itself)."""
        seen: set[str] = set()
        q = deque(self.edges.get(cell_id, ()))
        while q:
            cid = q.popleft()
            if cid in seen:
                continue
            seen.add(cid)
            q.extend(self.edges.get(cid, ()))
        seen.discard(cell_id)
        return seen

    def run_order(self, cell_id: str) -> list[str]:
        """`cell_id` plus its transitive dependents, in topological order."""
        affected = {cell_id} | self.downstream(cell_id)
        return [cid for cid in self.order if cid in affected]

    def has_diagnostic(self, cell_id: str) -> bool:
        return any(d.cellId == cell_id for d in self.diagnostics)

    def to_payload(self) -> dict:
        return {
            "edges": [
                {"from": src, "to": dst}
                for src, dsts in self.edges.items()
                for dst in dsts
            ],
            "defs": {cid: sorted(a.defs) for cid, a in self.analyses.items()},
            "diagnostics": [
                {
                    "cellId": d.cellId,
                    "kind": d.kind,
                    "message": d.message,
                    "names": d.names,
                }
                for d in self.diagnostics
            ],
        }

"""Python source → a design graph: the other half of the round trip.

The `.py` file is the structural source of truth, so this is what makes an edit in the
code pane land on the canvas. It reads the shape `codegen.py` emits — `__init__`
assignments become nodes, `forward` statements become edges — and recovers node
identity from the `# horrible:node=` markers, which is what keeps your layout instead
of re-arranging the canvas on every save.

Three rules make it honest rather than merely clever:

- **It only claims to read what it can read.** A class it cannot map onto nodes is not
  guessed at: it is preserved verbatim as a `custom.module`, and the caller is told.
  Nothing is ever silently dropped and nothing is ever partially imported.
- **The code is the fixed point.** The guarantee under test is
  `emit(parse(emit(g))) == emit(g)` — parse and regenerate and the file does not
  churn. That is stronger than IR equality in the way that matters, because it is what
  a user experiences when they hit save.
- **A name is recovered only when it is not derivable.** Codegen names attributes
  `norm_1`, `attn_1`, …; this replays that counter, and only records an explicit
  `name` when the source disagrees. Recording every attribute name would round-trip
  correctly and permanently pin names the user never chose.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

from backend.modules.interpretability.graph import primitives, spec as specs
from backend.modules.interpretability.graph.codegen import MARKER
from backend.modules.interpretability.graph.models import (
    DesignGraph,
    GraphEdge,
    GraphNode,
    SubGraph,
)

_MARKER_LINE = re.compile(rf"#\s*{MARKER}=([\w:-]+)\s*$")

#: Constructor expression → (node type, the params its positional arguments fill).
#:
#: This is the one place the generator is mirrored rather than reused: `init_fn`
#: builds a string and a string cannot be run backwards. `test_model_graph` pins the
#: mirror by asserting every spec that emits a constructor appears here, so a node
#: type added on one side cannot quietly go unreadable on the other.
CONSTRUCTORS: dict[str, tuple[str, tuple[str, ...]]] = {
    "RMSNorm": ("norm.rms", ("dim",)),
    "nn.LayerNorm": ("norm.layer", ("dim",)),
    "MultiHeadAttention": ("attn.mha", ("dim",)),
    "nn.Linear": ("ffn.linear", ("dim", "out_features")),
    "SwiGLU": ("ffn.swiglu", ("dim", "hidden")),
    "GeGLU": ("ffn.geglu", ("dim", "hidden")),
    "MLP": ("ffn.mlp", ("dim", "hidden")),
    "MoE": ("ffn.moe", ("dim", "hidden")),
    "nn.Dropout": ("op.dropout", ("p",)),
    # Both embeddings are `nn.Embedding`; `forward` tells them apart, because only the
    # positional one indexes a `torch.arange`. Defaulting to the token embedding here
    # and correcting in `forward` keeps the ambiguity in one place.
    "nn.Embedding": ("embed.token", ("vocab_size", "dim")),
}

#: Elementwise calls in `forward` that are nodes without constructors.
CALL_NODES: dict[str, str] = {
    "F.silu": "act.silu",
    "F.gelu": "act.gelu",
    "F.relu": "act.relu",
    "torch.tanh": "act.tanh",
}


class ParseError(ValueError):
    """The source is not Python, or holds no model class at all."""


@dataclass
class ParseResult:
    graph: DesignGraph
    #: Class names preserved verbatim as `custom.module` nodes because they could not
    #: be mapped. Surfaced in the pane — an opaque import the reader is not told about
    #: is indistinguishable from a wrong one.
    opaque: list[str] = field(default_factory=list)
    #: Things worth saying that are not failures.
    warnings: list[str] = field(default_factory=list)


def parse_module(source: str) -> ParseResult:
    """Read a generated (or generated-then-edited) module back into a design."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ParseError(f"line {exc.lineno}: {exc.msg}") from exc

    markers = _markers(source)
    lines = source.splitlines()

    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    # Primitives are ours and are re-emitted from `primitives.py`; carrying them into
    # the graph would make every design contain a copy of RMSNorm as user code.
    candidates = [c for c in classes if c.name not in primitives.PRIMITIVES]
    if not candidates:
        raise ParseError(
            "No model class found — nothing here defines an nn.Module to read."
        )

    scopes: dict[str, _Scope] = {}
    opaque_source: dict[str, str] = {}
    for cls in candidates:
        scope = _read_class(cls, markers)
        if scope is None:
            opaque_source[cls.name] = _source_of(lines, cls)
        else:
            scopes[cls.name] = scope

    if not scopes:
        raise ParseError(
            "No class here matches the shape the designer generates, so there is nothing to draw. "
            "The file is unchanged."
        )

    instantiated = {name for scope in scopes.values() for name in scope.instantiates}
    roots = [name for name in scopes if name not in instantiated]
    root_name = roots[-1] if roots else list(scopes)[-1]

    result = ParseResult(graph=DesignGraph(name=root_name))
    graph = result.graph
    graph.config = scopes[root_name].config

    for name, scope in scopes.items():
        if name == root_name:
            continue
        _as_group(scope)
        graph.groups.append(
            SubGraph(
                id=_group_id(name), name=name, nodes=scope.nodes, edges=scope.edges
            )
        )

    root = scopes[root_name]
    graph.nodes = root.nodes
    graph.edges = root.edges

    # A group's own `__init__` signature is a subset of the root's; the root is the
    # only one whose config describes the model.
    for scope in scopes.values():
        for key, value in scope.config.items():
            graph.config.setdefault(key, value)

    for node in _all_nodes(graph):
        if node.type == "custom.module":
            cls_name = str(node.params.get("class_name", ""))
            if cls_name in opaque_source:
                node.params["code"] = opaque_source[cls_name]
                if cls_name not in result.opaque:
                    result.opaque.append(cls_name)

    for name in opaque_source:
        if name not in result.opaque:
            result.warnings.append(
                f"{name} is defined here but nothing instantiates it; it was left out of the graph."
            )

    return result


def _as_group(scope: "_Scope") -> None:
    """Turn a scope's terminals into a group's.

    Every class is read as though it were the root, because which one *is* the root
    depends on what the other classes instantiate — knowable only after all of them
    have been read. This is the correction, applied once that is known.
    """
    for node in scope.nodes:
        if node.type == "io.input":
            node.type = "io.group_input"
        elif node.type == "io.output":
            node.type = "io.group_output"


def _all_nodes(graph: DesignGraph) -> list[GraphNode]:
    return [*graph.nodes, *(node for group in graph.groups for node in group.nodes)]


def _group_id(class_name: str) -> str:
    return f"grp_{class_name.lower()}"


def _markers(source: str) -> dict[int, str]:
    """1-based line → node id, from the trailing marker comments."""
    out: dict[int, str] = {}
    for index, line in enumerate(source.splitlines(), start=1):
        found = _MARKER_LINE.search(line)
        if found:
            out[index] = found.group(1)
    return out


def _source_of(lines: list[str], node: ast.AST) -> str:
    start = getattr(node, "lineno", 1) - 1
    end = getattr(node, "end_lineno", start + 1)
    return "\n".join(lines[start:end])


def _dotted(node: ast.AST) -> str:
    """`nn.Linear` from an Attribute chain; `` for anything else."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else ""
    return ""


def _self_attr(node: ast.AST) -> str:
    """`norm_1` from `self.norm_1`, else ``."""
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    ):
        return node.attr
    return ""


@dataclass
class _Scope:
    """One class, read into nodes and edges."""

    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
    config: dict[str, int | float | bool | str] = field(default_factory=dict)
    instantiates: set[str] = field(default_factory=set)


def _read_class(cls: ast.ClassDef, markers: dict[int, str]) -> _Scope | None:
    """Read one class, or None when it is not the shape the designer emits."""
    init = next(
        (
            f
            for f in cls.body
            if isinstance(f, ast.FunctionDef) and f.name == "__init__"
        ),
        None,
    )
    forward = next(
        (f for f in cls.body if isinstance(f, ast.FunctionDef) and f.name == "forward"),
        None,
    )
    if forward is None or len(forward.args.args) < 2:
        return None

    scope = _Scope()
    if init is not None:
        scope.config = _signature_defaults(init)

    reader = _Reader(scope, markers, cls.name)
    if init is not None and not reader.read_init(init):
        return None
    if not reader.read_forward(forward):
        return None
    return scope


def _signature_defaults(init: ast.FunctionDef) -> dict[str, int | float | bool | str]:
    """`__init__(self, d_model: int = 512, …)` → the model's config.

    This is where a design's hyperparameters come back from: codegen puts them in the
    signature precisely so the class stays a family of models, and reading them back
    is what makes that survive a trip through source.
    """
    out: dict[str, int | float | bool | str] = {}
    args = init.args.args[1:]
    defaults = init.args.defaults
    offset = len(args) - len(defaults)
    for index, arg in enumerate(args):
        if index < offset:
            continue
        value = _literal(defaults[index - offset])
        if isinstance(value, (int, float, bool, str)):
            out[arg.arg] = value
    return out


def _literal(node: ast.AST) -> object:
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return None


class _Reader:
    """Walks one class body, building nodes as it goes."""

    def __init__(self, scope: _Scope, markers: dict[int, str], class_name: str) -> None:
        self.scope = scope
        self.markers = markers
        self.class_name = class_name
        #: attribute name → node, so `forward` can find what `__init__` built.
        self.by_attr: dict[str, GraphNode] = {}
        #: local value name (`x_3`) → the node that produced it.
        self.values: dict[str, str] = {}
        self.stems: dict[str, int] = {}
        self.seq = 0

    # ── ids and names ────────────────────────────────────────────────────────

    def node_id(self, stmt: ast.AST, hint: str) -> str:
        """The marker's id when the line carries one; a fresh one otherwise.

        Recovering the id is the entire reason the markers exist: it is what lets the
        layout sidecar still describe the graph after a round trip through source.
        """
        marked = self.markers.get(getattr(stmt, "lineno", -1))
        if marked:
            return marked
        self.seq += 1
        return f"{hint}_{self.class_name.lower()}_{self.seq}"

    def derived_name(self, node_type: str, attr: str, group_class: str = "") -> str:
        """Replay codegen's attribute naming; return `` when the source agrees.

        Pinning `name` on every node would round-trip fine and would also freeze
        generated names the user never chose, so that the first time they renamed a
        group every attribute in it kept the old stem.
        """
        if group_class:
            # Codegen names a group instance after the class it runs, pluralised when
            # it is stacked. Not replaying that would record every generated name as
            # an explicit override.
            stem = group_class
        else:
            spec = specs.spec_for(node_type)
            stem = (spec.attr if spec else "") or "mod"
        self.stems[stem] = self.stems.get(stem, 0) + 1
        return "" if attr == f"{stem}_{self.stems[stem]}" else attr

    # ── __init__ ─────────────────────────────────────────────────────────────

    def read_init(self, init: ast.FunctionDef) -> bool:
        for stmt in init.body:
            if isinstance(stmt, ast.Expr) or isinstance(stmt, ast.Pass):
                continue  # `super().__init__()`, docstrings
            if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1:
                return False
            attr = _self_attr(stmt.targets[0])
            if not attr:
                return False
            node = self._node_from_call(stmt, attr)
            if node is None:
                return False
            self.by_attr[attr] = node
            self.scope.nodes.append(node)
        return True

    def _node_from_call(self, stmt: ast.Assign, attr: str) -> GraphNode | None:
        value = stmt.value
        if isinstance(value, ast.Call) and _dotted(value.func) == "nn.ModuleList":
            return self._stack(stmt, attr, value)
        if not isinstance(value, ast.Call):
            return None

        name = _dotted(value.func)
        entry = CONSTRUCTORS.get(name)
        if entry is None:
            # An unknown constructor is user code, not a parse failure. Preserving it
            # is the difference between "we could not read your file" and "we read
            # your file and kept the part we do not understand".
            self.scope.instantiates.add(name)
            node = GraphNode(
                id=self.node_id(stmt, "custom"),
                type="custom.module",
                params={
                    "class_name": name,
                    "code": "",
                    "args": _args_source(value),
                    "out_shape": "",
                },
            )
            node.name = self.derived_name("custom.module", attr)
            return node

        node_type, positional = entry
        params = self._call_params(value, node_type, positional)
        node = GraphNode(
            id=self.node_id(stmt, node_type.split(".")[-1]),
            type=node_type,
            params=params,
        )
        node.name = self.derived_name(node_type, attr)
        return node

    def _stack(self, stmt: ast.Assign, attr: str, call: ast.Call) -> GraphNode | None:
        """`nn.ModuleList([Block(...) for _ in range(N)])` → a repeated group."""
        if not call.args or not isinstance(call.args[0], ast.ListComp):
            return None
        comp = call.args[0]
        element = comp.elt
        if not isinstance(element, ast.Call):
            return None
        cls_name = _dotted(element.func)
        self.scope.instantiates.add(cls_name)

        count: object = 1
        generator = comp.generators[0] if comp.generators else None
        if generator is not None and isinstance(generator.iter, ast.Call):
            iter_args = generator.iter.args
            if iter_args:
                count = self._value(iter_args[0])

        node = GraphNode(
            id=self.node_id(stmt, "group"),
            type="group",
            params={"group": _group_id(cls_name), "count": count},
        )
        stem = re.sub(r"[^0-9a-zA-Z_]", "_", cls_name).lstrip("_").lower() or "block"
        node.name = self.derived_name("group", attr, group_class=f"{stem}s" if count != 1 else stem)
        return node

    def _call_params(
        self, call: ast.Call, node_type: str, positional: tuple[str, ...]
    ) -> dict[str, object]:
        params: dict[str, object] = {}
        for index, arg in enumerate(call.args):
            if index < len(positional):
                params[positional[index]] = self._value(arg)
        for keyword in call.keywords:
            if keyword.arg:
                params[keyword.arg] = self._value(keyword.value)
        spec = specs.spec_for(node_type)
        if spec:
            for declared in spec.params:
                params.setdefault(declared.name, declared.default)
        return params

    def _value(self, node: ast.AST) -> object:
        """A literal, or `"$key"` when the source names a config argument.

        `RMSNorm(d_model)` in a generated class is not the number 512 — it is the
        reference that keeps the class parametric, and reading it back as 512 would
        quietly collapse a family of models into one.
        """
        if isinstance(node, ast.Name):
            return f"${node.id}"
        literal = _literal(node)
        return literal if literal is not None else ast.unparse(node)

    # ── forward ──────────────────────────────────────────────────────────────

    def read_forward(self, forward: ast.FunctionDef) -> bool:
        arg = forward.args.args[1].arg
        # Every scope starts as a root; `parse_module` retypes the terminals of the
        # classes that turn out to be groups, since which one is the root is only
        # knowable once every class has been read.
        spec_type = "io.input"
        entry = GraphNode(
            id=f"{spec_type.split('.')[-1]}_{self.class_name.lower()}",
            type=spec_type,
            params={},
        )
        self.scope.nodes.insert(0, entry)
        self.values[arg] = entry.id

        for stmt in forward.body:
            if isinstance(stmt, ast.Expr):
                continue
            if (
                isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
            ):
                if not self._read_assign(stmt, stmt.targets[0].id):
                    return False
                continue
            if isinstance(stmt, ast.For):
                if not self._read_loop(stmt):
                    return False
                continue
            if isinstance(stmt, ast.Return):
                return self._read_return(stmt)
            return False
        return False

    def _read_assign(self, stmt: ast.Assign, target: str) -> bool:
        produced = self._read_expr(stmt, stmt.value)
        if produced is None:
            return False
        self.values[target] = produced
        return True

    def _read_loop(self, stmt: ast.For) -> bool:
        """The `for _layer in self.blocks_1:` a stacked group emits.

        The seeding line above it (`x_2 = x_1`) is a plain alias, so the *loop* is
        what actually attaches the stack: it rebinds the value that flows onward to
        the group's output. Treating the alias as the connection instead would leave
        the whole block stack unwired — the chain would appear to skip it.
        """
        attr = _self_attr(stmt.iter)
        node = self.by_attr.get(attr)
        if node is None or node.type != "group":
            return False
        target = next(
            (
                inner.targets[0].id
                for inner in stmt.body
                if isinstance(inner, ast.Assign)
                and len(inner.targets) == 1
                and isinstance(inner.targets[0], ast.Name)
            ),
            "",
        )
        if not target:
            return False
        source = self.values.get(target)
        if source and source != node.id:
            self._link(source, node.id)
        self.values[target] = node.id
        return True

    def _read_return(self, stmt: ast.Return) -> bool:
        if stmt.value is None:
            return False
        source = self._resolve(stmt.value)
        if source is None:
            return False
        terminal = GraphNode(
            id=f"output_{self.class_name.lower()}", type="io.output", params={}
        )
        self.scope.nodes.append(terminal)
        self._link(source, terminal.id)
        return True

    def _resolve(self, node: ast.AST, stmt: ast.AST | None = None) -> str | None:
        """The node that produced this expression's value.

        Recursive, because a hand-edited `forward` nests freely — `F.silu(self.up(x))`
        is one statement and two nodes. A resolver that only understood bare names
        would refuse the whole class and send it to the opaque fallback, which is a
        much worse answer than reading it.
        """
        if isinstance(node, ast.Name):
            return self.values.get(node.id)
        if stmt is not None and isinstance(node, (ast.Call, ast.BinOp)):
            return self._read_expr(stmt, node)
        return None

    def _link(self, source: str, target: str, handle: str = "in") -> None:
        self.scope.edges.append(
            GraphEdge(
                id=f"{source}->{target}:{handle}",
                source=source,
                target=target,
                targetHandle=handle,
            )
        )

    def _read_expr(self, stmt: ast.Assign, value: ast.AST) -> str | None:
        """One `forward` statement → the id of the node that produced its value."""
        # `x_2 = self.attn_1(x_1)`
        if isinstance(value, ast.Call):
            attr = _self_attr(value.func)
            if attr:
                node = self.by_attr.get(attr)
                if node is None:
                    return None
                for arg in value.args:
                    source = self._resolve(arg, stmt)
                    if source:
                        self._link(source, node.id)
                self._retype_embedding(node, value)
                return node.id

            name = _dotted(value.func)
            if name in CALL_NODES:
                return self._elementwise(stmt, CALL_NODES[name], value.args)
            if name == "torch.cat":
                return self._concat(stmt, value)
            return None

        # `x_3 = x_0 + x_2` — the residual, and the only reason it is visible at all.
        if isinstance(value, ast.BinOp):
            return self._binop(stmt, value)

        # `x_2 = x_1` — how a stacked group's loop is seeded.
        if isinstance(value, ast.Name):
            return self.values.get(value.id)

        return None

    def _retype_embedding(self, node: GraphNode, call: ast.Call) -> None:
        """Both embeddings construct `nn.Embedding`; only one indexes a range.

        Resolving it here rather than in `__init__` keeps the ambiguity in the one
        place that can actually see the difference.
        """
        if node.type != "embed.token":
            return
        if any(
            isinstance(arg, ast.Call) and _dotted(arg.func) == "torch.arange"
            for arg in call.args
        ):
            node.type = "embed.learned_positional"
            # The table it indexes is sized by sequence length, not vocabulary; the
            # constructor is the same `nn.Embedding` either way, so the first
            # positional argument means a different thing here.
            node.params = {
                "max_seq": node.params.get("vocab_size", 2048),
                "dim": node.params.get("dim"),
            }

    def _elementwise(
        self, stmt: ast.Assign, node_type: str, args: list[ast.expr]
    ) -> str | None:
        node = GraphNode(
            id=self.node_id(stmt, node_type.split(".")[-1]), type=node_type, params={}
        )
        self.scope.nodes.append(node)
        for arg in args:
            source = self._resolve(arg, stmt)
            if source:
                self._link(source, node.id)
        return node.id

    def _concat(self, stmt: ast.Assign, call: ast.Call) -> str | None:
        axis = next((self._value(k.value) for k in call.keywords if k.arg == "dim"), -1)
        node = GraphNode(
            id=self.node_id(stmt, "concat"),
            type="op.concat",
            params={"dim_index": axis},
        )
        self.scope.nodes.append(node)
        if call.args and isinstance(call.args[0], (ast.List, ast.Tuple)):
            for element in call.args[0].elts:
                source = self._resolve(element)
                if source:
                    self._link(source, node.id)
        return node.id


    def _absolute_positions(self, value: ast.BinOp) -> str | None:
        """`x + self.pos_1(torch.arange(...))` is **one** node, not an Add of two.

        The learned positional embedding adds itself; reading the `+` as a separate
        `op.add` would draw a node the user never placed and regenerate a file that
        no longer matches the one they edited.
        """
        if not isinstance(value.op, ast.Add) or not isinstance(value.right, ast.Call):
            return None
        attr = _self_attr(value.right.func)
        node = self.by_attr.get(attr) if attr else None
        if node is None or node.type not in ("embed.token", "embed.learned_positional"):
            return None
        if not any(
            isinstance(arg, ast.Call) and _dotted(arg.func) == "torch.arange"
            for arg in value.right.args
        ):
            return None
        self._retype_embedding(node, value.right)
        source = self._resolve(value.left)
        if source:
            self._link(source, node.id)
        return node.id

    def _binop(self, stmt: ast.Assign, value: ast.BinOp) -> str | None:
        positional = self._absolute_positions(value)
        if positional is not None:
            return positional
        left, right = self._resolve(value.left, stmt), self._resolve(value.right, stmt)
        if isinstance(value.op, ast.Mult) and (left is None) != (right is None):
            # One side is a constant: that is a Scale, not a Multiply with a missing
            # input. Reading it as the latter would draw a broken wire.
            source = left or right
            constant = value.right if left else value.left
            node = GraphNode(
                id=self.node_id(stmt, "scale"),
                type="op.scale",
                params={"factor": self._value(constant)},
            )
            self.scope.nodes.append(node)
            if source:
                self._link(source, node.id)
            return node.id

        if left is None or right is None:
            return None
        node_type = (
            "op.add"
            if isinstance(value.op, ast.Add)
            else "op.mul"
            if isinstance(value.op, ast.Mult)
            else ""
        )
        if not node_type:
            return None
        node = GraphNode(
            id=self.node_id(stmt, node_type.split(".")[-1]), type=node_type, params={}
        )
        self.scope.nodes.append(node)
        self._link(left, node.id)
        self._link(right, node.id)
        return node.id


def _args_source(call: ast.Call) -> str:
    """The constructor arguments of an unknown class, verbatim."""
    parts = [ast.unparse(arg) for arg in call.args]
    parts += [f"{kw.arg}={ast.unparse(kw.value)}" for kw in call.keywords if kw.arg]
    return ", ".join(parts)

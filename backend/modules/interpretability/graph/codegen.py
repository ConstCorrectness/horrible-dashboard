"""The graph, as a PyTorch `nn.Module` subclass.

The output is meant to be read and then owned. It is ordinary, boring PyTorch — no
framework, no registry, no import from this app — because the point of the pane is
that you leave with a file, not with a dependency. The only trace we leave behind is
a `# horrible:node=<id>` comment per emitted line, and even that is only load-bearing
in one direction: it is how re-parsing the file recovers which node a line came from,
so your canvas layout survives a round trip instead of being re-laid-out from scratch
on every save.

Three structures do all the work:

- a **node group emits a class**, which is what makes the Blender metaphor fit a
  neural network at all — a group is a reusable parametrised unit, and so is a Module;
- a group with **repeat > 1 emits a `ModuleList` and a loop**, the ×N the model
  explorer already draws rather than forty identical rectangles;
- **config values stay variables**, so the generated class is a family of models
  (`d_model=2048` today, 4096 tomorrow) instead of one frozen instance.
"""

from __future__ import annotations

import keyword
import re

from backend.modules.interpretability.graph import primitives, spec as specs
from backend.modules.interpretability.graph.models import (
    CodeResult,
    DesignGraph,
    GraphEdge,
    GraphNode,
    SubGraph,
)
from backend.modules.interpretability.graph.spec import Ctx, ShapeError
from backend.modules.interpretability.graph.walk import (
    CycleError,
    inputs_for,
    topo_order,
)

MARKER = "horrible:node"
_MARKER_RE = re.compile(rf"#\s*{MARKER}=([\w:-]+)\s*$")

HEADER = '''"""{title}

Generated from a model graph in the interpretability pane's designer. Every line
carries the node it came from, which is how an edit here finds its way back onto the
canvas; otherwise this is a plain PyTorch module and yours to change.
"""
'''


class CodegenError(ValueError):
    """The graph cannot be turned into code, with a reason worth showing."""


def marker_of(line: str) -> str | None:
    """The node id a generated line belongs to, if it carries one."""
    found = _MARKER_RE.search(line)
    return found.group(1) if found else None


def class_name(raw: str) -> str:
    """A safe PascalCase class name. Falls back rather than emitting broken source."""
    parts = [p for p in re.split(r"[^0-9a-zA-Z]+", raw or "") if p]
    name = "".join(p[:1].upper() + p[1:] for p in parts)
    if not name or name[0].isdigit() or keyword.iskeyword(name):
        name = f"Module{name}"
    return name


#: Past this, a generated line stops being something you can read at a glance. The
#: file is meant to be edited by hand the moment it lands, so it is formatted for
#: that rather than for the generator's convenience.
LINE_LIMIT = 96


def _split_call(text: str) -> list[str]:
    """One call statement as one line, or as an argument per line if it is too long."""
    if len(text) <= LINE_LIMIT:
        return [text]
    indent = text[: len(text) - len(text.lstrip())]
    stripped = text.rstrip()
    # A `def` line is a call with a colon glued on; wrapping it is the same problem.
    suffix = ":" if stripped.endswith("):") else ""
    if suffix:
        stripped = stripped[:-1]
    open_at = stripped.find("(")
    if open_at < 0 or not stripped.endswith(")"):
        return [text]

    body = stripped[open_at + 1 : -1]
    args: list[str] = []
    depth = 0
    current = ""
    for char in body:
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        if char == "," and depth == 0:
            args.append(current.strip())
            current = ""
            continue
        current += char
    if current.strip():
        args.append(current.strip())
    if len(args) < 2:
        return [text]

    inner = indent + "    "
    return [
        stripped[:open_at] + "(",
        *[f"{inner}{arg}," for arg in args],
        indent + ")" + suffix,
    ]


class _Attrs:
    """Which node each generated `self.<attr>` came from, per class.

    Built as the attributes are emitted rather than recovered afterwards, for the
    same reason the line markers are: a second pass that re-derives names has to
    reimplement the naming rules, and the day the two disagree nothing says so —
    a measured parameter count simply lands on the wrong box.
    """

    def __init__(self) -> None:
        #: class → attr → node id
        self.of: dict[str, dict[str, str]] = {}
        #: class → attr → the class that attribute instantiates (groups only)
        self.classes: dict[str, dict[str, str]] = {}

    def record(self, cls: str, attr: str, node_id: str, holds: str = "") -> None:
        self.of.setdefault(cls, {})[attr] = node_id
        if holds:
            self.classes.setdefault(cls, {})[attr] = holds


class _Lines:
    """Accumulated source plus the line→node map, kept in step by construction."""

    def __init__(self) -> None:
        self.rows: list[str] = []
        self.markers: dict[int, str] = {}

    def add(self, text: str = "", node: GraphNode | None = None) -> None:
        self.rows.append(
            f"{text}  # {MARKER}={node.id}" if (node and text.strip()) else text
        )
        if node and text.strip():
            self.markers[len(self.rows)] = node.id

    def wrap(self, text: str, node: GraphNode | None = None) -> None:
        """Add a call, split across lines if it would otherwise be unreadable.

        The marker comment goes on the *first* line because that is the line
        `ast` reports for the whole statement, and the round-trip parser looks it
        up by that number. On any other line it would be invisible to the reader
        that needs it.
        """
        rows = _split_call(text)
        self.add(rows[0], node)
        for row in rows[1:]:
            self.add(row)

    def extend(self, other: "_Lines") -> None:
        offset = len(self.rows)
        self.rows.extend(other.rows)
        for line, nid in other.markers.items():
            self.markers[line + offset] = nid

    def render(self) -> str:
        return "\n".join(self.rows).rstrip() + "\n"


def generate(graph: DesignGraph) -> CodeResult:
    """The whole file: header, imports, primitives, group classes, root class."""
    try:
        return _generate(graph)
    except (CodegenError, CycleError, ShapeError) as exc:
        return CodeResult(source="", error=str(exc))


def _generate(graph: DesignGraph) -> CodeResult:
    ctx = Ctx(config=dict(graph.config))
    used_prims: set[str] = set()
    _check_class_names(graph)
    attrs = _Attrs()

    bodies = _Lines()
    for sub in _group_order(graph):
        bodies.extend(
            _emit_class(
                graph,
                ctx,
                sub.nodes,
                sub.edges,
                class_name(sub.name),
                arg="x",
                used=used_prims,
                attrs=attrs,
            )
        )
        bodies.add()
        bodies.add()

    root = _emit_class(
        graph,
        ctx,
        graph.nodes,
        graph.edges,
        class_name(graph.name),
        arg=_root_arg(graph),
        used=used_prims,
        attrs=attrs,
    )

    out = _Lines()
    for line in (
        HEADER.format(title=f"{class_name(graph.name)} — a PyTorch model.")
        .rstrip("\n")
        .split("\n")
    ):
        out.add(line)
    out.add()
    out.add("import torch")
    out.add("import torch.nn as nn")
    out.add("import torch.nn.functional as F")
    out.add()
    out.add()

    prim_source = primitives.source_for(used_prims)
    if prim_source:
        for line in prim_source.split("\n"):
            out.add(line)
        out.add()
        out.add()

    for block in _custom_classes(graph):
        for line in block.split("\n"):
            out.add(line)
        out.add()
        out.add()

    out.extend(bodies)
    out.extend(root)
    return CodeResult(
        source=out.render(),
        markers=out.markers,
        attrs=attrs.of,
        attrClasses=attrs.classes,
        rootClass=class_name(graph.name),
    )


def _check_class_names(graph: DesignGraph) -> None:
    """Refuse a design where two groups compile to the same class.

    `class_name` strips punctuation, so "Block 2" and "Block-2" are one class, and
    two definitions of it in one file means the second silently wins — every
    instance of the first would then run the second's code, with no error anywhere.
    Naming them is the user's job; noticing the collision is ours.
    """
    seen: dict[str, str] = {}
    for sub in graph.groups:
        emitted = class_name(sub.name)
        if emitted in seen and seen[emitted] != sub.name:
            raise CodegenError(
                f"groups {seen[emitted]!r} and {sub.name!r} both generate a class named "
                f"{emitted!r} — rename one, or the second definition silently replaces the first"
            )
        if emitted in seen:
            raise CodegenError(
                f"two groups are named {sub.name!r} — rename one, or the second "
                "definition silently replaces the first"
            )
        seen[emitted] = sub.name

    root = class_name(graph.name)
    if root in seen:
        raise CodegenError(
            f"the model and the group {seen[root]!r} both generate a class named "
            f"{root!r} — the model's definition would replace the block's"
        )


def _custom_classes(graph: DesignGraph) -> list[str]:
    """Source carried by `custom.module` nodes, deduplicated, in first-seen order.

    Without this the generated file instantiates a class it never defines — a
    `NameError` at import, from a node whose entire purpose is to hold code the
    generator does not understand. It is also what makes the round-trip parser's
    fallback honest: source it cannot map onto a node is preserved *and still runs*,
    rather than being preserved somewhere the file never reaches.

    Deduplicated by class name because two instances of the same custom block are two
    nodes and one class; emitting the definition twice is a redefinition that silently
    wins.
    """
    seen: set[str] = set()
    blocks: list[str] = []
    scopes = [graph.nodes, *(group.nodes for group in graph.groups)]
    for nodes in scopes:
        for node in nodes:
            if node.type != "custom.module" or node.muted:
                continue
            name = str(node.params.get("class_name", "")).strip()
            source = str(node.params.get("code", "")).strip("\n")
            if not source.strip() or name in seen:
                continue
            seen.add(name)
            blocks.append(source)
    return blocks


def _root_arg(graph: DesignGraph) -> str:
    node = next((n for n in graph.nodes if n.type == "io.input"), None)
    return _identifier(node.name) if node and node.name else "ids"


def _identifier(raw: str) -> str:
    cleaned = re.sub(r"[^0-9a-zA-Z_]", "_", raw or "").lstrip("_")
    if not cleaned or cleaned[0].isdigit() or keyword.iskeyword(cleaned):
        cleaned = f"v_{cleaned}"
    return cleaned


def _group_order(graph: DesignGraph) -> list[SubGraph]:
    """Groups in definition-safe order: a group that instantiates another comes after it.

    Same failure mode as the primitives: a class referenced before it is defined is a
    `NameError` at import, which reads as "the generated code is broken" rather than
    "two definitions were emitted in the wrong order".
    """
    by_id = {g.id: g for g in graph.groups}
    ordered: list[SubGraph] = []
    seen: set[str] = set()
    visiting: set[str] = set()

    def visit(gid: str) -> None:
        if gid in seen or gid not in by_id:
            return
        if gid in visiting:
            raise CodegenError(
                f"group {by_id[gid].name!r} contains itself, directly or indirectly"
            )
        visiting.add(gid)
        for node in by_id[gid].nodes:
            if node.type == "group":
                visit(str(node.params.get("group", "")))
        visiting.discard(gid)
        seen.add(gid)
        ordered.append(by_id[gid])

    for group in graph.groups:
        visit(group.id)
    return ordered


def _config_refs(
    graph: DesignGraph, nodes: list[GraphNode], ctx: Ctx, depth: int = 0
) -> list[str]:
    """Config keys a scope reads, including through the groups it instantiates.

    A group's `__init__` accepts exactly these, and no more: passing the whole config
    into every class would make each generated signature a wall of arguments the
    class does not use.
    """
    if depth > 16:
        return []
    found: set[str] = set()
    for node in nodes:
        found |= ctx.refs(node)
        if node.type == "group":
            sub = graph.group(str(node.params.get("group", "")))
            if sub is not None:
                found |= set(_config_refs(graph, sub.nodes, ctx, depth + 1))
    return [key for key in graph.config if key in found]


def _emit_class(
    graph: DesignGraph,
    ctx: Ctx,
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    name: str,
    *,
    arg: str,
    used: set[str],
    attrs: _Attrs,
) -> _Lines:
    lines = _Lines()
    order = topo_order(nodes, edges)

    signature = _config_refs(graph, nodes, ctx)
    args = "".join(
        f", {key}: {_annotation(graph.config[key])} = {graph.config[key]!r}"
        for key in signature
    )
    lines.add(f"class {name}(nn.Module):")
    lines.wrap(f"    def __init__(self{args}):")
    lines.add("        super().__init__()")

    init = _Lines()
    body = _Lines()
    values: dict[str, str] = {}
    owned: dict[str, str] = {}
    stems: dict[str, int] = {}
    value_seq = 0
    returned: str | None = None

    for node in order:
        spec = specs.spec_for(node.type)
        if spec is None:
            raise CodegenError(f"unknown node type {node.type!r}")

        ins: dict[str, str] = {}
        for key, edge in inputs_for(node, edges):
            if edge.source in values:
                ins[key] = values[edge.source]

        if node.type in ("io.input", "io.group_input"):
            values[node.id] = arg
            continue

        if node.type in ("io.output", "io.group_output"):
            if not ins:
                raise CodegenError(f"{spec.label} has nothing connected to it")
            returned = next(iter(ins.values()))
            continue

        if spec.inputs and not ins:
            raise CodegenError(f"{spec.label} has nothing connected to its input")

        # Muted (ablated) nodes and reroutes contribute no statement at all: the
        # value simply keeps flowing. Emitting `x_4 = x_3` for them would be
        # correct and would also bury the real computation in aliases.
        if node.muted or node.type == "struct.reroute":
            values[node.id] = next(iter(ins.values()))
            continue

        # Claimed only once the node is known to survive: a muted SwiGLU that still
        # dragged its class into the file would leave an ablation looking like it
        # had not taken, and the file carrying a definition nothing instantiates.
        used |= set(spec.prims)

        if node.type == "group":
            value_seq += 1
            target = f"x_{value_seq}"
            _emit_group(
                graph, ctx, node, init, body, ins, target, stems, used, attrs, name
            )
            values[node.id] = target
            continue

        attr = ""
        if spec.init_fn is not None:
            stem = spec.attr or "mod"
            stems[stem] = stems.get(stem, 0) + 1
            attr_name = _identifier(node.name) if node.name else f"{stem}_{stems[stem]}"
            owned[node.id] = attr_name
            attrs.record(name, attr_name, node.id)
            attr = f"self.{attr_name}"
            init.wrap(f"        {attr} = {spec.init_fn(node, ctx)}", node)

        if spec.forward_fn is None:
            raise CodegenError(f"{spec.label} cannot be turned into code")

        value_seq += 1
        target = f"x_{value_seq}"
        body.add(f"        {target} = {spec.forward_fn(node, attr, ins, ctx)}", node)
        values[node.id] = target

    if not init.rows:
        init.add("        pass" if not owned else "")
    lines.extend(init)
    lines.add()
    lines.add(f"    def forward(self, {arg}):")
    if body.rows:
        lines.extend(body)
    if returned is None:
        raise CodegenError(
            f"{name} has no Output node, so `forward` would return nothing"
        )
    lines.add(f"        return {returned}")
    return lines


def _emit_group(
    graph: DesignGraph,
    ctx: Ctx,
    node: GraphNode,
    init: _Lines,
    body: _Lines,
    ins: dict[str, str],
    target: str,
    stems: dict[str, int],
    used: set[str],
    attrs: _Attrs,
    owner: str,
) -> None:
    """A group instance — one submodule, or a `ModuleList` and a loop for a stack."""
    sub = graph.group(str(node.params.get("group", "")))
    if sub is None:
        raise CodegenError(
            f"a group instance points at {node.params.get('group')!r}, which does not exist"
        )

    cls = class_name(sub.name)
    passed = ", ".join(f"{key}={key}" for key in _config_refs(graph, sub.nodes, ctx))
    count_raw = node.params.get("count", 1)
    count_code = (
        count_raw[1:]
        if isinstance(count_raw, str) and count_raw.startswith("$")
        else repr(int(count_raw or 1))
    )
    count = int(ctx.value(node, "count") or 1)
    source = next(iter(ins.values()))

    if count == 1:
        stem = _identifier(sub.name).lower() or "block"
        stems[stem] = stems.get(stem, 0) + 1
        attr_name = _identifier(node.name) if node.name else f"{stem}_{stems[stem]}"
        attrs.record(owner, attr_name, node.id, holds=cls)
        attr = f"self.{attr_name}"
        init.wrap(f"        {attr} = {cls}({passed})", node)
        body.add(f"        {target} = {attr}({source})", node)
        return

    stem = f"{_identifier(sub.name).lower() or 'block'}s"
    stems[stem] = stems.get(stem, 0) + 1
    attr_name = _identifier(node.name) if node.name else f"{stem}_{stems[stem]}"
    attrs.record(owner, attr_name, node.id, holds=cls)
    attr = f"self.{attr_name}"
    one_line = f"        {attr} = nn.ModuleList([{cls}({passed}) for _ in range({count_code})])"
    if len(one_line) <= LINE_LIMIT:
        init.add(one_line, node)
    else:
        # The comprehension, spread the way `black` would spread it. A generated
        # file people are expected to edit should not arrive needing a reformat
        # before it can be read.
        init.add(f"        {attr} = nn.ModuleList(", node)
        init.add("            [")
        for row in _split_call(f"                {cls}({passed})"):
            init.add(row)
        init.add(f"                for _ in range({count_code})")
        init.add("            ]")
        init.add("        )")
    # Reassigning through the loop rather than unrolling: forty identical statements
    # would be the code equivalent of drawing forty identical rectangles.
    body.add(f"        {target} = {source}", node)
    body.add(f"        for _layer in {attr}:")
    body.add(f"            {target} = _layer({target})")


def _annotation(value: object) -> str:
    return {int: "int", float: "float", bool: "bool", str: "str"}.get(
        type(value), "int"
    )

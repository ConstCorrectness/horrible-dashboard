"""Tier-1 validation: symbolic shape inference, in pure Python, with no torch.

The backend has no torch and must not grow one — heavy dependencies live in
per-project uv envs. So the shape a wire is labelled with is *our* arithmetic, and
the pane says so. Tier 2 (`probe.py`) instantiates the module in a training
project's venv and asks torch for the truth; when the two disagree, this file is
wrong and the disagreement is surfaced rather than reconciled.

What that buys is immediacy: every edit re-labels every wire in under a millisecond,
so a width mismatch is red while you are still holding the mouse button, instead of
being a `RuntimeError` several minutes into a training run.

Batch and sequence stay **symbolic** (`B`, `T`). A symbolic dimension compared
against a concrete one is an unknown, not a conflict — reporting unknowns as errors
would paint every honest graph red.
"""

from __future__ import annotations

from backend.modules.interpretability.graph import spec as specs
from backend.modules.interpretability.graph.models import (
    DesignGraph,
    GraphEdge,
    GraphNode,
    Shape,
    ShapeIssue,
    ShapeReport,
    SubGraph,
)
from backend.modules.interpretability.graph.spec import Ctx, ShapeError
from backend.modules.interpretability.graph.walk import (
    CycleError,
    inputs_for,
    topo_order,
)

#: A group nested this deep is a bug in the caller, not a model.
MAX_DEPTH = 16


class _Run:
    """One inference pass. Accumulates issues instead of raising on the first one —
    a graph mid-edit usually has several, and fixing them one round-trip at a time
    is a worse experience than seeing them all."""

    def __init__(self, graph: DesignGraph) -> None:
        self.graph = graph
        self.ctx = Ctx(config=dict(graph.config))
        self.issues: list[ShapeIssue] = []
        self.shapes: dict[str, dict[str, Shape]] = {}
        self.params: dict[str, int] = {}

    def fail(self, node: GraphNode, message: str, handle: str = "") -> None:
        self.issues.append(ShapeIssue(nodeId=node.id, handle=handle, message=message))


def infer(graph: DesignGraph) -> ShapeReport:
    """Propagate shapes from every source node, and count parameters on the way."""
    run = _Run(graph)
    try:
        outputs = _scope(run, graph.nodes, graph.edges, bound=None, depth=0)
    except CycleError as exc:
        return ShapeReport(ok=False, issues=[ShapeIssue(message=str(exc))])

    if not any(n.type == "io.input" for n in graph.nodes):
        run.issues.append(
            ShapeIssue(
                message="No Input node — nothing to propagate shapes from.",
                severity="warning",
            )
        )
    if not any(n.type == "io.output" for n in graph.nodes):
        run.issues.append(
            ShapeIssue(
                message="No Output node — the model returns nothing.",
                severity="warning",
            )
        )

    del outputs
    return ShapeReport(
        ok=not any(i.severity == "error" for i in run.issues),
        shapes=run.shapes,
        params=run.params,
        totalParams=sum(run.params.values()),
        issues=run.issues,
    )


def _scope(
    run: _Run,
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    *,
    bound: Shape | None,
    depth: int,
) -> dict[str, Shape]:
    """Infer one graph level. `bound` is the tensor a group was handed, if any."""
    values: dict[str, Shape] = {}

    for node in topo_order(nodes, edges):
        spec = specs.spec_for(node.type)
        if spec is None:
            run.fail(node, f"Unknown node type {node.type!r}.")
            continue

        ins: dict[str, Shape] = {}
        missing = False
        for key, edge in inputs_for(node, edges):
            upstream = values.get(edge.source)
            if upstream is None:
                missing = True
                break
            ins[key] = upstream

        if missing:
            continue

        # Blender's mute: the node stops contributing and its input passes through
        # unchanged. Here that is ablation — and an ablated block must not also
        # report the parameters it is no longer applying.
        if node.muted:
            if ins:
                values[node.id] = list(next(iter(ins.values())))
                run.shapes[node.id] = {"out": values[node.id]}
            continue

        if node.type in ("io.input", "io.group_input"):
            shape = (
                list(bound) if (node.type == "io.group_input" and bound) else ["B", "T"]
            )
            if node.type == "io.group_input" and not bound:
                shape = ["B", "T", run.ctx.config.get("d_model", "d_model")]
            values[node.id] = shape
            run.shapes[node.id] = {"out": shape}
            continue

        if spec.inputs and not ins:
            run.fail(
                node,
                f"{spec.label} has nothing connected to its input.",
                handle=spec.inputs[0].name,
            )
            continue

        if node.type == "group":
            shape = _group(run, node, ins, depth)
            if shape is None:
                continue
            values[node.id] = shape
            run.shapes[node.id] = {"out": shape}
            continue

        try:
            shape = (
                spec.shape_fn(node, ins, run.ctx)
                if spec.shape_fn
                else list(next(iter(ins.values()), []))
            )
        except ShapeError as exc:
            run.fail(node, str(exc), handle=spec.inputs[0].name if spec.inputs else "")
            continue
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            run.fail(node, f"{spec.label}: {exc}")
            continue

        values[node.id] = shape
        if spec.outputs:
            run.shapes[node.id] = {spec.outputs[0].name: shape}

        if spec.params_fn:
            try:
                run.params[node.id] = int(spec.params_fn(node, ins, run.ctx))
            except (ShapeError, KeyError, TypeError, ValueError):
                # A count we cannot compute is left out of the overlay rather than
                # entered as zero — a zero would quietly deflate the model's total.
                pass

    return values


def _group(
    run: _Run, node: GraphNode, ins: dict[str, Shape], depth: int
) -> Shape | None:
    """A group instance: infer its subgraph, once, with the incoming shape bound."""
    if depth >= MAX_DEPTH:
        run.fail(node, "Group nesting is too deep — a group probably contains itself.")
        return None

    gid = str(node.params.get("group", ""))
    sub: SubGraph | None = run.graph.group(gid)
    if sub is None:
        run.fail(node, f"This instance points at group {gid!r}, which does not exist.")
        return None

    incoming = next(iter(ins.values()), None)
    try:
        values = _scope(run, sub.nodes, sub.edges, bound=incoming, depth=depth + 1)
    except CycleError as exc:
        run.fail(node, f"{sub.name}: {exc}")
        return None

    terminal = next((n for n in sub.nodes if n.type == "io.group_output"), None)
    if terminal is None:
        run.fail(node, f"Group {sub.name!r} has no Group output node.")
        return None

    wired = [edge for edge in sub.edges if edge.target == terminal.id]
    if not wired:
        run.fail(node, f"Group {sub.name!r} has nothing connected to its output.")
        return None

    out = next((values[edge.source] for edge in wired if edge.source in values), None)
    if out is None:
        # The output *is* wired; something upstream of it inside the group failed and
        # has already said why. Reporting "nothing connected" here would be a second
        # explanation that is also false, and the reader would chase the wrong one.
        return None

    try:
        count = int(run.ctx.value(node, "count") or 1)
    except (ShapeError, TypeError, ValueError) as exc:
        run.fail(node, f"Repeat count is not a number: {exc}")
        return None
    if count > 1 and incoming is not None and not _compatible(incoming, out):
        # The ×N stack feeds each copy the previous one's output, so a block that
        # changes shape can only ever be applied once. Repeating it anyway would
        # emit a loop that fails on its second iteration.
        run.fail(
            node,
            f"This block changes shape ({incoming} → {out}), so it cannot be stacked {count}×.",
        )
        return None

    # Every copy owns its own weights.
    for nid in [n.id for n in sub.nodes]:
        if nid in run.params:
            run.params[nid] *= count

    return out


def _compatible(a: Shape, b: Shape) -> bool:
    """Shapes that could be the same tensor. Symbols match anything."""
    if len(a) != len(b):
        return False
    return all(
        not (isinstance(x, int) and isinstance(y, int)) or x == y for x, y in zip(a, b)
    )

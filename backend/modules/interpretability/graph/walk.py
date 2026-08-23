"""Ordering and wiring — the part `shapes.py` and `codegen.py` must agree on exactly.

Both modules walk the same graph in the same order and resolve the same inputs for
each node. Doing that twice, in two files, is how the shape a wire is labelled with
drifts from the shape the emitted code actually produces — so it is done once, here.
"""

from __future__ import annotations

from backend.modules.interpretability.graph.models import GraphEdge, GraphNode


class CycleError(ValueError):
    """The graph is not a DAG.

    A model with a cycle is a recurrent model, and expressing one as a loop of wires
    would need a node that says how many times to go round. `group` with a repeat
    count is that node; an implicit cycle is refused instead of guessed at.
    """


def topo_order(nodes: list[GraphNode], edges: list[GraphEdge]) -> list[GraphNode]:
    """Kahn's algorithm, deterministic in declaration order.

    Determinism matters more than it looks: the generated source is compared against
    golden fixtures and re-parsed for round-tripping, so "same graph, same file" has
    to hold across runs and across Python's hash seed.
    """
    by_id = {n.id: n for n in nodes}
    incoming: dict[str, int] = {n.id: 0 for n in nodes}
    outgoing: dict[str, list[str]] = {n.id: [] for n in nodes}
    for edge in edges:
        if edge.source not in by_id or edge.target not in by_id:
            continue
        incoming[edge.target] += 1
        outgoing[edge.source].append(edge.target)

    ready = [n.id for n in nodes if incoming[n.id] == 0]
    order: list[GraphNode] = []
    while ready:
        current = ready.pop(0)
        order.append(by_id[current])
        for nxt in outgoing[current]:
            incoming[nxt] -= 1
            if incoming[nxt] == 0:
                ready.append(nxt)

    if len(order) != len(nodes):
        stuck = sorted(nid for nid, count in incoming.items() if count > 0)
        raise CycleError(f"the graph has a cycle involving {', '.join(stuck)}")
    return order


def inputs_for(node: GraphNode, edges: list[GraphEdge]) -> list[tuple[str, GraphEdge]]:
    """This node's incoming links as `(key, edge)`, in a stable order.

    A socket that accepts several links (`op.add`'s, so a residual can fold three
    ways) gets one key per link — `in#00`, `in#01` — because a dict cannot hold two
    values under one handle and dropping the second silently is how a residual
    connection goes missing without an error.
    """
    landed = [e for e in edges if e.target == node.id]
    counts: dict[str, int] = {}
    out: list[tuple[str, GraphEdge]] = []
    for edge in landed:
        seen = counts.get(edge.targetHandle, 0)
        counts[edge.targetHandle] = seen + 1
        out.append((edge.targetHandle, edge))
    # Only disambiguate handles that genuinely arrived more than once, so the common
    # single-link case keeps the plain handle name its spec declares.
    repeated = {handle for handle, count in counts.items() if count > 1}
    numbered: dict[str, int] = {}
    result: list[tuple[str, GraphEdge]] = []
    for handle, edge in out:
        if handle in repeated:
            index = numbered.get(handle, 0)
            numbered[handle] = index + 1
            result.append((f"{handle}#{index:02d}", edge))
        else:
            result.append((handle, edge))
    return result

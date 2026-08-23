/**
 * Auto-layout for the design canvas.
 *
 * `@dagrejs/dagre` is already a dependency and already does exactly this job one
 * directory over (`training/panels/ModelGraphPane.tsx`), so this reuses it rather
 * than adding a second graph-layout library or hand-rolling a layered pass.
 *
 * `rankdir: 'LR'`, because Blender's manual is explicit that node trees read left to
 * right and every node editor people already know follows it. The `TB` in the
 * training pane is right for *that* view — a traced module tree reads as a stack —
 * and wrong for this one.
 */
import dagre from '@dagrejs/dagre';

import type { GraphEdge, GraphNode, Layout, NodeLayout } from './graph';

/** Matches the node CSS. A layout computed against the wrong box overlaps or gaps. */
export const NODE_W = 190;
export const NODE_H = 62;

export function autoLayout(nodes: GraphNode[], edges: GraphEdge[]): Record<string, NodeLayout> {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: 'LR', nodesep: 28, ranksep: 76, marginx: 24, marginy: 24 });
  g.setDefaultEdgeLabel(() => ({}));

  const ids = new Set(nodes.map((n) => n.id));
  for (const node of nodes) g.setNode(node.id, { width: NODE_W, height: NODE_H });
  for (const edge of edges) {
    if (ids.has(edge.source) && ids.has(edge.target)) g.setEdge(edge.source, edge.target);
  }
  dagre.layout(g);

  const out: Record<string, NodeLayout> = {};
  for (const node of nodes) {
    const placed = g.node(node.id);
    // dagre reports centres; React Flow positions top-left corners.
    out[node.id] = { x: (placed?.x ?? 0) - NODE_W / 2, y: (placed?.y ?? 0) - NODE_H / 2 };
  }
  return out;
}

/**
 * Positions for every node, preferring the ones already saved.
 *
 * A node the sidecar has never seen — just dropped from the palette, or arrived
 * through a code edit — is placed by dagre; every node that *does* have a saved
 * position keeps it. Re-laying out the whole canvas because one node appeared is
 * the single most annoying thing a node editor can do.
 */
export function resolvePositions(
  nodes: GraphNode[],
  edges: GraphEdge[],
  layout: Layout | undefined,
): Record<string, NodeLayout> {
  const saved = layout?.nodes ?? {};
  const missing = nodes.filter((n) => !saved[n.id]);
  if (missing.length === 0) return saved;
  if (missing.length === nodes.length) return autoLayout(nodes, edges);

  const fresh = autoLayout(nodes, edges);
  // Drop the new nodes clear of the existing drawing rather than into the middle
  // of it: dagre laid them out against a graph whose other coordinates it invented.
  const right = Math.max(0, ...Object.values(saved).map((p) => p.x + NODE_W));
  const merged: Record<string, NodeLayout> = { ...saved };
  for (const node of missing) {
    merged[node.id] = { x: right + 60 + (fresh[node.id]?.x ?? 0), y: fresh[node.id]?.y ?? 0 };
  }
  return merged;
}

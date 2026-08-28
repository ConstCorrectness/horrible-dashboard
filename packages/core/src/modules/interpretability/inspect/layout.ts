/**
 * Auto-layout for the Inspect canvas.
 *
 * A separate file from `designer/layout.ts`, and separate on purpose: **this one
 * is `TB`, that one is `LR`.**
 *
 * The designer's file already argues both sides. It picks `LR` because node
 * editors read left to right and every one people already know does. It also names
 * the case for the other direction: the `TB` in `training/panels/ModelGraphPane`
 * is right for *that* view because "a traced module tree reads as a stack". Inspect
 * is that second case by the designer's own criterion, and three more things point
 * the same way:
 *
 * - Every canonical transformer figure is vertical. A reader who has seen one
 *   diagram of a decoder has seen a vertical one.
 * - The residual skip only reads as a *spine* beside the block when the block runs
 *   down the page. Rotated, it reads as an extra parallel track.
 * - The pane's left column is now resizable and starts narrow, so the axis with
 *   room to spare is the vertical one.
 *
 * Importing across into `designer/` would couple two node models that share
 * nothing but dagre.
 */
import dagre from '@dagrejs/dagre';

import type { InspectEdge, InspectNode } from './graph';

/** Matches `inspect.css`. A layout computed against the wrong box overlaps or gaps. */
export const NODE_W = 190;
export const NODE_H = 58;

/**
 * The stack node is taller than the rest: it carries the layer rail. Told to dagre
 * explicitly, because a rank sized for a 58px box leaves the rail overlapping
 * whatever dagre put beneath it.
 */
export const STACK_H = 104;

export interface Placed {
  x: number;
  y: number;
}

export function layoutInspect(nodes: InspectNode[], edges: InspectEdge[]): Record<string, Placed> {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: 'TB', nodesep: 26, ranksep: 42, marginx: 20, marginy: 20 });
  g.setDefaultEdgeLabel(() => ({}));

  const ids = new Set(nodes.map((n) => n.id));
  for (const node of nodes) {
    g.setNode(node.id, { width: NODE_W, height: node.kind === 'stack' ? STACK_H : NODE_H });
  }
  for (const edge of edges) {
    // The residual is a SKIP: including it as a rank constraint would pull its
    // target up beside the block's first sublayer and flatten the chain it skips
    // over — the chain is the thing being drawn.
    if (edge.residual) continue;
    if (ids.has(edge.source) && ids.has(edge.target)) g.setEdge(edge.source, edge.target);
  }
  dagre.layout(g);

  const out: Record<string, Placed> = {};
  for (const node of nodes) {
    const placed = g.node(node.id);
    const height = node.kind === 'stack' ? STACK_H : NODE_H;
    // dagre reports centres; React Flow positions top-left corners.
    out[node.id] = { x: (placed?.x ?? 0) - NODE_W / 2, y: (placed?.y ?? 0) - height / 2 };
  }
  return out;
}

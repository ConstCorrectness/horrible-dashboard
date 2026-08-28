/**
 * The Inspect canvas: the loaded model's architecture, pannable and zoomable.
 *
 * ## This is the third file in the repo to import `@xyflow/react`
 *
 * `flow/canvas/FlowCanvas.tsx` said it was the only one, and that was true and
 * deliberate. `designer/ModelCanvas.tsx` then argued its way in as the second, on
 * the grounds that the two canvases share an engine and nothing else. The same
 * argument holds a third time, and it is worth making rather than assuming:
 *
 * `ModelCanvas` is built around the designer's **editable** `GraphNode` — typed
 * sockets, `socketsCompatible` refusing a link at the wire, `onConnect`, frame
 * membership, and a per-node layout sidecar persisted to disk. Inspect's nodes are
 * read-only, derived from `ModelArchitecture` plus the GGUF inventory, and have no
 * sockets, no persistence and no edits. Reusing `ModelCanvas` would mean each of
 * those invariants growing a "not in inspect mode" branch, producing a component
 * that is neither. What the rule actually protects — that the node *model* is the
 * API and the engine stays swappable — holds here too: nothing outside this file
 * imports the engine.
 *
 * ## What replaced what
 *
 * A vertical stack of `<button>` rows in a CSS grid column capped at 260px. It
 * could not be resized, could not be zoomed, and could not draw the residual
 * connection at all — that was a text row reading "(+) residual" which connected
 * nothing to nothing.
 *
 * ## Zoom, twice
 *
 * The wheel already changes the zoom factor, so a "zoom control" that did the same
 * thing would be redundant. The two that are not:
 *
 * - **Fit** — the whole model in the box. The default, and the way back.
 * - **Focus** — the selected node *and what it connects to*, on double-click. The
 *   useful verb on a graph is "show me this and its neighbours", not "make it 1.4x".
 *
 * Level-of-detail lives in `nodes.tsx`: the facts appear as you zoom in.
 */
import '@xyflow/react/dist/style.css';
import './inspect.css';

import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  type Edge,
  type Node,
  type ReactFlowInstance,
} from '@xyflow/react';
import { useCallback, useEffect, useMemo, useRef } from 'react';

import { layoutInspect, NODE_H, NODE_W, STACK_H } from './layout';
import { NODE_TYPES, type InspectNodeData } from './nodes';
import { nodeIdFor, type InspectGraph, type Selection } from './graph';

export interface InspectCanvasProps {
  graph: InspectGraph;
  selection: Selection;
  onSelect: (selection: Selection) => void;
  onPickLayer: (layer: number) => void;
  /**
   * Bumped whenever the selection arrived from OUTSIDE (the model locus), to bring
   * that node into view. A counter rather than the id, so choosing layer 15 twice
   * re-centres instead of being a no-op.
   */
  focusNonce: number;
}

export function InspectCanvas({
  graph,
  selection,
  onSelect,
  onPickLayer,
  focusNonce,
}: InspectCanvasProps) {
  const instance = useRef<ReactFlowInstance<Node, Edge> | null>(null);
  const selectedId = nodeIdFor(selection);

  const positions = useMemo(
    () => layoutInspect(graph.nodes, graph.edges),
    [graph.nodes, graph.edges],
  );

  const nodes = useMemo<Node[]>(
    () =>
      graph.nodes.map((node) => ({
        id: node.id,
        type: node.kind === 'stack' ? 'stack' : 'stage',
        position: positions[node.id] ?? { x: 0, y: 0 },
        // Both dimensions, and the same ones dagre laid out against. React Flow
        // otherwise waits to MEASURE the node before it can route an edge to it,
        // and until it has, the edges simply do not render — silently, with the
        // nodes looking perfectly fine. Declaring them also guarantees the layout
        // and the drawing agree rather than agreeing by coincidence.
        width: NODE_W,
        height: node.kind === 'stack' ? STACK_H : NODE_H,
        draggable: false,
        connectable: false,
        deletable: false,
        data: {
          node,
          selected: node.id === selectedId,
          onPickLayer,
        } satisfies InspectNodeData as unknown as Record<string, unknown>,
      })),
    [graph.nodes, positions, selectedId, onPickLayer],
  );

  const edges = useMemo<Edge[]>(
    () =>
      graph.edges.map((edge) => ({
        id: edge.id,
        source: edge.source,
        target: edge.target,
        type: edge.residual ? 'smoothstep' : 'default',
        deletable: false,
        selectable: false,
        focusable: false,
        className: edge.residual ? 'ix-edge ix-edge-residual' : 'ix-edge',
        // The skip leaves the block's side rather than its foot, which is what
        // makes it read as bypassing the chain instead of being part of it.
        pathOptions: edge.residual ? { borderRadius: 24, offset: 26 } : undefined,
        label: edge.residual ? '⊕' : undefined,
      })),
    [graph.edges],
  );

  const fit = useCallback((ids?: string[]) => {
    const flow = instance.current;
    if (!flow) return;
    flow.fitView(
      ids
        ? { nodes: ids.map((id) => ({ id })), duration: 180, maxZoom: 1.1, padding: 0.3 }
        : { duration: 180, padding: 0.14 },
    );
  }, []);

  // Bring an externally-driven selection into view. Only on the nonce: refitting
  // whenever `selection` changed would yank the viewport out from under someone
  // who had just panned somewhere and clicked a node.
  useEffect(() => {
    if (focusNonce === 0 || !graph.focusId) return;
    fit([graph.focusId]);
  }, [focusNonce, graph.focusId, fit]);

  /** The node and everything one edge away from it. */
  const focusNeighbours = useCallback(
    (id: string) => {
      const near = new Set<string>([id]);
      for (const edge of graph.edges) {
        if (edge.source === id) near.add(edge.target);
        if (edge.target === id) near.add(edge.source);
      }
      fit([...near]);
    },
    [graph.edges, fit],
  );

  return (
    <div className="ix-canvas">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        onInit={(flow) => {
          instance.current = flow as ReactFlowInstance<Node, Edge>;
          flow.fitView({ padding: 0.14 });
        }}
        onNodeClick={(_, node) => {
          const found = graph.nodes.find((n) => n.id === node.id);
          if (found) onSelect(found.selection);
        }}
        onNodeDoubleClick={(_, node) => focusNeighbours(node.id)}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable
        minZoom={0.25}
        maxZoom={2.5}
        proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Dots} gap={18} size={1} className="ix-bg" />
        <Controls showInteractive={false} />
        <MiniMap pannable zoomable className="ix-minimap" />
      </ReactFlow>

      <div className="ix-tools">
        <button type="button" onClick={() => fit()} title="Fit the whole model">
          Fit
        </button>
        <button
          type="button"
          onClick={() => graph.focusId && focusNeighbours(graph.focusId)}
          disabled={!graph.focusId}
          title="Centre on the selection and what it connects to (or double-click a node)"
        >
          Focus
        </button>
      </div>
    </div>
  );
}

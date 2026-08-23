/**
 * The design canvas: a thin wrapper around `@xyflow/react`.
 *
 * This is the second file in the repo to import the engine — the first,
 * `modules/flow/canvas/FlowCanvas.tsx`, says in its header that it is the only one,
 * and that was true and deliberate. A second importer is also deliberate: the two
 * canvases share an engine and nothing else. A flow node is an executable step with
 * a run status; a model node is an operator with typed sockets, a symbolic shape and
 * a parameter count, and threading both through one component would produce a
 * component that is neither. What the rule is really protecting — that the node
 * *model* stays the API and the engine stays swappable — holds here too: nothing
 * outside this file imports `@xyflow/react`.
 *
 * The rules it enforces, all borrowed from Blender's node editor:
 *
 * - **Sockets are typed and nothing converts implicitly.** A link between
 *   incompatible sockets is refused at the wire. Blender will quietly turn a float
 *   into a colour; a tensor never becomes a differently-shaped tensor here without
 *   an explicit Reshape node.
 * - **An input socket takes one link, an output feeds many** — except the sockets
 *   declared `multi`, which is how a residual `Add` folds two branches.
 * - **Data flows left to right**, which is what the auto-layout enforces.
 */
import '@xyflow/react/dist/style.css';
import './model-graph.css';

import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type FinalConnectionState,
  type Node,
  type NodeChange,
} from '@xyflow/react';
import { useCallback, useEffect, useMemo, useRef } from 'react';

import {
  formatShape,
  socketsCompatible,
  type GraphEdge,
  type GraphNode,
  type Layout,
  type NodeSpec,
  type ShapeReport,
} from './graph';
import { frameMembership, placeFrames } from './FrameNode';
import { resolvePositions } from './layout';
import { NODE_TYPES, type ModelNodeData } from './ModelNode';

type RFNode = Node<ModelNodeData>;

export interface ModelCanvasProps {
  /**
   * One graph *level* — the root, or the inside of a group. The canvas has no idea
   * which: a group is a graph, and giving this component a `DesignGraph` and a path
   * to dig into would put the group-editing rules in the one file that is supposed
   * to know only about wires.
   */
  nodes: GraphNode[];
  edges: GraphEdge[];
  layout: Layout;
  report: ShapeReport | null;
  specs: Map<string, NodeSpec>;
  /** Group id → the class it generates, so an instance can title itself with it. */
  groupNames: Map<string, string>;
  showCost: boolean;
  /** A probe's measured per-node counts, when one has run against this graph. */
  measured?: { params: Record<string, number>; agrees: Record<string, boolean> } | null;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  /** Every selected node, for the operations that act on a set (grouping). */
  onSelectionChange?: (ids: string[]) => void;
  onScopeChange: (nodes: GraphNode[], edges: GraphEdge[]) => void;
  onLayoutChange: (layout: Layout) => void;
  /** Double-click on a group instance: the mouse equivalent of Blender's Tab. */
  onEnter?: (id: string) => void;
  /** Hands out a "fit everything in view" callback once the engine has mounted —
   * how the Home binding reaches a viewport this file otherwise owns privately. */
  onReady?: (fitAll: () => void) => void;
  /** Told about a refused connection so the pane can say why out loud. */
  onRefused?: (reason: string) => void;
  /**
   * A wire dropped in empty space. Blender opens a search of compatible nodes;
   * this hands the caller everything needed to do the same — where it landed, in
   * both screen and graph coordinates, and which socket is dangling.
   */
  onDropInSpace?: (drop: {
    screen: { x: number; y: number };
    position: { x: number; y: number };
    from: { nodeId: string; handle: string; type: string; side: 'input' | 'output' };
  }) => void;
}

function edgeId(edge: GraphEdge): string {
  return (
    edge.id ||
    `${edge.source}:${edge.sourceHandle ?? 'out'}->${edge.target}:${edge.targetHandle ?? 'in'}`
  );
}

export function ModelCanvas({
  nodes: scopeNodes,
  edges: scopeEdges,
  layout,
  report,
  specs,
  groupNames,
  showCost,
  measured,
  selectedId,
  onSelect,
  onSelectionChange,
  onScopeChange,
  onLayoutChange,
  onEnter,
  onReady,
  onRefused,
  onDropInSpace,
}: ModelCanvasProps) {
  const [nodes, setNodes, onNodesChange] = useNodesState<RFNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  /** The last level this canvas emitted, so its own edits don't bounce back in. */
  const emitted = useRef<{ nodes: GraphNode[]; edges: GraphEdge[] } | null>(null);
  /** Narrowed to the one method used, rather than the whole generically-typed
   * instance: this file needs screen → graph coordinates and nothing else. */
  const flow = useRef<{ screenToFlowPosition: (p: { x: number; y: number }) => { x: number; y: number } } | null>(
    null,
  );

  const issues = useMemo(() => {
    const map = new Map<string, ShapeReport['issues'][number]>();
    for (const issue of report?.issues ?? []) {
      if (issue.nodeId && !map.has(issue.nodeId)) map.set(issue.nodeId, issue);
    }
    return map;
  }, [report]);

  // Rebuild from the graph — but only when the change came from outside (a load, a
  // template, an inspector edit). Rebuilding on our own emissions would fight the
  // user's drag.
  useEffect(() => {
    if (emitted.current?.nodes === scopeNodes && emitted.current?.edges === scopeEdges) return;
    const positions = resolvePositions(scopeNodes, scopeEdges, layout);
    // Frames first: React Flow paints in array order, so a frame later in the list
    // would cover the nodes it is supposed to sit behind.
    const framed = placeFrames(
      layout.frames ?? [],
      positions,
      frameMembership(layout),
      scopeNodes.map((n) => n.id),
    ).map((frame) => ({
      id: `frame:${frame.id}`,
      type: 'frame',
      position: { x: frame.x, y: frame.y },
      draggable: false,
      selectable: false,
      style: { width: frame.width, height: frame.height },
      data: { label: frame.label, color: frame.color },
    }));

    setNodes([
      ...(framed as unknown as RFNode[]),
      ...scopeNodes
        .filter((node) => specs.has(node.type))
        .map((node) => ({
          id: node.id,
          type: 'model',
          position: { x: positions[node.id]?.x ?? 0, y: positions[node.id]?.y ?? 0 },
          selected: node.id === selectedId,
          data: {
            spec: specs.get(node.type)!,
            node,
            groupName:
              node.type === 'group' ? groupNames.get(String(node.params.group ?? '')) : undefined,
            collapsed: layout.nodes[node.id]?.collapsed,
            showCost,
          },
        })),
    ]);
    setEdges(
      scopeEdges.map((edge) => ({
        id: edgeId(edge),
        source: edge.source,
        target: edge.target,
        sourceHandle: edge.sourceHandle ?? 'out',
        targetHandle: edge.targetHandle ?? 'in',
      })),
    );
    // `selectedId` and `showCost` are folded in by the effect below; including them
    // here would rebuild every node (and lose in-flight drags) on a mere selection.
  }, [scopeNodes, scopeEdges, layout, specs, groupNames, setNodes, setEdges]);

  // Shapes, costs and issues change far more often than the graph does — every
  // validate round-trip — so they are painted onto the existing nodes rather than
  // rebuilt into new ones.
  //
  // `scopeNodes` is a dependency because stepping into a group rebuilds every node
  // from a report that did not change: without it the wires inside a block you just
  // entered carry no shapes at all, which is exactly the labelling the pane is for.
  useEffect(() => {
    setNodes((current) =>
      current.map((node) => (node.type === 'frame' ? node : {
        ...node,
        data: {
          ...node.data,
          shape: report?.shapes[node.id]?.out,
          params: report?.params[node.id],
          measured: measured?.params[node.id],
          measuredAgrees: measured?.agrees[node.id],
          issue: issues.get(node.id),
          showCost,
        },
      })),
    );
  }, [report, issues, showCost, measured, scopeNodes, setNodes]);

  // Edges carry the shape they transport, and go red when their target could not
  // resolve — the wire is where a mismatch is actually legible.
  const painted = useMemo(
    () =>
      edges.map((edge) => {
        const shape = report?.shapes[edge.source]?.[String(edge.sourceHandle ?? 'out')];
        const broken = issues.has(edge.target);
        return {
          ...edge,
          label: shape ? formatShape(shape) : undefined,
          className: broken ? 'mg-edge mg-edge-broken' : 'mg-edge',
          animated: false,
        };
      }),
    [edges, report, issues],
  );

  const commit = useCallback(
    (nextNodes: RFNode[], nextEdges: Edge[]) => {
      // Frames are decoration living in the layout sidecar. Letting one through
      // here would put an `undefined` where a node belongs and take the whole
      // graph down on the next validate.
      const real = nextNodes.filter((n) => n.type !== 'frame');
      const level = {
        nodes: real.map((n) => n.data.node),
        edges: nextEdges.map((e) => ({
          id: e.id,
          source: e.source,
          sourceHandle: e.sourceHandle ?? 'out',
          target: e.target,
          targetHandle: e.targetHandle ?? 'in',
        })),
      };
      emitted.current = level;
      onScopeChange(level.nodes, level.edges);
    },
    [onScopeChange],
  );

  const onConnect = useCallback(
    (connection: Connection) => {
      const from = nodes.find((n) => n.id === connection.source);
      const to = nodes.find((n) => n.id === connection.target);
      if (!from || !to) return;
      if (from.id === to.id) {
        onRefused?.('A node cannot feed itself — a loop needs a stacked group, not a wire.');
        return;
      }
      const out = from.data.spec.outputs.find((s) => s.name === (connection.sourceHandle ?? 'out'));
      const inn = to.data.spec.inputs.find((s) => s.name === (connection.targetHandle ?? 'in'));
      if (!socketsCompatible(out, inn)) {
        onRefused?.(`A ${out?.type ?? '?'} output cannot drive a ${inn?.type ?? '?'} input.`);
        return;
      }

      const fresh: Edge = {
        id: `${connection.source}:${connection.sourceHandle ?? 'out'}->${connection.target}:${connection.targetHandle ?? 'in'}`,
        source: connection.source,
        target: connection.target,
        sourceHandle: connection.sourceHandle ?? 'out',
        targetHandle: connection.targetHandle ?? 'in',
      };
      // A single-link input replaces what was there; a `multi` one accumulates.
      // Blender's rule, and the reason `op.add` can fold three branches.
      const kept = inn?.multi
        ? edges.filter((e) => e.id !== fresh.id)
        : edges.filter(
            (e) => !(e.target === fresh.target && (e.targetHandle ?? 'in') === fresh.targetHandle),
          );
      const nextEdges = [...kept, fresh];
      setEdges(nextEdges);
      commit(nodes, nextEdges);
    },
    [nodes, edges, setEdges, commit, onRefused],
  );

  /**
   * A drag that ended on the pane rather than on a socket.
   *
   * React Flow reports this for *every* failed connection, including one dropped on
   * a node it could not attach to — so the check is that the target is the pane
   * itself. Opening a "what can go here" menu on top of the node you just missed
   * would be answering a question nobody asked.
   */
  const handleConnectEnd = useCallback(
    (event: MouseEvent | TouchEvent, state: FinalConnectionState) => {
      if (state.isValid || !onDropInSpace) return;
      const target = event.target as HTMLElement | null;
      if (!target?.classList.contains('react-flow__pane')) return;

      const handle = state.fromHandle;
      const from = state.fromNode;
      if (!handle || !from) return;
      const side: 'input' | 'output' = handle.type === 'source' ? 'output' : 'input';
      const spec = (from.data as ModelNodeData).spec;
      const sockets = side === 'output' ? spec.outputs : spec.inputs;
      const socket = sockets.find((s) => s.name === (handle.id ?? sockets[0]?.name));
      if (!socket) return;

      const point =
        'changedTouches' in event
          ? { x: event.changedTouches[0].clientX, y: event.changedTouches[0].clientY }
          : { x: event.clientX, y: event.clientY };
      onDropInSpace({
        screen: point,
        position: flow.current?.screenToFlowPosition(point) ?? { x: 0, y: 0 },
        from: {
          nodeId: from.id,
          handle: socket.name,
          type: socket.type,
          // Dragging *from* an output wants a node with a matching input.
          side: side === 'output' ? 'input' : 'output',
        },
      });
    },
    [onDropInSpace],
  );

  const handleNodesChange = useCallback(
    (changes: NodeChange<RFNode>[]) => {
      onNodesChange(changes);
      if (changes.some((c) => c.type === 'remove')) {
        const removed = new Set(changes.filter((c) => c.type === 'remove').map((c) => c.id));
        const nextNodes = nodes.filter((n) => !removed.has(n.id));
        const nextEdges = edges.filter((e) => !removed.has(e.source) && !removed.has(e.target));
        setEdges(nextEdges);
        commit(nextNodes, nextEdges);
      }
    },
    [onNodesChange, nodes, edges, setEdges, commit],
  );

  const handleEdgesChange = useCallback(
    (changes: Parameters<typeof onEdgesChange>[0]) => {
      onEdgesChange(changes);
      if (changes.some((c) => c.type === 'remove')) {
        const removed = new Set(changes.filter((c) => c.type === 'remove').map((c) => c.id));
        const nextEdges = edges.filter((e) => !removed.has(e.id));
        commit(nodes, nextEdges);
      }
    },
    [onEdgesChange, edges, nodes, commit],
  );

  const persistPositions = useCallback(() => {
    const positions = { ...layout.nodes };
    for (const node of nodes) {
      // A frame's rectangle is derived from its members every render; writing it
      // back would make it a second, competing fact about where the frame is.
      if (node.type === 'frame') continue;
      positions[node.id] = { ...positions[node.id], x: node.position.x, y: node.position.y };
    }
    onLayoutChange({ ...layout, nodes: positions });
  }, [nodes, layout, onLayoutChange]);

  return (
    <div className="mg-canvas">
      <ReactFlow
        nodes={nodes}
        edges={painted}
        nodeTypes={NODE_TYPES}
        onNodesChange={handleNodesChange}
        onEdgesChange={handleEdgesChange}
        onConnect={onConnect}
        onNodeDragStop={persistPositions}
        onNodeClick={(_event, node) => onSelect(node.id)}
        onNodeDoubleClick={(_event, node) => onEnter?.(node.id)}
        onSelectionChange={({ nodes: selected }) => onSelectionChange?.(selected.map((n) => n.id))}
        onPaneClick={() => onSelect(null)}
        onInit={(instance) => {
          flow.current = instance;
          onReady?.(() => instance.fitView({ duration: 180 }));
        }}
        onConnectEnd={handleConnectEnd}
        proOptions={{ hideAttribution: true }}
        fitView
        minZoom={0.1}
        deleteKeyCode={null}
      >
        <Background variant={BackgroundVariant.Dots} gap={18} size={1} />
        <Controls showInteractive={false} />
        <MiniMap pannable zoomable className="mg-minimap" />
      </ReactFlow>
    </div>
  );
}

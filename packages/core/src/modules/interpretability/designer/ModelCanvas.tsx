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
  type Node,
  type NodeChange,
} from '@xyflow/react';
import { useCallback, useEffect, useMemo, useRef } from 'react';

import {
  formatShape,
  socketsCompatible,
  type DesignGraph,
  type GraphEdge,
  type Layout,
  type NodeSpec,
  type ShapeReport,
} from './graph';
import { resolvePositions } from './layout';
import { NODE_TYPES, type ModelNodeData } from './ModelNode';

type RFNode = Node<ModelNodeData>;

export interface ModelCanvasProps {
  graph: DesignGraph;
  layout: Layout;
  report: ShapeReport | null;
  specs: Map<string, NodeSpec>;
  showCost: boolean;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  onGraphChange: (graph: DesignGraph) => void;
  onLayoutChange: (layout: Layout) => void;
  /** Told about a refused connection so the pane can say why out loud. */
  onRefused?: (reason: string) => void;
}

function edgeId(edge: GraphEdge): string {
  return (
    edge.id ||
    `${edge.source}:${edge.sourceHandle ?? 'out'}->${edge.target}:${edge.targetHandle ?? 'in'}`
  );
}

export function ModelCanvas({
  graph,
  layout,
  report,
  specs,
  showCost,
  selectedId,
  onSelect,
  onGraphChange,
  onLayoutChange,
  onRefused,
}: ModelCanvasProps) {
  const [nodes, setNodes, onNodesChange] = useNodesState<RFNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  /** The last graph this canvas emitted, so its own edits don't bounce back in. */
  const emitted = useRef<DesignGraph | null>(null);

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
    if (emitted.current === graph) return;
    const positions = resolvePositions(graph.nodes, graph.edges, layout);
    setNodes(
      graph.nodes
        .filter((node) => specs.has(node.type))
        .map((node) => ({
          id: node.id,
          type: 'model',
          position: { x: positions[node.id]?.x ?? 0, y: positions[node.id]?.y ?? 0 },
          selected: node.id === selectedId,
          data: {
            spec: specs.get(node.type)!,
            node,
            collapsed: layout.nodes[node.id]?.collapsed,
            showCost,
          },
        })),
    );
    setEdges(
      graph.edges.map((edge) => ({
        id: edgeId(edge),
        source: edge.source,
        target: edge.target,
        sourceHandle: edge.sourceHandle ?? 'out',
        targetHandle: edge.targetHandle ?? 'in',
      })),
    );
    // `selectedId` and `showCost` are folded in by the effect below; including them
    // here would rebuild every node (and lose in-flight drags) on a mere selection.
  }, [graph, layout, specs, setNodes, setEdges]);

  // Shapes, costs and issues change far more often than the graph does — every
  // validate round-trip — so they are painted onto the existing nodes rather than
  // rebuilt into new ones.
  useEffect(() => {
    setNodes((current) =>
      current.map((node) => ({
        ...node,
        data: {
          ...node.data,
          shape: report?.shapes[node.id]?.out,
          params: report?.params[node.id],
          issue: issues.get(node.id),
          showCost,
        },
      })),
    );
  }, [report, issues, showCost, setNodes]);

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
      const next: DesignGraph = {
        ...graph,
        nodes: nextNodes.map((n) => n.data.node),
        edges: nextEdges.map((e) => ({
          id: e.id,
          source: e.source,
          sourceHandle: e.sourceHandle ?? 'out',
          target: e.target,
          targetHandle: e.targetHandle ?? 'in',
        })),
      };
      emitted.current = next;
      onGraphChange(next);
    },
    [graph, onGraphChange],
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
        onPaneClick={() => onSelect(null)}
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

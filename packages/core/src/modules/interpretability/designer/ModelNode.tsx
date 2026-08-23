/**
 * One node on the design canvas — rendered from the served spec, not from a
 * hand-written component per type.
 *
 * There are twenty-seven node types and there will be more; a component each would
 * mean the palette and the renderer drift from the generator the moment a parameter
 * is added on the backend. So this is one shell that reads `NodeSpec`: sockets from
 * `inputs`/`outputs`, the summary line from the params the type declares, and the
 * accent from its category.
 *
 * The two things it shows that a hand-drawn diagram cannot: the **shape on the
 * wire** (a socket labelled `[B, T, 2048]` is the difference between believing the
 * graph and knowing it), and the **cost** — this node's share of the parameter count.
 */
import { Handle, Position, type NodeProps } from '@xyflow/react';

import { FrameNode } from './FrameNode';
import {
  formatCount,
  formatShape,
  type GraphNode,
  type NodeSpec,
  type Shape,
  type ShapeIssue,
} from './graph';

export interface ModelNodeData {
  spec: NodeSpec;
  node: GraphNode;
  shape?: Shape;
  params?: number;
  /** Measured, from a probe that actually ran the module. Replaces the estimate on
   * this node rather than sitting beside it — two numbers for one quantity is a
   * question, and the measurement is the answer. */
  measured?: number;
  /** False when the measurement contradicted the estimate here. That is a finding
   * about `shapes.py`, and the node is the only place it can be pointed at. */
  measuredAgrees?: boolean;
  issue?: ShapeIssue;
  /** For a `group` instance: the name of the class it runs, which is what a reader
   * needs. Every instance titled "Group" makes a canvas of blocks unreadable. */
  groupName?: string;
  collapsed?: boolean;
  /** Set while the cost overlay is on, so nodes can show their share. */
  showCost?: boolean;
  [key: string]: unknown;
}

/**
 * Stroke icons per category, inheriting `currentColor` so one glyph is sized and
 * coloured by whatever contains it.
 */
const ICONS: Record<string, React.ReactNode> = {
  io: <path d="M2 8h12M10 4l4 4-4 4" />,
  embedding: <path d="M3 3h4v4H3zM9 9h4v4H9zM7 5h2M5 7v2" />,
  norm: <path d="M2 12c3 0 3-8 6-8s3 8 6 8" />,
  attention: <path d="M8 3v10M4 6v7M12 6v7M2 9h12" />,
  ffn: <path d="M3 4h10M3 8h10M3 12h10M6 4v8M10 4v8" />,
  activation: <path d="M2 12h5c2 0 1-8 3-8s2 4 4 4" />,
  op: <path d="M8 3v10M3 8h10" />,
  structure: <path d="M3 3h10v10H3zM3 6h10M6 6v7" />,
};

function Icon({ category }: { category: string }) {
  return (
    <svg
      className="mg-icon"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {ICONS[category] ?? ICONS.op}
    </svg>
  );
}

/**
 * The one-line summary under the title: the parameters that change what this node
 * *is*, not every knob it has. Attention shows its head counts because the KV ratio
 * is the biggest driver of cost; a norm shows nothing, because its epsilon is noise.
 */
function summarise(spec: NodeSpec, node: GraphNode): string {
  const value = (key: string): string => {
    // Falling back to the spec's default matters more than it looks: a node dropped
    // from the palette or arriving in a template carries no explicit params at all,
    // so reading `node.params` alone renders every summary blank — which is how the
    // token embedding first showed up as " × ".
    const raw = node.params[key] ?? spec.params.find((p) => p.name === key)?.default;
    if (raw === undefined || raw === null || raw === '') return '';
    return typeof raw === 'string' && raw.startsWith('$') ? raw.slice(1) : String(raw);
  };
  switch (spec.type) {
    case 'attn.mha': {
      const heads = value('heads');
      const kv = value('kv_heads');
      const kind = kv && heads ? (kv === heads ? 'MHA' : kv === '1' ? 'MQA' : 'GQA') : '';
      return [
        kind,
        heads && kv ? `${heads}q / ${kv}kv` : '',
        value('rope') === 'true' ? 'rope' : '',
      ]
        .filter(Boolean)
        .join(' · ');
    }
    case 'ffn.moe':
      return `${value('experts')} experts · top-${value('top_k')}`;
    case 'ffn.linear':
      return `${value('dim')} → ${value('out_features')}`;
    case 'ffn.swiglu':
    case 'ffn.geglu':
    case 'ffn.mlp':
      return `hidden ${value('hidden')}`;
    case 'embed.token':
      return `${value('vocab_size')} × ${value('dim')}`;
    case 'group': {
      const count = value('count');
      return count && count !== '1' ? `× ${count}` : '';
    }
    case 'op.scale':
      return `× ${value('factor')}`;
    case 'op.dropout':
      return `p = ${value('p')}`;
    case 'op.reshape':
      return value('shape');
    case 'custom.module':
      return value('class_name');
    default:
      return '';
  }
}

/**
 * A reroute: a bend in a wire, drawn as one.
 *
 * Blender's is a dot, and it has to be — a reroute rendered as a full titled box
 * would be a node that looks like it does something, when codegen walks straight
 * through it and it contributes no code and no parameters at all. The socket
 * positions are the whole component.
 */
function RerouteNode({ d, selected }: { d: ModelNodeData; selected?: boolean }) {
  return (
    <div
      className={`mg-reroute${selected ? ' mg-selected' : ''}${d.issue ? ' mg-invalid' : ''}`}
      title={d.issue?.message || `Reroute${d.shape ? ` — ${formatShape(d.shape)}` : ''}`}
    >
      <Handle
        id="in"
        type="target"
        position={Position.Left}
        className="mg-socket mg-socket-tensor"
      />
      <Handle
        id="out"
        type="source"
        position={Position.Right}
        className="mg-socket mg-socket-tensor"
      />
    </div>
  );
}

export function ModelNode({ data, selected }: NodeProps) {
  const d = data as ModelNodeData;
  const { spec, node } = d;
  if (spec.type === 'struct.reroute') return <RerouteNode d={d} selected={selected} />;
  const summary = summarise(spec, node);
  const shape = formatShape(d.shape);
  const classes = [
    'mg-node',
    `mg-cat-${spec.category}`,
    selected ? 'mg-selected' : '',
    node.muted ? 'mg-muted' : '',
    d.issue ? 'mg-invalid' : '',
    d.collapsed ? 'mg-collapsed' : '',
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <div className={classes} title={d.issue?.message || spec.doc}>
      {spec.inputs.map((socket, index) => (
        <Handle
          key={socket.name}
          id={socket.name}
          type="target"
          position={Position.Left}
          className={`mg-socket mg-socket-${socket.type}${socket.multi ? ' mg-socket-multi' : ''}`}
          style={
            spec.inputs.length > 1
              ? { top: `${((index + 1) * 100) / (spec.inputs.length + 1)}%` }
              : undefined
          }
        />
      ))}

      <div className="mg-node-head">
        <Icon category={spec.category} />
        <span className="mg-node-title">{node.name || d.groupName || spec.label}</span>
        {node.muted && <span className="mg-chip mg-chip-muted">muted</span>}
      </div>

      {!d.collapsed && (summary || shape) && (
        <div className="mg-node-body">
          {summary && <div className="mg-node-summary">{summary}</div>}
          {shape && <div className="mg-node-shape">{shape}</div>}
        </div>
      )}

      {!d.collapsed && d.showCost && (d.measured ?? d.params) ? (
        <div
          className={`mg-node-cost${d.measured !== undefined ? ' mg-node-measured' : ''}${
            d.measuredAgrees === false ? ' mg-node-disagrees' : ''
          }`}
          title={
            d.measured !== undefined
              ? d.measuredAgrees === false
                ? `Measured ${d.measured} parameters, but the estimate said ${d.params}. The estimate is the one that is wrong.`
                : 'Measured by running the module, not estimated.'
              : 'Counted from the graph, not measured.'
          }
        >
          {formatCount(d.measured ?? d.params ?? 0)} params
          {d.measured !== undefined && (
            <span className="mg-chip mg-chip-measured">
              {d.measuredAgrees === false ? 'measured — estimate wrong' : 'measured'}
            </span>
          )}
        </div>
      ) : null}

      {d.issue && !d.collapsed && <div className="mg-node-issue">{d.issue.message}</div>}

      {spec.outputs.map((socket, index) => (
        <Handle
          key={socket.name}
          id={socket.name}
          type="source"
          position={Position.Right}
          className={`mg-socket mg-socket-${socket.type}`}
          style={
            spec.outputs.length > 1
              ? { top: `${((index + 1) * 100) / (spec.outputs.length + 1)}%` }
              : undefined
          }
        />
      ))}
    </div>
  );
}

/** Stable for React Flow's `nodeTypes` — a fresh object every render remounts every node. */
export const NODE_TYPES = { model: ModelNode, frame: FrameNode };

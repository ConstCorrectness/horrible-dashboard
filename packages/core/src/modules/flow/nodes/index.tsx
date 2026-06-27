/**
 * Built-in flow node types (Phase 1): the draggable elements and how they render
 * on the canvas. Each maps to an executor branch in backend/modules/flow/executor.py.
 *
 * Phase 2 will *derive* additional Tool nodes from the agent capability manifest
 * (serializeManifest) so any pane's `agentTools`/commands become draggable — which
 * is why this stays a small built-in set for now.
 */
import { Handle, Position, type NodeProps } from '@xyflow/react';

export type NodeStatus = 'idle' | 'running' | 'ok' | 'error' | 'skipped';

export interface FlowNodeData {
  label: string;
  config: Record<string, unknown>;
  status: NodeStatus;
  /** Live streamed agent tokens for the current run. */
  stream?: string;
  /** Final output text after the node finished. */
  output?: string;
  error?: string;
  [key: string]: unknown;
}

/** Palette entry describing a draggable node kind. */
export interface NodeKind {
  type: string;
  label: string;
  emoji: string;
  defaultConfig: Record<string, unknown>;
  hasInput: boolean;
  hasOutput: boolean;
}

export const NODE_KINDS: NodeKind[] = [
  {
    type: 'trigger.prompt',
    label: 'Prompt',
    emoji: '▶',
    defaultConfig: { prompt: '' },
    hasInput: false,
    hasOutput: true,
  },
  {
    type: 'agent',
    label: 'Agent',
    emoji: '🤖',
    defaultConfig: { model: '', system: '' },
    hasInput: true,
    hasOutput: true,
  },
  {
    type: 'tool',
    label: 'Tool',
    emoji: '🔧',
    defaultConfig: { tool: '', args: {} },
    hasInput: true,
    hasOutput: true,
  },
  {
    type: 'if',
    label: 'If',
    emoji: '🔀',
    // Two output handles ('true'/'false') drawn by IfNode, so hasOutput is false
    // (no single default handle from NodeShell).
    defaultConfig: { op: 'non_empty', value: '' },
    hasInput: true,
    hasOutput: false,
  },
  {
    type: 'output.pane',
    label: 'Output',
    emoji: '📤',
    defaultConfig: {},
    hasInput: true,
    hasOutput: false,
  },
];

export function nodeKind(type: string): NodeKind | undefined {
  return NODE_KINDS.find((k) => k.type === type);
}

function statusClass(status: NodeStatus): string {
  return `flow-node flow-node-${status}`;
}

function NodeShell({
  kind,
  data,
  children,
}: {
  kind: NodeKind;
  data: FlowNodeData;
  children?: React.ReactNode;
}) {
  return (
    <div className={statusClass(data.status)}>
      {kind.hasInput && <Handle type="target" position={Position.Left} />}
      <div className="flow-node-header">
        <span className="flow-node-emoji">{kind.emoji}</span>
        <span className="flow-node-title">{data.label}</span>
        {data.status === 'running' && <span className="flow-node-spinner">●</span>}
      </div>
      {children && <div className="flow-node-body">{children}</div>}
      {data.error && <div className="flow-node-error">{data.error}</div>}
      {kind.hasOutput && <Handle type="source" position={Position.Right} />}
    </div>
  );
}

function TriggerPromptNode({ data }: NodeProps) {
  const d = data as FlowNodeData;
  const prompt = String(d.config.prompt ?? '');
  return (
    <NodeShell kind={nodeKind('trigger.prompt')!} data={d}>
      <div className="flow-node-text">{prompt || <em>no prompt set</em>}</div>
    </NodeShell>
  );
}

function AgentNode({ data }: NodeProps) {
  const d = data as FlowNodeData;
  const model = String(d.config.model ?? '') || 'configured model';
  const live = d.stream ?? d.output;
  return (
    <NodeShell kind={nodeKind('agent')!} data={d}>
      <div className="flow-node-meta">{model}</div>
      {live && <div className="flow-node-text flow-node-stream">{live}</div>}
    </NodeShell>
  );
}

function ToolNode({ data }: NodeProps) {
  const d = data as FlowNodeData;
  const tool = String(d.config.tool ?? '');
  return (
    <NodeShell kind={nodeKind('tool')!} data={d}>
      <div className="flow-node-meta">{tool || <em>pick a tool</em>}</div>
      {d.output && <div className="flow-node-text">{d.output}</div>}
    </NodeShell>
  );
}

function OutputPaneNode({ data }: NodeProps) {
  const d = data as FlowNodeData;
  return (
    <NodeShell kind={nodeKind('output.pane')!} data={d}>
      {d.output ? (
        <div className="flow-node-text">{d.output}</div>
      ) : (
        <div className="flow-node-text">
          <em>result appears here</em>
        </div>
      )}
    </NodeShell>
  );
}

function IfNode({ data }: NodeProps) {
  const d = data as FlowNodeData;
  const op = String(d.config.op ?? 'non_empty');
  const value = String(d.config.value ?? '');
  const cond = op === 'non_empty' ? 'input is non-empty' : `input ${op} "${value}"`;
  return (
    <div className={`flow-node flow-node-if flow-node-${d.status}`}>
      <Handle type="target" position={Position.Left} />
      <div className="flow-node-header">
        <span className="flow-node-emoji">🔀</span>
        <span className="flow-node-title">{d.label}</span>
        {d.status === 'running' && <span className="flow-node-spinner">●</span>}
      </div>
      <div className="flow-node-body">
        <div className="flow-node-meta">if {cond}</div>
      </div>
      <Handle type="source" id="true" position={Position.Right} style={{ top: '40%' }} />
      <span className="flow-if-label flow-if-true">true</span>
      <Handle type="source" id="false" position={Position.Right} style={{ top: '72%' }} />
      <span className="flow-if-label flow-if-false">false</span>
    </div>
  );
}

/** Stable map for React Flow's `nodeTypes` prop (module-constant — never re-create). */
export const NODE_TYPES = {
  'trigger.prompt': TriggerPromptNode,
  agent: AgentNode,
  tool: ToolNode,
  if: IfNode,
  'output.pane': OutputPaneNode,
};

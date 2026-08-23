/**
 * The flow canvas: a thin wrapper around `@xyflow/react` (React Flow). One of only
 * two files that import the node-graph engine — the other is the model designer's
 * `interpretability/designer/ModelCanvas.tsx`, which wraps it the same way for a
 * different node model. The rule the wrapping protects still holds: the node model
 * stays the API and the engine is swappable — mirroring how the frame engine is wrapped in packages/ui's
 * Workspace. It lives in packages/core (not ui) because a core module panel consumes
 * it, and ui already imports core (a ui-hosted wrapper would be a core→ui cycle —
 * the Avatar3D-in-core precedent). See docs/modules/flow-canvas.md.
 */
import '@xyflow/react/dist/style.css';
import './flow.css';

import {
  addEdge,
  Background,
  Controls,
  ReactFlow,
  useEdgesState,
  useNodesState,
  useReactFlow,
  type Connection,
  type Edge,
  type Node,
} from '@xyflow/react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { serializeManifest, type SerializedTool } from '../../agent/manifest';
import { getFlow, saveFlow, type Flow, type FlowEdge, type FlowNode } from '../flows';
import { runFlow, stopFlow, subscribeFlowEvents } from '../flow-channel';
import { nodeKind, NODE_KINDS, NODE_TYPES, type FlowNodeData } from '../nodes';

type RFNode = Node<FlowNodeData>;

/** Param keys declared by a tool's JSON-schema, in order. */
function toolParams(tool?: SerializedTool): { keys: string[]; required: string[] } {
  const schema = tool?.params as
    | { properties?: Record<string, unknown>; required?: string[] }
    | undefined;
  return {
    keys: schema?.properties ? Object.keys(schema.properties) : [],
    required: Array.isArray(schema?.required) ? schema.required : [],
  };
}

/**
 * Which parameter the upstream node's output should fill, chosen when a tool is
 * picked: a param literally named `input`, else the sole required param, else the
 * first param, else none. The user can override it in the inspector.
 */
function defaultInputArg(tool?: SerializedTool): string {
  const { keys, required } = toolParams(tool);
  if (keys.length === 0) return '';
  if (keys.includes('input')) return 'input';
  if (required.length === 1 && keys.includes(required[0])) return required[0];
  return keys[0];
}

let idSeq = 0;
function newNodeId(type: string): string {
  return `${type.split('.')[0]}-${Date.now().toString(36)}-${idSeq++}`;
}

function toRFNode(n: FlowNode): RFNode {
  const kind = nodeKind(n.type);
  return {
    id: n.id,
    type: n.type,
    position: n.position ?? { x: 0, y: 0 },
    data: { label: kind?.label ?? n.type, config: n.config ?? {}, status: 'idle' },
  };
}

function toFlowNode(n: RFNode): FlowNode {
  return { id: n.id, type: n.type ?? '', position: n.position, config: n.data.config };
}

function toFlowEdge(e: Edge): FlowEdge {
  return {
    id: e.id,
    source: e.source,
    target: e.target,
    sourceHandle: e.sourceHandle,
    targetHandle: e.targetHandle,
  };
}

export function FlowCanvas({ flowId }: { flowId: string }) {
  const [nodes, setNodes, onNodesChange] = useNodesState<RFNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);
  const { screenToFlowPosition } = useReactFlow();

  const loadedRef = useRef(false);
  const runIdRef = useRef<string | null>(null);

  // Load the flow graph once.
  useEffect(() => {
    let cancelled = false;
    loadedRef.current = false;
    getFlow(flowId)
      .then((flow: Flow) => {
        if (cancelled) return;
        setNodes(flow.nodes.map(toRFNode));
        setEdges(flow.edges.map((e) => ({ ...e, id: e.id ?? `${e.source}->${e.target}` })));
        // Allow saves only after the initial load has settled.
        setTimeout(() => (loadedRef.current = true), 0);
      })
      .catch(() => setBanner('Could not load this flow.'));
    return () => {
      cancelled = true;
    };
  }, [flowId, setNodes, setEdges]);

  // Debounced persistence of the graph (skips the initial load).
  useEffect(() => {
    if (!loadedRef.current) return;
    const handle = setTimeout(() => {
      void saveFlow(flowId, { nodes: nodes.map(toFlowNode), edges: edges.map(toFlowEdge) });
    }, 600);
    return () => clearTimeout(handle);
  }, [flowId, nodes, edges]);

  // Live execution telemetry → node/edge state.
  useEffect(() => {
    return subscribeFlowEvents((evt) => {
      if (evt.data.runId !== runIdRef.current) return;
      const { event, data } = evt;
      if (event === 'node_started') {
        setNodes((ns) =>
          ns.map((n) =>
            n.id === data.nodeId
              ? {
                  ...n,
                  data: {
                    ...n.data,
                    status: 'running',
                    stream: '',
                    output: undefined,
                    error: undefined,
                  },
                }
              : n,
          ),
        );
      } else if (event === 'node_skipped') {
        setNodes((ns) =>
          ns.map((n) =>
            n.id === data.nodeId ? { ...n, data: { ...n.data, status: 'skipped' } } : n,
          ),
        );
      } else if (event === 'node_token') {
        setNodes((ns) =>
          ns.map((n) =>
            n.id === data.nodeId
              ? { ...n, data: { ...n.data, stream: (n.data.stream ?? '') + (data.delta ?? '') } }
              : n,
          ),
        );
      } else if (event === 'node_finished') {
        setNodes((ns) =>
          ns.map((n) =>
            n.id === data.nodeId
              ? {
                  ...n,
                  data: {
                    ...n.data,
                    status: data.ok ? 'ok' : 'error',
                    output: data.output ?? n.data.stream,
                    error: data.error,
                  },
                }
              : n,
          ),
        );
      } else if (event === 'edge_fired') {
        setEdges((es) => es.map((e) => (e.id === data.edgeId ? { ...e, animated: true } : e)));
      } else if (event === 'run_finished') {
        setRunning(false);
      } else if (event === 'error') {
        setRunning(false);
        setBanner(data.message ?? 'Flow run failed.');
      }
    });
  }, [setNodes, setEdges]);

  const onConnect = useCallback(
    (c: Connection) => setEdges((es) => addEdge({ ...c, id: `${c.source}->${c.target}` }, es)),
    [setEdges],
  );

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      const type = e.dataTransfer.getData('application/flow-node');
      const kind = nodeKind(type);
      if (!kind) return;
      const position = screenToFlowPosition({ x: e.clientX, y: e.clientY });
      const node: RFNode = {
        id: newNodeId(type),
        type,
        position,
        data: { label: kind.label, config: { ...kind.defaultConfig }, status: 'idle' },
      };
      setNodes((ns) => [...ns, node]);
    },
    [screenToFlowPosition, setNodes],
  );

  const run = useCallback(() => {
    setBanner(null);
    // Reset visuals before the run.
    setNodes((ns) =>
      ns.map((n) => ({
        ...n,
        data: { ...n.data, status: 'idle', stream: '', output: undefined, error: undefined },
      })),
    );
    setEdges((es) => es.map((e) => ({ ...e, animated: false })));
    runIdRef.current = runFlow(flowId);
    setRunning(true);
  }, [flowId, setNodes, setEdges]);

  const stop = useCallback(() => {
    if (runIdRef.current) stopFlow(runIdRef.current);
    setRunning(false);
  }, []);

  // The Tool node catalog = every pane's agentTools + agent commands, the exact
  // surface the orchestrator calls (serializeManifest). So "pane functionality" is
  // draggable onto the canvas with no separate registration.
  const toolCatalog = useMemo(() => serializeManifest(), []);

  const updateConfig = useCallback(
    (key: string, value: unknown) => {
      if (!selectedId) return;
      setNodes((ns) =>
        ns.map((n) =>
          n.id === selectedId
            ? { ...n, data: { ...n.data, config: { ...n.data.config, [key]: value } } }
            : n,
        ),
      );
    },
    [selectedId, setNodes],
  );

  const updateArg = useCallback(
    (key: string, value: unknown) => {
      if (!selectedId) return;
      setNodes((ns) =>
        ns.map((n) => {
          if (n.id !== selectedId) return n;
          const args = { ...((n.data.config.args as Record<string, unknown>) ?? {}), [key]: value };
          return { ...n, data: { ...n.data, config: { ...n.data.config, args } } };
        }),
      );
    },
    [selectedId, setNodes],
  );

  // Picking a tool resets its args and sets a smart default for which param the
  // upstream output maps to (so a `files.read` node wires the previous node into
  // `path`, not a stray `input`).
  const selectTool = useCallback(
    (name: string) => {
      if (!selectedId) return;
      const inputArg = defaultInputArg(toolCatalog.find((t) => t.name === name));
      setNodes((ns) =>
        ns.map((n) =>
          n.id === selectedId
            ? {
                ...n,
                data: { ...n.data, config: { ...n.data.config, tool: name, args: {}, inputArg } },
              }
            : n,
        ),
      );
    },
    [selectedId, toolCatalog, setNodes],
  );

  const deleteSelected = useCallback(() => {
    if (!selectedId) return;
    setNodes((ns) => ns.filter((n) => n.id !== selectedId));
    setEdges((es) => es.filter((e) => e.source !== selectedId && e.target !== selectedId));
    setSelectedId(null);
  }, [selectedId, setNodes, setEdges]);

  const selected = nodes.find((n) => n.id === selectedId) ?? null;
  const selectedTool =
    selected?.type === 'tool'
      ? toolCatalog.find((t) => t.name === String(selected.data.config.tool ?? ''))
      : undefined;
  const toolParamKeys = toolParams(selectedTool).keys;
  const selectedInputArg = String(selected?.data.config.inputArg ?? '');

  return (
    <div className="flow-canvas">
      <aside className="flow-palette">
        <div className="flow-palette-title">Nodes</div>
        {NODE_KINDS.map((k) => (
          <div
            key={k.type}
            className="flow-palette-item"
            draggable
            onDragStart={(e) => e.dataTransfer.setData('application/flow-node', k.type)}
          >
            <span className="flow-node-emoji">{k.emoji}</span> {k.label}
          </div>
        ))}
      </aside>

      <div className="flow-graph" onDrop={onDrop} onDragOver={(e) => e.preventDefault()}>
        <div className="flow-toolbar">
          {running ? (
            <button className="flow-btn flow-btn-stop" onClick={stop}>
              ◼ Stop
            </button>
          ) : (
            <button className="flow-btn flow-btn-run" onClick={run}>
              ▶ Run
            </button>
          )}
          {banner && <span className="flow-banner">{banner}</span>}
        </div>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          onNodeClick={(_, n) => setSelectedId(n.id)}
          onPaneClick={() => setSelectedId(null)}
          deleteKeyCode={['Backspace', 'Delete']}
          fitView
          proOptions={{ hideAttribution: true }}
        >
          <Background />
          <Controls />
        </ReactFlow>
      </div>

      {selected && (
        <aside className="flow-inspector">
          <div className="flow-inspector-title">{selected.data.label}</div>
          {selected.type === 'trigger.prompt' && (
            <label className="flow-field">
              <span>Prompt</span>
              <textarea
                value={String(selected.data.config.prompt ?? '')}
                onChange={(e) => updateConfig('prompt', e.target.value)}
                placeholder="What should this flow do?"
              />
            </label>
          )}
          {selected.type === 'agent' && (
            <>
              <label className="flow-field">
                <span>Model (blank = configured)</span>
                <input
                  value={String(selected.data.config.model ?? '')}
                  onChange={(e) => updateConfig('model', e.target.value)}
                  placeholder="e.g. gemma4:12b"
                />
              </label>
              <label className="flow-field">
                <span>System prompt</span>
                <textarea
                  value={String(selected.data.config.system ?? '')}
                  onChange={(e) => updateConfig('system', e.target.value)}
                  placeholder="Role / instructions for this agent…"
                />
              </label>
            </>
          )}
          {selected.type === 'tool' && (
            <>
              <label className="flow-field">
                <span>Tool (from any pane's capabilities)</span>
                <select
                  value={String(selected.data.config.tool ?? '')}
                  onChange={(e) => selectTool(e.target.value)}
                >
                  <option value="">— pick a tool —</option>
                  {toolCatalog.map((t) => (
                    <option key={t.name} value={t.name}>
                      {t.name}
                      {t.sideEffect ? ' ⚠' : ''}
                    </option>
                  ))}
                </select>
              </label>
              {selectedTool?.description && (
                <p className="flow-tool-desc">{selectedTool.description}</p>
              )}
              {toolParamKeys.length > 0 && (
                <label className="flow-field">
                  <span>Upstream output fills</span>
                  <select
                    value={selectedInputArg}
                    onChange={(e) => updateConfig('inputArg', e.target.value)}
                  >
                    <option value="">(don't use upstream)</option>
                    {toolParamKeys.map((k) => (
                      <option key={k} value={k}>
                        {k}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              {toolParamKeys.map((key) => {
                const isInput = key === selectedInputArg;
                return (
                  <label className="flow-field" key={key}>
                    <span>
                      {key}
                      {isInput ? ' ← from previous node' : ''}
                    </span>
                    <input
                      disabled={isInput}
                      value={
                        isInput
                          ? ''
                          : String(
                              ((selected.data.config.args as Record<string, unknown>) ?? {})[key] ??
                                '',
                            )
                      }
                      placeholder={isInput ? 'comes from the upstream node' : ''}
                      onChange={(e) => updateArg(key, e.target.value)}
                    />
                  </label>
                );
              })}
              {toolParamKeys.length === 0 && selectedTool && (
                <p className="flow-tool-desc">This tool takes no parameters.</p>
              )}
            </>
          )}
          {selected.type === 'if' && (
            <>
              <label className="flow-field">
                <span>Condition on the input</span>
                <select
                  value={String(selected.data.config.op ?? 'non_empty')}
                  onChange={(e) => updateConfig('op', e.target.value)}
                >
                  <option value="non_empty">is non-empty</option>
                  <option value="contains">contains…</option>
                  <option value="equals">equals…</option>
                </select>
              </label>
              {selected.data.config.op !== 'non_empty' && (
                <label className="flow-field">
                  <span>Value</span>
                  <input
                    value={String(selected.data.config.value ?? '')}
                    onChange={(e) => updateConfig('value', e.target.value)}
                    placeholder="text to match"
                  />
                </label>
              )}
              <p className="flow-tool-desc">
                True → the <code>true</code> handle; otherwise the <code>false</code> handle. The
                untaken branch is skipped.
              </p>
            </>
          )}
          {selected.type === 'output.pane' && (
            <div className="flow-field">
              <span>Output</span>
              <div className="flow-inspector-output">{selected.data.output ?? '—'}</div>
            </div>
          )}
          <button className="flow-btn flow-btn-stop flow-delete-node" onClick={deleteSelected}>
            Delete node
          </button>
        </aside>
      )}
    </div>
  );
}

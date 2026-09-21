import { useEffect, useMemo, useState } from 'react';
import dagre from '@dagrejs/dagre';

import { onTrainingEvent, watchGraph, type ModelGraph, type ModelGraphNode } from '../client';
import { lastProjectId } from '../last-project';
import { usePaneParams } from '../../../panes';

const dim = { color: 'var(--text-dim)' } as const;

const NODE_W = 168;
const NODE_H = 44;

interface LaidOutNode extends ModelGraphNode {
  x: number;
  y: number;
}

interface Layout {
  nodes: LaidOutNode[];
  edges: { from: LaidOutNode; to: LaidOutNode }[];
  width: number;
  height: number;
}

function layoutGraph(graph: ModelGraph): Layout {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: 'TB', nodesep: 24, ranksep: 36 });
  g.setDefaultEdgeLabel(() => ({}));
  for (const node of graph.nodes) {
    g.setNode(node.id, { width: NODE_W, height: NODE_H });
  }
  for (const edge of graph.edges) {
    if (graph.nodes.some((n) => n.id === edge.from) && graph.nodes.some((n) => n.id === edge.to)) {
      g.setEdge(edge.from, edge.to);
    }
  }
  dagre.layout(g);
  const byId = new Map<string, LaidOutNode>();
  for (const node of graph.nodes) {
    const pos = g.node(node.id);
    byId.set(node.id, { ...node, x: pos?.x ?? 0, y: pos?.y ?? 0 });
  }
  const meta = g.graph();
  return {
    nodes: [...byId.values()],
    edges: graph.edges
      .filter((e) => byId.has(e.from) && byId.has(e.to))
      .map((e) => ({ from: byId.get(e.from)!, to: byId.get(e.to)! })),
    width: meta.width ?? 600,
    height: meta.height ?? 400,
  };
}

/** grad-norm → color: cold blue (0) → hot red (high), log-scaled. */
function heat(gnorm: number, max: number): string {
  if (max <= 0 || gnorm <= 0) return 'var(--border)';
  const t = Math.min(1, Math.log1p(gnorm) / Math.log1p(max));
  const r = Math.round(83 + t * (229 - 83));
  const g = Math.round(155 - t * (155 - 83));
  const b = Math.round(245 - t * (245 - 75));
  return `rgb(${r},${g},${b})`;
}

const fmtParams = (n: number): string =>
  n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : String(n);

/**
 * Live PyTorch architecture view: the layer graph `horrible_train` publishes when
 * training begins (torch.fx topology, or the module tree when the model isn't
 * traceable), color-mapping per-layer gradient norms when the callback was asked
 * for `weights=True`.
 *
 * **Scoped to one project.** It used to take any `model_graph` off the channel,
 * which is only correct while exactly one project is open; two notebooks side by
 * side overwrote each other's graphs, and a strip riding on notebook A would
 * quietly start showing B. The project comes from the host pane's params — a
 * region strip inherits them — falling back to the remembered project when this
 * is opened as a standalone widget with no document beside it.
 */
export function ModelGraphPane() {
  const params = usePaneParams();
  const [projectId] = useState(() => String(params.projectId ?? lastProjectId() ?? ''));
  const [graph, setGraph] = useState<ModelGraph | null>(null);
  const [stats, setStats] = useState<Record<string, { w_norm: number; g_norm: number }>>({});
  const [selected, setSelected] = useState<ModelGraphNode | null>(null);

  useEffect(() => {
    // A project we cannot name is one we cannot filter for, so it listens to
    // nothing rather than to everything — the old behaviour dressed up.
    if (!projectId) return;
    const mine = (d: { projectId?: string }): boolean => d.projectId === projectId;
    const unsubs = [
      onTrainingEvent('model_graph', (d) => {
        if (!mine(d)) return;
        setGraph(d.graph);
        setStats({});
        setSelected(null);
      }),
      onTrainingEvent('model_stats', (d) => {
        if (mine(d)) setStats(d.stats);
      }),
    ];
    // Replay whatever was published before this strip was opened. `watch()` fires
    // once, at the top of the run.
    watchGraph(projectId);
    return () => unsubs.forEach((u) => u());
  }, [projectId]);

  const layout = useMemo(() => (graph ? layoutGraph(graph) : null), [graph]);
  const maxGrad = useMemo(() => Math.max(0, ...Object.values(stats).map((s) => s.g_norm)), [stats]);

  if (!layout) {
    // Not "call watch(model)" any more. A generated recipe installs `ht.callback()`,
    // which publishes the graph itself the moment training begins — so the honest
    // reading of an empty pane is "nothing has trained here yet", and naming a call
    // the notebook does not contain sent people looking for a bug that wasn't one.
    return (
      <div style={{ padding: '1rem', fontSize: '0.8rem', lineHeight: 1.5, ...dim }}>
        {projectId ? (
          <>
            No architecture yet for <code>{projectId}</code> — it appears on its own when{' '}
            <code>trainer.train()</code> starts. For a model you built by hand, publish it with{' '}
            <code>horrible_train.watch(model)</code> (optionally <code>example=x</code> for shapes,{' '}
            <code>weights=True</code> for live gradient norms).
          </>
        ) : (
          <>No project — open this beside a training notebook, or open one from Explorer.</>
        )}
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', height: '100%' }}>
      <div style={{ flex: 1, overflow: 'auto' }}>
        <svg
          width={layout.width + 32}
          height={layout.height + 32}
          viewBox={`-16 -16 ${layout.width + 32} ${layout.height + 32}`}
        >
          <defs>
            <marker
              id="tg-arrow"
              viewBox="0 0 8 8"
              refX="7"
              refY="4"
              markerWidth="6"
              markerHeight="6"
              orient="auto-start-reverse"
            >
              <path d="M0,0 L8,4 L0,8 z" fill="var(--text-dim, #768390)" />
            </marker>
          </defs>
          {layout.edges.map((e, i) => (
            <line
              key={i}
              x1={e.from.x}
              y1={e.from.y + NODE_H / 2}
              x2={e.to.x}
              y2={e.to.y - NODE_H / 2}
              stroke="var(--text-dim, #768390)"
              strokeWidth={1}
              markerEnd="url(#tg-arrow)"
            />
          ))}
          {layout.nodes.map((n) => {
            const s = stats[n.id];
            return (
              <g
                key={n.id}
                transform={`translate(${n.x - NODE_W / 2}, ${n.y - NODE_H / 2})`}
                style={{ cursor: 'pointer' }}
                onClick={() => setSelected(n)}
              >
                <rect
                  width={NODE_W}
                  height={NODE_H}
                  rx={6}
                  fill="var(--bg-raised, #22272e)"
                  stroke={s ? heat(s.g_norm, maxGrad) : 'var(--border)'}
                  strokeWidth={s ? 2.5 : 1}
                />
                <text
                  x={8}
                  y={18}
                  fontSize={11}
                  fill="var(--text, #adbac7)"
                  fontFamily="var(--font-mono, monospace)"
                >
                  {n.op.length > 22 ? `${n.op.slice(0, 21)}…` : n.op}
                </text>
                <text x={8} y={34} fontSize={9.5} fill="var(--text-dim, #768390)">
                  {(n.name.length > 26 ? `…${n.name.slice(-25)}` : n.name) +
                    (n.params ? ` · ${fmtParams(n.params)}` : '')}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
      {selected && (
        <div
          style={{
            width: 200,
            borderLeft: '1px solid var(--border)',
            padding: '0.5rem',
            fontSize: '0.72rem',
          }}
        >
          <div style={{ fontWeight: 600 }}>{selected.op}</div>
          <div style={dim}>{selected.name}</div>
          <div>params: {selected.params.toLocaleString()}</div>
          {selected.shape && <div>out: [{selected.shape.join(', ')}]</div>}
          {stats[selected.id] && (
            <>
              <div>‖w‖: {stats[selected.id].w_norm.toFixed(4)}</div>
              <div>‖∇‖: {stats[selected.id].g_norm.toFixed(6)}</div>
            </>
          )}
          <div style={{ marginTop: '0.5rem', ...dim }}>
            {graph?.kind === 'fx' ? 'traced with torch.fx' : 'module tree (untraceable model)'}
          </div>
        </div>
      )}
    </div>
  );
}

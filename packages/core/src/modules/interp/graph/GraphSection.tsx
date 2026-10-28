/**
 * Graph: an attribution graph — how a prediction was computed, as a network.
 *
 * Token positions run left to right, layers bottom to top: the prompt's
 * embeddings at the bottom, transcoder features between, the output logits on
 * top. Edges are signed (accent = excitatory, danger = inhibitory) and as strong
 * as their opacity. A graph routinely carries tens of thousands of edges, so it
 * is drawn on a canvas and pruned client-side: features by cumulative influence,
 * edges by their share of each target's strongest input.
 *
 * Click a node for its strongest inputs and outputs and its Neuronpedia label;
 * Ctrl-click pins it; Shift-click gathers nodes into a supernode. Pins and
 * supernodes are written into the graph's own `qParams`, so they persist and
 * travel with the file.
 */
import { useEffect, useMemo, useRef, useState, type CSSProperties, type MouseEvent } from 'react';

import { askInterp } from '../ask';
import { A } from '../attention/styles';
import { NeuronpediaCard } from '../library/NeuronpediaCard';
import { S } from '../styles';
import { SuiteFrame } from '../SuiteFrame';
import {
  describeNode,
  kindOf,
  layout,
  prune,
  topEdges,
  type GraphNode,
  type Placed,
} from './model';
import {
  groupAs,
  importFrom,
  labelOf,
  openGraph,
  refreshGraphs,
  select,
  setPruning,
  toggleGrouping,
  togglePin,
  ungroup,
  useGraph,
} from './state';

export function GraphBody() {
  return (
    <SuiteFrame title="Graph">
      <GraphSection />
    </SuiteFrame>
  );
}

export function GraphSection() {
  const st = useGraph();
  useEffect(() => {
    void refreshGraphs();
  }, []);
  return (
    <div style={A.root}>
      <ImportBar />
      {st.error && <div style={A.conflict}>{st.error}</div>}
      {st.graph ? (
        <>
          <Controls />
          <div style={G.layout}>
            <Canvas />
            <Details />
          </div>
          <Supernodes />
        </>
      ) : (
        <p style={A.emptyText}>
          {st.loading
            ? 'Loading the graph…'
            : 'No graph yet. Paste a Neuronpedia graph link (neuronpedia.org/<model>/graph?slug=…) or open a circuit-tracer JSON file.'}
        </p>
      )}
    </div>
  );
}

// ── import and pick ──────────────────────────────────────────────────────────

function ImportBar() {
  const st = useGraph();
  const [ref, setRef] = useState('');
  const fileRef = useRef<HTMLInputElement>(null);
  return (
    <section style={{ ...A.ribbonWrap, borderTopColor: 'var(--accent)', paddingTop: 10 }}>
      <div style={G.row}>
        <label style={G.field}>
          <span style={S.heading}>Graph</span>
          <select
            value={st.id ?? ''}
            onChange={(e) => void openGraph(e.target.value)}
            aria-label="Graph to show"
            disabled={!st.graphs.length}
          >
            {st.graphs.length === 0 && <option value="">none yet</option>}
            {st.graphs.map((g) => (
              <option key={g.id} value={g.id}>
                {g.choice} · {g.facts.nodes ?? '?'} nodes
              </option>
            ))}
          </select>
        </label>
        <input
          type="text"
          value={ref}
          onChange={(e) => setRef(e.target.value)}
          placeholder="neuronpedia.org/<model>/graph?slug=…  or  <model>/<slug>"
          aria-label="Neuronpedia graph link to import"
          style={{ flex: 1, minWidth: 220 }}
        />
        <button
          type="button"
          disabled={!ref.trim() || st.loading}
          title="Fetch the graph file from Neuronpedia (public data) and keep it in the Library"
          onClick={() => void importFrom({ ref }).then((err) => !err && setRef(''))}
        >
          {st.loading ? 'Importing…' : 'Import'}
        </button>
        <button type="button" onClick={() => fileRef.current?.click()}>
          Open file
        </button>
        <input
          ref={fileRef}
          type="file"
          accept=".json,application/json"
          style={{ display: 'none' }}
          aria-label="Open a circuit-tracer graph file"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file)
              void file
                .text()
                .then((text) =>
                  importFrom({ json_text: text, name: file.name.replace(/\.json$/, '') }),
                );
            e.target.value = '';
          }}
        />
      </div>
    </section>
  );
}

function Controls() {
  const st = useGraph();
  const g = st.graph!;
  const pruned = useMemo(
    () => prune(g, st.nodeThreshold, st.edgeFraction, new Set(st.pinned), st.pinnedOnly),
    [g, st.nodeThreshold, st.edgeFraction, st.pinned, st.pinnedOnly],
  );
  const [label, setLabel] = useState('');
  return (
    <div style={G.controls}>
      <div style={G.prompt}>
        <span style={S.heading}>Prompt</span>
        <span style={G.promptText}>
          {(g.metadata.prompt ?? g.metadata.prompt_tokens.join('')).replace(/^<bos>/, '')}
        </span>
        <span style={S.mono}>{g.metadata.scan}</span>
      </div>
      <label style={G.field}>
        <span style={S.heading}>Nodes ≤ {st.nodeThreshold.toFixed(2)} influence</span>
        <input
          type="range"
          min={0.1}
          max={1}
          step={0.05}
          value={st.nodeThreshold}
          disabled={st.pinnedOnly}
          onChange={(e) => setPruning({ nodeThreshold: Number(e.target.value) })}
          aria-label="Node pruning threshold (cumulative influence)"
        />
      </label>
      <label style={G.field}>
        <span style={S.heading}>Edges ≥ {Math.round(st.edgeFraction * 100)}% of strongest</span>
        <input
          type="range"
          min={0}
          max={0.6}
          step={0.02}
          value={st.edgeFraction}
          onChange={(e) => setPruning({ edgeFraction: Number(e.target.value) })}
          aria-label="Edge pruning: share of each target's strongest input"
        />
      </label>
      <label style={G.check}>
        <input
          type="checkbox"
          checked={st.pinnedOnly}
          disabled={!st.pinned.length}
          onChange={(e) => setPruning({ pinnedOnly: e.target.checked })}
        />
        <span>Pinned only ({st.pinned.length})</span>
      </label>
      <span style={S.mono}>
        {pruned.nodes.length}/{g.nodes.length} nodes · {pruned.links.length.toLocaleString()}/
        {g.links.length.toLocaleString()} edges
      </span>
      {st.grouping.length > 0 && (
        <div style={G.row}>
          <input
            type="text"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder={`label ${st.grouping.length} nodes`}
            aria-label="Supernode label"
            style={{ width: 180 }}
          />
          <button
            type="button"
            disabled={st.grouping.length < 2 || !label.trim()}
            onClick={() => {
              groupAs(label);
              setLabel('');
            }}
          >
            Group
          </button>
        </div>
      )}
    </div>
  );
}

// ── the canvas ───────────────────────────────────────────────────────────────

/** Theme tokens resolved for the canvas, which cannot read `var(--…)`. */
function tokens(el: HTMLElement) {
  const css = getComputedStyle(el);
  const get = (name: string) => css.getPropertyValue(name).trim();
  return {
    accent: get('--accent'),
    danger: get('--danger'),
    success: get('--success'),
    text: get('--text-primary'),
    dim: get('--text-dim'),
    border: get('--border'),
    bg: get('--bg-primary'),
  };
}

const HEIGHT = 520;

function Canvas() {
  const st = useGraph();
  const g = st.graph!;
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(900);
  const [hover, setHover] = useState<Placed | null>(null);

  const pinned = useMemo(() => new Set(st.pinned), [st.pinned]);
  const pruned = useMemo(
    () => prune(g, st.nodeThreshold, st.edgeFraction, pinned, st.pinnedOnly),
    [g, st.nodeThreshold, st.edgeFraction, pinned, st.pinnedOnly],
  );
  const { placed } = useMemo(
    () => layout(pruned.nodes, g.metadata.prompt_tokens.length, width, HEIGHT),
    [pruned.nodes, g.metadata.prompt_tokens.length, width],
  );
  const at = useMemo(() => new Map(placed.map((p) => [p.node.node_id, p])), [placed]);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(Math.max(480, el.clientWidth)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Drawn synchronously on every change: a hidden pane never fires rAF.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = width * dpr;
    canvas.height = HEIGHT * dpr;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const c = tokens(canvas);
    ctx.clearRect(0, 0, width, HEIGHT);

    // Token labels along the bottom.
    ctx.font = '10px monospace';
    ctx.fillStyle = c.dim;
    ctx.textAlign = 'center';
    const colW = (width - 56) / Math.max(1, g.metadata.prompt_tokens.length);
    g.metadata.prompt_tokens.forEach((t, i) => {
      ctx.fillText(t.replace(/▁/g, ' ').slice(0, 10), 28 + colW * (i + 0.5), HEIGHT - 6);
    });

    const focus = st.selected ?? hover?.node.node_id ?? null;
    const maxW = Math.max(1e-9, ...pruned.links.map((l) => Math.abs(l.weight)));
    for (const l of pruned.links) {
      const a = at.get(l.source);
      const b = at.get(l.target);
      if (!a || !b) continue;
      const touches = focus !== null && (l.source === focus || l.target === focus);
      const strength = Math.abs(l.weight) / maxW;
      ctx.globalAlpha = focus ? (touches ? 0.9 : 0.04) : 0.08 + 0.6 * strength;
      ctx.strokeStyle = l.weight >= 0 ? c.accent : c.danger;
      ctx.lineWidth = touches ? 1.6 : 0.6 + 1.4 * strength;
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;

    // Supernodes: a dashed frame around their members.
    ctx.setLineDash([4, 3]);
    for (const group of st.supernodes) {
      const pts = group
        .slice(1)
        .map((id) => at.get(id))
        .filter((p): p is Placed => !!p);
      if (!pts.length) continue;
      const xs = pts.map((p) => p.x);
      const ys = pts.map((p) => p.y);
      ctx.strokeStyle = c.text;
      ctx.lineWidth = 1;
      ctx.strokeRect(
        Math.min(...xs) - 8,
        Math.min(...ys) - 8,
        Math.max(...xs) - Math.min(...xs) + 16,
        Math.max(...ys) - Math.min(...ys) + 16,
      );
      ctx.fillStyle = c.text;
      ctx.textAlign = 'left';
      ctx.fillText(group[0], Math.min(...xs) - 8, Math.min(...ys) - 12);
    }
    ctx.setLineDash([]);

    for (const p of placed) {
      const kind = kindOf(p.node);
      const r = kind === 'logit' ? 5 : kind === 'embedding' ? 4 : 3.5;
      ctx.fillStyle =
        kind === 'logit'
          ? c.success
          : kind === 'embedding'
            ? c.text
            : kind === 'error'
              ? c.dim
              : c.accent;
      ctx.beginPath();
      if (kind === 'error') ctx.rect(p.x - r, p.y - r, r * 2, r * 2);
      else ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
      ctx.fill();
      const id = p.node.node_id;
      if (pinned.has(id) || st.grouping.includes(id) || id === st.selected) {
        ctx.strokeStyle =
          id === st.selected ? c.text : st.grouping.includes(id) ? c.danger : c.text;
        ctx.lineWidth = id === st.selected ? 2 : 1.2;
        ctx.beginPath();
        ctx.arc(p.x, p.y, r + 3, 0, Math.PI * 2);
        ctx.stroke();
      }
    }
  }, [placed, pruned.links, at, width, g, st.selected, st.supernodes, st.grouping, pinned, hover]);

  const nearest = (e: MouseEvent<HTMLCanvasElement>): Placed | null => {
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    let best: Placed | null = null;
    let bestD = 81;
    for (const p of placed) {
      const d = (p.x - x) ** 2 + (p.y - y) ** 2;
      if (d < bestD) {
        bestD = d;
        best = p;
      }
    }
    return best;
  };

  return (
    <div ref={wrapRef} style={G.canvasWrap}>
      <canvas
        ref={canvasRef}
        style={{ width, height: HEIGHT, display: 'block', cursor: hover ? 'pointer' : 'default' }}
        aria-label="Attribution graph: embeddings at the bottom, logits at the top"
        onMouseMove={(e) => setHover(nearest(e))}
        onMouseLeave={() => setHover(null)}
        onClick={(e) => {
          const hit = nearest(e);
          if (!hit) return select(null);
          if (e.ctrlKey || e.metaKey) togglePin(hit.node.node_id);
          else if (e.shiftKey) toggleGrouping(hit.node.node_id);
          else select(hit.node.node_id);
        }}
      />
      {hover && (
        <div
          style={{
            ...G.tip,
            left: Math.min(hover.x + 12, width - 260),
            top: Math.max(4, hover.y - 46),
          }}
        >
          <div style={G.tipTitle}>{describeNode(hover.node, g.metadata.prompt_tokens)}</div>
          <div style={S.mono}>
            {labelOf(hover.node, st.cards) ?? 'click for its label'}
            {hover.node.activation != null ? ` · act ${hover.node.activation.toFixed(2)}` : ''}
          </div>
        </div>
      )}
      <div style={G.legend}>
        <span style={S.mono}>
          ● feature ● embedding ■ error ● logit · Ctrl-click pin · Shift-click group
        </span>
      </div>
    </div>
  );
}

// ── the selected node ────────────────────────────────────────────────────────

function Details() {
  const st = useGraph();
  const g = st.graph!;
  const node = st.selected ? g.nodes.find((n) => n.node_id === st.selected) : null;
  if (!node) {
    return (
      <aside style={A.card}>
        <div style={S.heading}>Node</div>
        <p style={A.emptyText}>Click a node for its strongest inputs and outputs.</p>
      </aside>
    );
  }
  const byId = new Map(g.nodes.map((n) => [n.node_id, n]));
  const edgeRow = (other: GraphNode | undefined, weight: number, key: string) =>
    other ? (
      <li key={key}>
        <button
          type="button"
          className="interp-col"
          style={G.edge}
          onClick={() => select(other.node_id)}
        >
          <span style={{ ...S.mono, color: weight >= 0 ? 'var(--success)' : 'var(--danger)' }}>
            {weight >= 0 ? '+' : ''}
            {weight.toFixed(3)}
          </span>
          <span style={G.edgeName}>
            {labelOf(other, st.cards) ?? describeNode(other, g.metadata.prompt_tokens)}
          </span>
        </button>
      </li>
    ) : null;
  const card = st.cards[node.node_id];
  return (
    <aside
      style={{ ...A.card, display: 'grid', gap: 8, alignContent: 'start' }}
      aria-label="Selected node"
    >
      <div style={A.cardHead}>
        <span style={A.cardLayer}>{describeNode(node, g.metadata.prompt_tokens)}</span>
      </div>
      <div style={S.mono}>
        {node.feature_type}
        {node.activation != null ? ` · activation ${node.activation.toFixed(2)}` : ''}
        {node.influence != null ? ` · influence ${node.influence.toFixed(3)}` : ''}
        {node.token_prob ? ` · p ${node.token_prob.toFixed(3)}` : ''}
      </div>
      {labelOf(node, st.cards) && <div style={G.label}>“{labelOf(node, st.cards)}”</div>}
      <div style={A.actions}>
        <button type="button" className="btn-mini" onClick={() => togglePin(node.node_id)}>
          {st.pinned.includes(node.node_id) ? 'Unpin' : 'Pin'}
        </button>
        <button
          type="button"
          className="btn-mini"
          onClick={() =>
            askInterp(
              `Graph section, node ${node.node_id} (${describeNode(node, g.metadata.prompt_tokens)})`,
            )
          }
        >
          Ask
        </button>
      </div>
      <div style={S.heading}>Strongest inputs</div>
      <ol style={G.edges}>
        {topEdges(g.links, node.node_id, 'in').map((l, i) =>
          edgeRow(byId.get(l.source), l.weight, `i${i}`),
        )}
      </ol>
      <div style={S.heading}>Strongest outputs</div>
      <ol style={G.edges}>
        {topEdges(g.links, node.node_id, 'out').map((l, i) =>
          edgeRow(byId.get(l.target), l.weight, `o${i}`),
        )}
      </ol>
      {card && !card.error && <NeuronpediaCard card={card} examples={2} />}
    </aside>
  );
}

function Supernodes() {
  const st = useGraph();
  if (!st.supernodes.length) return null;
  return (
    <section style={A.ribbonWrap} aria-label="Supernodes">
      <div style={A.panelHead}>
        <span style={S.heading}>Supernodes</span>
      </div>
      <ol style={G.edges}>
        {st.supernodes.map((group, i) => (
          <li key={`${group[0]}-${i}`} style={G.group}>
            <span style={G.tipTitle}>{group[0]}</span>
            <span style={S.mono}>{group.length - 1} nodes</span>
            <button type="button" className="btn-mini" onClick={() => ungroup(i)}>
              Ungroup
            </button>
          </li>
        ))}
      </ol>
    </section>
  );
}

const G = {
  row: {
    display: 'flex',
    alignItems: 'flex-end',
    gap: 8,
    flexWrap: 'wrap',
  } satisfies CSSProperties,
  field: { display: 'grid', gap: 4, minWidth: 0 } satisfies CSSProperties,
  check: { display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 } satisfies CSSProperties,
  controls: {
    display: 'flex',
    alignItems: 'flex-end',
    gap: 16,
    flexWrap: 'wrap',
  } satisfies CSSProperties,
  prompt: { display: 'grid', gap: 2, flex: '1 1 100%' } satisfies CSSProperties,
  promptText: { fontFamily: 'var(--font-mono)', fontSize: 13 } satisfies CSSProperties,
  layout: {
    display: 'grid',
    gridTemplateColumns: 'minmax(0, 1fr) 300px',
    gap: 14,
    alignItems: 'start',
  } satisfies CSSProperties,
  canvasWrap: {
    position: 'relative',
    minWidth: 0,
    background: 'var(--bg-secondary)',
    borderTop: '2px solid var(--accent)',
  } satisfies CSSProperties,
  tip: {
    position: 'absolute',
    width: 250,
    padding: '6px 8px',
    background: 'var(--bg-primary)',
    borderLeft: '2px solid var(--accent)',
    pointerEvents: 'none',
    display: 'grid',
    gap: 2,
  } satisfies CSSProperties,
  tipTitle: {
    fontFamily: 'var(--font-mono)',
    fontSize: 12,
    fontWeight: 700,
  } satisfies CSSProperties,
  legend: { padding: '4px 8px' } satisfies CSSProperties,
  label: { fontSize: 13 } satisfies CSSProperties,
  edges: {
    listStyle: 'none',
    margin: 0,
    padding: 0,
    display: 'grid',
    gap: 1,
  } satisfies CSSProperties,
  edge: {
    display: 'grid',
    gridTemplateColumns: '64px minmax(0, 1fr)',
    gap: 8,
    width: '100%',
    height: 24,
    padding: '0 4px',
    border: 'none',
    borderRadius: 0,
    background: 'transparent',
    color: 'var(--text-primary)',
    cursor: 'pointer',
    textAlign: 'left',
    alignItems: 'center',
  } satisfies CSSProperties,
  edgeName: {
    fontSize: 12,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  } satisfies CSSProperties,
  group: { display: 'flex', alignItems: 'center', gap: 10 } satisfies CSSProperties,
};

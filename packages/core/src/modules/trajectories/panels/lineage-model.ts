/**
 * The lineage view's data model: agents, the harness revisions each one went
 * through, and the runs each revision produced — as one tree.
 *
 * **A harness's parent is the same agent's previous harness.** Fingerprints are
 * content-addressed, so a new one means *something* in the agent's configuration
 * changed. Chaining them by `first_seen` turns "18 unrelated fingerprints" into a
 * history you can read left to right, and the edge says what the change was. That
 * diff is computed here rather than in the backend because the list endpoint already
 * returns every field it needs.
 *
 * **Colour follows the run's verdict, and ungraded is not success.** A `complete`
 * run nobody has judged is grey, not accent. The same rule as `outcomeKind` in
 * `common.tsx` and the null success rate in `analyze.py`: finishing is not winning.
 *
 * Pure functions only — no React, no DOM — so the layout and the diffing are tested
 * directly (`__tests__/lineage-model.test.ts`).
 */
import { stratify, tree } from 'd3-hierarchy';

import type { Harness, TrajectoryRun } from '../api';

export type Level = 'harnesses' | 'runs';

/**
 * How a node relates to its parent.
 *
 * Harness-to-harness edges name the most significant thing that changed, in the
 * order a reader would care: a different model is a different agent in all but
 * name; a prompt edit is the usual experiment; tools and params are tuning.
 * `schema` means the fingerprint moved but none of the fields shown here did — a
 * tool's JSON schema changed, which the list endpoint does not carry in full.
 */
export type EdgeKind = 'origin' | 'model' | 'prompt' | 'tools' | 'params' | 'schema' | 'member';

/** A run's verdict as a mark colour. */
export type Verdict = 'success' | 'failure' | 'partial' | 'ungraded' | 'running' | 'abandoned';

export interface LineageRow {
  key: string;
  /** Null only for the hidden root. */
  pkey: string | null;
  kind: 'root' | 'agent' | 'harness' | 'run';
  label: string;
  agent: string;
  /** The harness row's own harness, or a run row's harness. */
  harness: Harness | null;
  run: TrajectoryRun | null;
  /** Every run at or beneath this row. */
  runs: TrajectoryRun[];
  edge: EdgeKind;
  /** What changed relative to the parent harness, one line each. */
  changes: string[];
}

export interface Lineage {
  rows: LineageRow[];
  /** Run id → the key of the row that draws it at this level. */
  memberOf: Record<string, string>;
  /** Delegation: a run started by another run. Both ends are run ids. */
  delegations: { parent: string; child: string }[];
  /** Harness revision times, for the inset's change ticks. */
  revisions: { key: string; t: number; edge: EdgeKind }[];
}

const ROOT = '__root';

export function agentOf(run: TrajectoryRun): string {
  return run.agent_id || run.agent_name || 'external';
}

export function verdictOf(run: TrajectoryRun): Verdict {
  if (run.status === 'running') return 'running';
  if (run.outcome === 'success') return 'success';
  if (run.outcome === 'failure' || run.status === 'failed') return 'failure';
  if (run.outcome === 'partial') return 'partial';
  if (run.status === 'abandoned') return 'abandoned';
  return 'ungraded';
}

/** Someone, or something, has judged this run — drawn as a ring. */
export function isGraded(run: TrajectoryRun): boolean {
  return run.outcome != null && run.outcome !== 'unknown';
}

/** The model's last path segment — `moonshotai/kimi-k3` reads as `kimi-k3`. */
export function shortModel(model: string): string {
  const tail = model.split('/').pop() ?? model;
  return tail || '(no model)';
}

function sameJson(a: unknown, b: unknown): boolean {
  return JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
}

/**
 * What changed from one harness to the next, most significant first.
 *
 * Returns the edge kind and one human line per change. Param values are shown
 * literally (`temperature 0.7 → 0.2`) because a param change is only legible as
 * the before and after.
 */
export function diffHarness(prev: Harness, next: Harness): { edge: EdgeKind; changes: string[] } {
  const changes: string[] = [];
  let edge: EdgeKind | null = null;
  const mark = (kind: EdgeKind) => {
    if (edge === null) edge = kind;
  };

  if (prev.model !== next.model || prev.provider !== next.provider) {
    mark('model');
    changes.push(`model ${shortModel(prev.model)} → ${shortModel(next.model)}`);
  }
  if (prev.system_prompt !== next.system_prompt) {
    mark('prompt');
    const delta = next.system_prompt.length - prev.system_prompt.length;
    changes.push(`prompt ${delta >= 0 ? '+' : '−'}${Math.abs(delta)} chars`);
  }
  const before = new Set(prev.tool_names);
  const after = new Set(next.tool_names);
  const added = next.tool_names.filter((t) => !before.has(t));
  const removed = prev.tool_names.filter((t) => !after.has(t));
  if (added.length || removed.length) {
    mark('tools');
    const parts = [];
    if (added.length) parts.push(`+${added.length}`);
    if (removed.length) parts.push(`−${removed.length}`);
    changes.push(`tools ${parts.join(' ')} (${next.tool_names.length})`);
  }
  const keys = new Set([...Object.keys(prev.params ?? {}), ...Object.keys(next.params ?? {})]);
  for (const key of [...keys].sort()) {
    const a = prev.params?.[key];
    const b = next.params?.[key];
    if (!sameJson(a, b)) {
      mark('params');
      changes.push(
        `${key} ${a === undefined ? '∅' : String(a)} → ${b === undefined ? '∅' : String(b)}`,
      );
    }
  }
  return { edge: edge ?? 'schema', changes };
}

/**
 * A harness the list endpoint did not return — a run whose fingerprint was pruned,
 * or a run recorded with none. Built from the run so the node still has a label.
 */
function syntheticHarness(fingerprint: string, run: TrajectoryRun): Harness {
  return {
    fingerprint,
    agent_id: run.agent_id,
    model: run.model,
    provider: run.provider,
    system_prompt: '',
    tool_names: [],
    params: {},
    label: run.model ? `${agentOf(run)} @ ${run.model}` : agentOf(run),
    first_seen: run.started_at,
    last_seen: run.started_at,
    run_count: 0,
  };
}

/**
 * Build the tree rows for one level.
 *
 * `harnesses` stops at harness nodes and folds each one's runs into it; `runs` adds
 * every run as a leaf. Child order is fixed — the next revision first, then the
 * runs by start time — so the revision chain forms the spine and runs fan off it.
 */
export function buildLineage(runs: TrajectoryRun[], harnesses: Harness[], level: Level): Lineage {
  const byFingerprint = new Map(harnesses.map((h) => [h.fingerprint, h]));
  const sorted = [...runs].sort((a, b) => a.started_at - b.started_at);

  // agent → fingerprint → runs, both in first-seen order.
  const agents = new Map<string, Map<string, TrajectoryRun[]>>();
  const harnessFor = new Map<string, Harness>();
  for (const run of sorted) {
    const agent = agentOf(run);
    const fingerprint = run.harness ?? `none:${agent}`;
    if (!harnessFor.has(fingerprint)) {
      harnessFor.set(
        fingerprint,
        byFingerprint.get(fingerprint) ?? syntheticHarness(fingerprint, run),
      );
    }
    let chain = agents.get(agent);
    if (!chain) agents.set(agent, (chain = new Map()));
    const list = chain.get(fingerprint);
    if (list) list.push(run);
    else chain.set(fingerprint, [run]);
  }

  const rows: LineageRow[] = [
    {
      key: ROOT,
      pkey: null,
      kind: 'root',
      label: '',
      agent: '',
      harness: null,
      run: null,
      runs: sorted,
      edge: 'origin',
      changes: [],
    },
  ];
  const memberOf: Record<string, string> = {};
  const revisions: Lineage['revisions'] = [];

  for (const [agent, chain] of agents) {
    const agentKey = `agent:${agent}`;
    const agentRuns = [...chain.values()].flat();
    rows.push({
      key: agentKey,
      pkey: ROOT,
      kind: 'agent',
      label: agent,
      agent,
      harness: null,
      run: null,
      runs: agentRuns,
      edge: 'origin',
      changes: [],
    });

    // Chain by when each harness first ran *here*, not by `first_seen`: a pulled
    // peer run can carry a harness first seen long before it reached this node.
    const ordered = [...chain.entries()].sort((a, b) => a[1][0].started_at - b[1][0].started_at);
    let prevKey = agentKey;
    let prevHarness: Harness | null = null;
    // Runs are pushed after the whole chain so each harness's next revision is its
    // first child — `stratify` keeps input order among siblings.
    const runRows: LineageRow[] = [];
    for (const [fingerprint, harnessRuns] of ordered) {
      const harness = harnessFor.get(fingerprint)!;
      const key = `harness:${fingerprint}`;
      const { edge, changes } = prevHarness
        ? diffHarness(prevHarness, harness)
        : { edge: 'origin' as EdgeKind, changes: [] };
      rows.push({
        key,
        pkey: prevKey,
        kind: 'harness',
        label: shortModel(harness.model) || fingerprint.slice(0, 8),
        agent,
        harness,
        run: null,
        runs: harnessRuns,
        edge,
        changes,
      });
      revisions.push({ key, t: harnessRuns[0].started_at, edge });
      for (const run of harnessRuns) {
        if (level === 'runs') {
          const runKey = `run:${run.id}`;
          memberOf[run.id] = runKey;
          runRows.push({
            key: runKey,
            pkey: key,
            kind: 'run',
            label: run.goal,
            agent,
            harness,
            run,
            runs: [run],
            edge: 'member',
            changes: [],
          });
        } else {
          memberOf[run.id] = key;
        }
      }
      prevKey = key;
      prevHarness = harness;
    }
    rows.push(...runRows);
  }

  const present = new Set(runs.map((r) => r.id));
  const delegations = sorted
    .filter((r) => r.parent_run_id && present.has(r.parent_run_id))
    .map((r) => ({ parent: r.parent_run_id!, child: r.id }));

  return { rows, memberOf, delegations, revisions };
}

/**
 * The keys to keep lit when one row is focused: its ancestors (how it came to be),
 * its descendants (what came of it), and the far end of any delegation it is part
 * of. Everything else dims.
 */
export function neighbourhood(lineage: Lineage, key: string): Set<string> {
  const parent = new Map(lineage.rows.map((r) => [r.key, r.pkey]));
  const children = new Map<string, string[]>();
  for (const row of lineage.rows) {
    if (!row.pkey) continue;
    const list = children.get(row.pkey);
    if (list) list.push(row.key);
    else children.set(row.pkey, [row.key]);
  }
  const keep = new Set<string>();
  const climb = (start: string) => {
    for (let k: string | null | undefined = start; k && k !== ROOT; k = parent.get(k)) keep.add(k);
  };
  climb(key);
  const stack = [key];
  while (stack.length) {
    const k = stack.pop()!;
    keep.add(k);
    stack.push(...(children.get(k) ?? []));
  }
  const row = lineage.rows.find((r) => r.key === key);
  for (const run of row?.runs ?? []) {
    for (const d of lineage.delegations) {
      const other = d.parent === run.id ? d.child : d.child === run.id ? d.parent : null;
      if (other && lineage.memberOf[other]) climb(lineage.memberOf[other]);
    }
  }
  return keep;
}

export interface PlacedNode {
  row: LineageRow;
  /** Screen-space x (generations run left to right). */
  x: number;
  y: number;
  depth: number;
}

export interface PlacedLink {
  source: PlacedNode;
  target: PlacedNode;
  edge: EdgeKind;
}

export interface Placement {
  nodes: PlacedNode[];
  links: PlacedLink[];
  byKey: Map<string, PlacedNode>;
  bounds: { x0: number; x1: number; y0: number; y1: number };
}

/**
 * Tidy-tree layout, generations along x.
 *
 * `nodeSize` rather than `size`: the stage is zoomable, so the tree keeps its own
 * natural spacing and the fit transform scales it into view. Fitting by `size`
 * would squash a 13-revision chain into whatever width the pane happened to have.
 */
export function placeLineage(
  lineage: Lineage,
  {
    rowGap,
    colGap,
    aspect,
    maxStretch = 4,
  }: {
    rowGap: number;
    colGap: number;
    /**
     * The stage's width/height. A long revision chain makes a tree far wider than
     * it is tall, and fitting that by width leaves it a thin line across a tall
     * stage. Given an aspect, rows are pulled apart (up to `maxStretch`) until the
     * tree's shape approaches the stage's. Columns never move: their spacing is
     * the spread control's.
     */
    aspect?: number;
    maxStretch?: number;
  },
): Placement {
  const root = stratify<LineageRow>()
    .id((d) => d.key)
    .parentId((d) => d.pkey)(lineage.rows);
  const laid = tree<LineageRow>().nodeSize([rowGap, colGap])(root);
  const drawn = laid.descendants().filter((d) => d.depth > 0);
  let stretch = 1;
  if (aspect && drawn.length > 1) {
    const w = Math.max(...drawn.map((d) => d.y)) - Math.min(...drawn.map((d) => d.y));
    const h = Math.max(...drawn.map((d) => d.x)) - Math.min(...drawn.map((d) => d.x));
    if (h > 0) stretch = Math.min(maxStretch, Math.max(1, w / h / aspect));
  }
  const nodes: PlacedNode[] = [];
  const byKey = new Map<string, PlacedNode>();
  for (const d of drawn) {
    // d3's tree is vertical; swap so depth runs along x.
    const node = { row: d.data, x: d.y, y: d.x * stretch, depth: d.depth };
    nodes.push(node);
    byKey.set(d.data.key, node);
  }
  const links: PlacedLink[] = [];
  for (const node of nodes) {
    const parent = node.row.pkey ? byKey.get(node.row.pkey) : undefined;
    if (parent) links.push({ source: parent, target: node, edge: node.row.edge });
  }
  const xs = nodes.map((n) => n.x);
  const ys = nodes.map((n) => n.y);
  const bounds = nodes.length
    ? { x0: Math.min(...xs), x1: Math.max(...xs), y0: Math.min(...ys), y1: Math.max(...ys) }
    : { x0: 0, x1: 0, y0: 0, y1: 0 };
  return { nodes, links, byKey, bounds };
}

/** A horizontal S-curve between two placed points. */
export function linkPath(a: { x: number; y: number }, b: { x: number; y: number }): string {
  const mx = (a.x + b.x) / 2;
  return `M${a.x},${a.y}C${mx},${a.y} ${mx},${b.y} ${b.x},${b.y}`;
}

/** The transform that fits `bounds` into a `width`×`height` stage with `pad` margin. */
export function fitTransform(
  bounds: Placement['bounds'],
  width: number,
  height: number,
  { pad = 48, maxScale = 2 }: { pad?: number; maxScale?: number } = {},
): { x: number; y: number; k: number } {
  const w = Math.max(1, bounds.x1 - bounds.x0);
  const h = Math.max(1, bounds.y1 - bounds.y0);
  const k = Math.max(0.05, Math.min((width - 2 * pad) / w, (height - 2 * pad) / h, maxScale));
  return {
    x: (width - w * k) / 2 - bounds.x0 * k,
    y: (height - h * k) / 2 - bounds.y0 * k,
    k,
  };
}

// --- the inset: runs over time ------------------------------------------------

export type YMetric = 'steps' | 'duration' | 'tokens';

export function metricValue(run: TrajectoryRun, metric: YMetric): number | null {
  if (metric === 'steps') return run.steps;
  if (metric === 'duration') return run.duration_ms;
  return run.tokens_out;
}

/** Durations and token counts span four orders of magnitude; steps do not. */
export const METRIC_IS_LOG: Record<YMetric, boolean> = {
  steps: false,
  duration: true,
  tokens: true,
};

export interface Axis {
  map: (v: number) => number;
  ticks: number[];
}

/**
 * A y axis over `values`, into pixel range `[bottom, top]`.
 *
 * Linear axes start at zero and round their top to a 1/2/5 step. Log axes snap to
 * whole decades so every tick is a power of ten — `10s`, `100s` — which is the only
 * labelling a log scale can carry without the reader doing arithmetic.
 */
export function yAxis(values: number[], log: boolean, bottom: number, top: number): Axis {
  const finite = values.filter((v) => Number.isFinite(v));
  if (log) {
    const positive = finite.map((v) => Math.max(1, v));
    const lo = Math.floor(Math.log10(Math.min(...positive, 10)));
    const hi = Math.max(lo + 1, Math.ceil(Math.log10(Math.max(...positive, 10))));
    const ticks: number[] = [];
    for (let e = lo; e <= hi; e++) ticks.push(10 ** e);
    return {
      map: (v) => bottom + ((Math.log10(Math.max(1, v)) - lo) / (hi - lo)) * (top - bottom),
      ticks,
    };
  }
  const max = Math.max(1, ...finite);
  const raw = max / 4;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const hi = Math.ceil(max / step) * step;
  const ticks: number[] = [];
  for (let v = 0; v <= hi + 1e-9; v += step) ticks.push(v);
  return { map: (v) => bottom + (v / hi) * (top - bottom), ticks };
}

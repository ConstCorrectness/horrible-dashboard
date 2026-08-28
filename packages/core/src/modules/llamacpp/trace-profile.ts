/**
 * A forward pass, summarized by depth.
 *
 * ## Why this needs a route, when the record list is already here
 *
 * The obvious implementation is none at all: `getTrace` returns every record, each
 * with a `summary` field, so arranging those by depth would cost no request. That
 * was the first cut, and against every trace on disk it rendered an empty chart.
 *
 * `tracer._capture` writes `summary` **only** for `summary`-fidelity records — the
 * ones that hold no data. So on an fp16 trace the manifest carries 170 records and
 * zero statistics, and a client-side profile shows "0 of 28 layers measured" while
 * blaming the fidelity. Exactly the records with data carry no statistics. It is the
 * same fact that forced `GET /series` to exist, one axis over, and
 * `GET /traces/{id}/profile` is its sibling: one request per (pass, statistic),
 * every record reduced server-side over its **whole** tensor.
 *
 * Everything below is then pure arrangement of that response — which role, which
 * grid — so changing the node under study costs nothing.
 *
 * ## The rule that matters
 *
 * **A missing statistic is `null`, never `0`.** A `summary`-fidelity record can be
 * missing a stat entirely, and zero is a measurement — plotting it would draw a
 * value that was never taken, and would drag every mean and every colour scale
 * toward it. `null` reaches `viz/spark.ts` as a gap and is drawn as one.
 */
import type { ProfilePoint } from './api';
import { nodeKind, type NodeKind } from './node-kind';

/** The statistics a record can carry. `count` is a size, not a measurement. */
export const TRACE_STATS = ['rms', 'absMax', 'mean', 'min', 'max', 'zeroFraction'] as const;
export type TraceStat = (typeof TRACE_STATS)[number];

export const STAT_LABELS: Record<TraceStat, string> = {
  rms: 'RMS',
  absMax: 'Largest magnitude',
  mean: 'Mean',
  min: 'Minimum',
  max: 'Maximum',
  zeroFraction: 'Fraction of zeros',
};

/**
 * A node's role, which is its name with the block index removed — so `kqv_out-3`
 * and `kqv_out-27` are the same role at two depths, which is the only way to
 * compare them.
 *
 * Two naming schemes reach here and only one of them is `blk.N.`. Graph nodes named
 * by llama.cpp's `cb()` carry the block index as a **`-N` suffix** (`l_out-17`,
 * `kqv_out-3`); tensor-derived names carry it as a `blk.N.` segment. Handling only
 * the second left every activation name unchanged, so each depth read as its own
 * role and nothing was ever profilable.
 */
export function roleOf(name: string): string {
  const withoutSegment = name.replace(/(?:^|\.)blk\.\d+\./, (m) => (m.startsWith('.') ? '.' : ''));
  return withoutSegment.replace(/-\d+$/, '') || name;
}

/** Roles that appear at more than one depth, commonest first. A node captured once
 * is not a profile, so it is not offered. */
export function profilableRoles(points: ProfilePoint[]): string[] {
  const depths = new Map<string, Set<number>>();
  for (const point of points) {
    if (point.layer === null) continue;
    const role = roleOf(point.name);
    const set = depths.get(role) ?? new Set<number>();
    set.add(point.layer);
    depths.set(role, set);
  }
  return [...depths.entries()]
    .filter(([, set]) => set.size > 1)
    .sort((a, b) => b[1].size - a[1].size)
    .map(([role]) => role);
}

/** A point that sits at a known depth — the layerless nodes (`inp_embd`,
 * `result_norm`, `result_output`) are not on this axis at all. */
export interface LayerPoint extends ProfilePoint {
  layer: number;
}

/** One role's statistic against depth, in depth order. */
export function profileByLayer(points: ProfilePoint[], role: string): LayerPoint[] {
  return points
    .filter((p): p is LayerPoint => p.layer !== null && roleOf(p.name) === role)
    .sort((a, b) => a.layer - b.layer);
}

/* ------------------------------------------------------------- the role grid -- */

export interface RoleGrid {
  kinds: NodeKind[];
  layers: number[];
  /** `cells[kindIndex][layerIndex]` — 0..1 within its own ROW, or null. */
  cells: (number | null)[][];
  /** The raw value behind each cell, for the tooltip. */
  raw: (number | null)[][];
  /** Per row, the magnitude that maps to 1. */
  rowScale: (number | null)[];
}

/**
 * The whole pass as one fingerprint: node kind against depth.
 *
 * **Normalized per row, not globally.** An attention score and an FFN activation
 * are different quantities in different units; a single scale would render every
 * row but the largest as uniformly blank, which reads as "nothing happens there".
 * Per-row normalization asks the only question a heat grid can honestly answer
 * here: where along the depth is *this* quantity large?
 */
export function roleGrid(points: ProfilePoint[]): RoleGrid {
  const layers = [
    ...new Set(points.filter((p) => p.layer !== null).map((p) => p.layer as number)),
  ].sort((a, b) => a - b);

  const byKind = new Map<NodeKind, Map<number, number[]>>();
  for (const point of points) {
    if (point.layer === null) continue;
    // `!== null`, not falsiness: a genuine 0.0 mean is a measurement and belongs in
    // the grid. Only an absent one is skipped.
    if (point.value === null || !Number.isFinite(point.value)) continue;
    const kind = nodeKind(point.name);
    const row = byKind.get(kind) ?? new Map<number, number[]>();
    const cell = row.get(point.layer) ?? [];
    cell.push(point.value);
    row.set(point.layer, cell);
    byKind.set(kind, row);
  }

  const kinds = [...byKind.keys()].sort();
  const raw: (number | null)[][] = [];
  const cells: (number | null)[][] = [];
  const rowScale: (number | null)[] = [];

  for (const kind of kinds) {
    const row = byKind.get(kind);
    // Several records of one kind can land on one layer (q, k and v are all
    // attention). Their mean is the honest cell: a max would let one outlier
    // stand for the group.
    const values = layers.map((layer) => {
      const list = row?.get(layer);
      if (!list || list.length === 0) return null;
      return list.reduce((sum, v) => sum + v, 0) / list.length;
    });
    const scale = Math.max(0, ...values.map((v) => (v === null ? 0 : Math.abs(v))));
    raw.push(values);
    rowScale.push(scale || null);
    cells.push(values.map((v) => (v === null || !scale ? null : v / scale)));
  }

  return { kinds, layers, cells, raw, rowScale };
}

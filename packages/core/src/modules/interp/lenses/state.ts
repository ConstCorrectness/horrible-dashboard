/**
 * The lens comparison's state, outside any component: one trace, one position,
 * every lens installed for its model read at that position, and a tracked token's
 * rank under each.
 *
 * Module-level like the suite's other stores, so the digest and `interp.lenses`
 * read the comparison whether the tab is open or not.
 */
import { useSyncExternalStore } from 'react';

import {
  getLensGrid,
  getLensTrack,
  listLenses,
  listTraces,
  type LensGrid,
  type LensSpec,
  type TraceSummary,
} from '../../llamacpp/api';

export interface LensCompareState {
  traces: TraceSummary[];
  traceId: string | null;
  lenses: LensSpec[];
  /** Prompt position read; defaults to the last prompt token. */
  position: number | null;
  /** By lens id: the grid at `position` (top-k per layer). */
  grids: Record<string, LensGrid>;
  /** A vocabulary token followed across layers, and its rank under each lens. */
  track: {
    tokenId: number;
    text: string;
    ranks: Record<string, Record<number, number | null>>;
  } | null;
  loading: boolean;
  error: string | null;
}

let state: LensCompareState = {
  traces: [],
  traceId: null,
  lenses: [],
  position: null,
  grids: {},
  track: null,
  loading: false,
  error: null,
};
const listeners = new Set<() => void>();

function set(patch: Partial<LensCompareState>): void {
  state = { ...state, ...patch };
  for (const l of listeners) l();
}

export const lensCompareStore = {
  get: (): LensCompareState => state,
  subscribe(l: () => void): () => void {
    listeners.add(l);
    return () => listeners.delete(l);
  },
};

export function useLensCompare(): LensCompareState {
  return useSyncExternalStore(lensCompareStore.subscribe, lensCompareStore.get);
}

function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/** Top readouts per lens at this many candidates. */
const K = 3;

export async function refreshCompareTraces(): Promise<void> {
  try {
    const { traces } = await listTraces();
    set({ traces });
    if (!state.traceId && traces.length) await openCompareTrace(traces[0].traceId);
  } catch (err) {
    set({ error: message(err) });
  }
}

export async function openCompareTrace(traceId: string): Promise<void> {
  set({ traceId, lenses: [], grids: {}, track: null, position: null, error: null });
  try {
    const { lenses } = await listLenses(traceId);
    if (state.traceId !== traceId) return;
    set({ lenses });
    await readAt(null);
  } catch (err) {
    set({ error: message(err) });
  }
}

/** Read every lens at `position` (null: the last prompt token). */
export async function readAt(position: number | null): Promise<void> {
  const { traceId, lenses } = state;
  if (!traceId || !lenses.length) return;
  set({ loading: true, error: null });
  try {
    // The identity grid first, unfiltered by position only when we do not yet
    // know the prompt's length: it carries the tokens.
    const first = await getLensGrid(traceId, {
      lens: lenses[0].id,
      k: K,
      ...(position === null ? {} : { positions: [position] }),
    });
    const prompt = first.tokens.filter((t) => !t.generated);
    const at = position ?? (prompt.length ? prompt[prompt.length - 1].index : 0);
    const grids: Record<string, LensGrid> = {};
    const rest = await Promise.all(
      lenses.map((l) => getLensGrid(traceId, { lens: l.id, k: K, positions: [at] })),
    );
    lenses.forEach((l, i) => (grids[l.id] = rest[i]));
    if (state.traceId !== traceId) return;
    set({ grids, position: at, loading: false });
    if (state.track) await track(state.track.tokenId, state.track.text);
  } catch (err) {
    set({ loading: false, error: message(err) });
  }
}

/** Follow one vocabulary token across layers under every lens, at the position. */
export async function track(tokenId: number, text: string): Promise<void> {
  const { traceId, lenses, position } = state;
  if (!traceId || position === null) return;
  try {
    const tracks = await Promise.all(lenses.map((l) => getLensTrack(traceId, tokenId, l.id)));
    const ranks: Record<string, Record<number, number | null>> = {};
    lenses.forEach((l, i) => {
      const t = tracks[i];
      const col = t.positions.indexOf(position);
      ranks[l.id] = Object.fromEntries(
        t.layers.map((layer, row) => [layer, col < 0 ? null : (t.ranks[row]?.[col] ?? null)]),
      );
    });
    set({ track: { tokenId, text, ranks } });
  } catch (err) {
    set({ error: message(err) });
  }
}

export function clearTrack(): void {
  set({ track: null });
}

/** The top-1 reading of each layer at the position, per lens. */
export function topByLayer(grid: LensGrid): Record<number, string | null> {
  return Object.fromEntries(
    grid.layers.map((layer, row) => [layer, grid.cells[row]?.[0]?.texts?.[0] ?? null]),
  );
}

/**
 * The first layer at which each lens reads the tracked token in its top `within` —
 * the number the comparison exists to show ("the J-lens has it by layer 11").
 */
export function firstLayerWithin(ranks: Record<number, number | null>, within = 10): number | null {
  const hit = Object.entries(ranks)
    .map(([layer, rank]) => [Number(layer), rank] as const)
    .filter(([, rank]) => rank !== null && rank <= within)
    .sort((a, b) => a[0] - b[0])[0];
  return hit ? hit[0] : null;
}

/** A line for the suite digest. */
export function lensCompareDigest(current: LensCompareState = state): string | null {
  if (!current.traceId || current.lenses.length < 2 || current.position === null) return null;
  const names = current.lenses.map((l) => (l.kind === 'identity' ? 'logit lens' : l.label));
  let line = `lenses on ${current.traceId} at position ${current.position}: ${names.join(' vs ')}`;
  if (current.track) {
    const parts = current.lenses.map((l) => {
      const first = firstLayerWithin(current.track!.ranks[l.id] ?? {});
      return `${l.kind === 'identity' ? 'logit' : 'fitted'} top-10 from L${first ?? '—'}`;
    });
    line += `; "${current.track.text}" ${parts.join(', ')}`;
  }
  return line;
}

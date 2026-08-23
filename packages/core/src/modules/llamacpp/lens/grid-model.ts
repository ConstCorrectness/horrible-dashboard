/**
 * The arithmetic behind the lens grid, kept out of the components.
 *
 * Everything here is pure and unit-tested. What a cell *says* comes from the
 * backend, which owns the model; what a cell *looks like* is a reading decision
 * and belongs on this side — and reading decisions are exactly the thing that is
 * quietly wrong when it lives inside a render function.
 */

import type { LensCell, LensGrid, LensTrack, TraceToken } from '../api';

/** The layer index the input embedding occupies. Mirrors `lens.EMBEDDING_LAYER`. */
export const EMBEDDING_LAYER = -1;

export function layerLabel(layer: number): string {
  return layer === EMBEDDING_LAYER ? 'emb' : `L${layer}`;
}

/**
 * A token as it should read in a strip: whitespace made visible, length capped.
 *
 * A leading space is the difference between `Paris` and ` Paris`, which are
 * different tokens with different ranks — rendering both as "Paris" would make
 * two rows of the grid look like one finding.
 */
export function displayToken(text: string, max = 12): string {
  const shown = text.replace(/\n/g, '⏎').replace(/\t/g, '⇥').replace(/ /g, '·');
  if (!shown) return '∅';
  return shown.length > max ? `${shown.slice(0, max - 1)}…` : shown;
}

/**
 * How strongly to tint a cell, from the top candidate's share among the shown
 * ones.
 *
 * Deliberately *not* the model's probability: `relProbs` is a softmax over the
 * top-k alone, and calling a 0.9 share of five candidates "90% confident" would
 * be inventing a number. It is a reading aid for "this cell is decided" versus
 * "this cell is a coin toss", and nothing is labelled with it.
 */
export function cellStrength(cell: LensCell | undefined): number {
  if (!cell || !cell.relProbs.length) return 0;
  const top = cell.relProbs[0] ?? 0;
  if (cell.relProbs.length < 2) return 1;
  // Rescale so an even split across k candidates reads as 0 rather than as 1/k.
  const floor = 1 / cell.relProbs.length;
  return Math.max(0, Math.min(1, (top - floor) / (1 - floor)));
}

export function cellBackground(cell: LensCell | undefined): string {
  const strength = cellStrength(cell);
  return `rgb(110 168 254 / ${(strength * 34).toFixed(1)}%)`;
}

/**
 * A rank as a colour, log-scaled.
 *
 * Rank 1 and rank 5 are a world apart; rank 4000 and rank 8000 are the same
 * answer ("not on its mind"). A linear ramp over a 262k vocabulary would paint
 * every cell the same shade and show nothing.
 */
export function rankStrength(rank: number, vocab: number): number {
  if (!Number.isFinite(rank) || rank < 1) return 0;
  const span = Math.log(Math.max(vocab, 2));
  return Math.max(0, Math.min(1, 1 - Math.log(rank) / span));
}

export function rankBackground(rank: number, vocab: number): string {
  return `rgb(120 210 160 / ${(rankStrength(rank, vocab) * 55).toFixed(1)}%)`;
}

/** Where a tracked token appears in a cell's candidate list, or `null`. */
export function candidateRank(cell: LensCell | undefined, tokenId: number): number | null {
  if (!cell) return null;
  const index = cell.ids.indexOf(tokenId);
  return index < 0 ? null : index + 1;
}

/** The tokens a fork may replace: the prompt, never the generated tail. */
export function editableTokens(tokens: TraceToken[]): TraceToken[] {
  return tokens.filter((token) => !token.generated);
}

export interface CellDiff {
  /** True when the two grids' top-1 tokens differ at this cell. */
  changed: boolean;
  /** The parent's top token, for the tooltip. Empty when the cell is missing. */
  was: string;
  now: string;
  /** How far the parent's top token fell in the fork, when it is still shown. */
  rankDelta: number | null;
}

/**
 * Compare two grids cell by cell.
 *
 * Aligned by (layer, position) rather than by array index: a fork can differ
 * from its parent in either — and comparing `cells[2][3]` against `cells[2][3]`
 * of a grid with a different layer subset silently compares different layers,
 * which is the one bug that would make every conclusion drawn here wrong.
 */
export function diffGrids(before: LensGrid, after: LensGrid): CellDiff[][] {
  const beforeAt = new Map<string, LensCell>();
  before.layers.forEach((layer, row) => {
    before.positions.forEach((position, col) => {
      const cell = before.cells[row]?.[col];
      if (cell) beforeAt.set(`${layer}:${position}`, cell);
    });
  });

  return after.layers.map((layer, row) =>
    after.positions.map((position, col) => {
      const now = after.cells[row]?.[col];
      const was = beforeAt.get(`${layer}:${position}`);
      if (!now || !was)
        return { changed: false, was: '', now: now?.texts[0] ?? '', rankDelta: null };
      const wasTop = was.texts[0] ?? '';
      const nowTop = now.texts[0] ?? '';
      const wasTopId = was.ids[0];
      const stillShown = wasTopId === undefined ? null : candidateRank(now, wasTopId);
      return {
        changed: wasTop !== nowTop,
        was: wasTop,
        now: nowTop,
        rankDelta: stillShown === null ? null : stillShown - 1,
      };
    }),
  );
}

/** A track's rank at (layer, position), or `null` when it is off the grid. */
export function trackRank(track: LensTrack, layer: number, position: number): number | null {
  const row = track.layers.indexOf(layer);
  const col = track.positions.indexOf(position);
  if (row < 0 || col < 0) return null;
  return track.ranks[row]?.[col] ?? null;
}

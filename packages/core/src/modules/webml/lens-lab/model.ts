/**
 * The Lens Lab's arithmetic, kept out of React so it is tested without a model:
 * what a lens cell says, the guessing game's puzzles and score, and the
 * comparison of two models read on the same text.
 */
import type { LayerLens } from '@horrible/webml';

import { settlesAt, type LensToken } from '../lens-run';

// ── Cells ────────────────────────────────────────────────────────────────────

/** The probability layer `layer` gives `token` — 0 when it is not in that layer's top 5. */
export function layerP(layer: LayerLens | undefined, token: string): number {
  return layer?.top.find((t) => t.token === token)?.p ?? 0;
}

/** Whether `token` is in the layer's readout at all (its top 5). */
export function inTop(layer: LayerLens | undefined, token: string): boolean {
  return !!layer?.top.some((t) => t.token === token);
}

/** A token as a cell or a label can show it: whitespace made visible, long ones cut. */
export function showToken(token: string, max = 10): string {
  const t = token.replace(/\n/g, '↵').replace(/\t/g, '⇥').replace(/ /g, '·');
  if (!t) return '∅';
  return t.length > max ? `${t.slice(0, max - 1)}…` : t;
}

// ── The guessing game ────────────────────────────────────────────────────────

/** One puzzle: a token of the reply, and the layer its prediction settled at. */
export interface Puzzle {
  index: number;
  token: string;
  settles: number;
}

/**
 * The tokens worth guessing: ones whose prediction settled somewhere in the
 * stack, not at layer 0 (the input embedding already said it, which teaches
 * nothing) and not never (a sampled runner-up has no answer). Whitespace-only
 * tokens are skipped too: "at which layer did it know there would be a space"
 * is not a question anyone enjoys.
 */
export function puzzleCandidates(tokens: LensToken[]): Puzzle[] {
  const out: Puzzle[] = [];
  tokens.forEach((t, index) => {
    const settles = settlesAt(t);
    if (settles === null || settles === 0 || !t.token.trim()) return;
    out.push({ index, token: t.token, settles });
  });
  return out;
}

/**
 * `count` puzzles for a game, spread across the reply rather than bunched at its
 * start, in reply order. `random` is injectable for tests.
 */
export function pickPuzzles(
  tokens: LensToken[],
  count: number,
  random: () => number = Math.random,
): Puzzle[] {
  const pool = puzzleCandidates(tokens);
  if (pool.length <= count) return pool;
  // One pick per equal slice of the pool keeps the game moving through the text.
  const picked: Puzzle[] = [];
  for (let i = 0; i < count; i++) {
    const from = Math.floor((i * pool.length) / count);
    const to = Math.floor(((i + 1) * pool.length) / count);
    picked.push(pool[from + Math.floor(random() * (to - from))]);
  }
  return picked;
}

/**
 * Points for a guess: 100 for the exact layer, falling linearly to 0 at a quarter
 * of the stack away. A quarter, not the whole stack: on a 28-layer model a guess
 * 14 layers off is not "half right".
 */
export function guessScore(guess: number, settles: number, layers: number): number {
  const reach = Math.max(1, layers / 4);
  return Math.max(0, Math.round(100 * (1 - Math.abs(guess - settles) / reach)));
}

/** How a guess reads, in a few words. */
export function guessVerdict(guess: number, settles: number): string {
  const off = guess - settles;
  if (off === 0) return 'Exactly';
  const n = Math.abs(off);
  return `${n} layer${n === 1 ? '' : 's'} ${off < 0 ? 'too early' : 'too late'}`;
}

// ── Comparing two models on one text ─────────────────────────────────────────

/** One side of a comparison: a forced run's steps, each with its lens. */
export interface ForcedRun {
  model: string;
  steps: { token: string; p: number; entropy: number; layers?: LayerLens[] }[];
}

export interface PositionDiff {
  index: number;
  token: string;
  pA: number;
  pB: number;
  /** Where each model settled on this token; null when it never did. */
  settleA: number | null;
  settleB: number | null;
  /** Per layer: the token's probability under B minus under A (top-5 readouts). */
  layerDelta: number[];
}

export interface Comparison {
  /** False when the two models tokenized the text differently. */
  aligned: boolean;
  /** Positions compared (the shorter run's length when not aligned). */
  positions: PositionDiff[];
  layers: number;
  /** Mean log-probability per token, nats: higher is more confident. */
  meanLogpA: number;
  meanLogpB: number;
  /** Positions where B's own top choice differs from A's. */
  disagreements: number;
  /** Mean settle layer over positions where both settled. */
  meanSettleA: number | null;
  meanSettleB: number | null;
}

const settle = (token: string, p: number, layers?: LayerLens[]): number | null =>
  layers ? settlesAt({ token, p, layers }) : null;

const meanLogp = (ps: number[]): number =>
  ps.length ? ps.reduce((sum, p) => sum + Math.log(Math.max(p, 1e-9)), 0) / ps.length : 0;

/** A against B, position by position, on the text both were made to read. */
export function compareRuns(a: ForcedRun, b: ForcedRun): Comparison {
  const n = Math.min(a.steps.length, b.steps.length);
  const aligned =
    a.steps.length === b.steps.length && a.steps.every((s, i) => s.token === b.steps[i].token);
  const layers = Math.max(a.steps[0]?.layers?.length ?? 0, b.steps[0]?.layers?.length ?? 0);
  const positions: PositionDiff[] = [];
  let disagreements = 0;
  const settlesA: number[] = [];
  const settlesB: number[] = [];
  for (let i = 0; i < n; i++) {
    const sa = a.steps[i];
    const sb = b.steps[i];
    const token = sa.token;
    const settleA = settle(token, sa.p, sa.layers);
    const settleB = settle(token, sb.p, sb.layers);
    if (settleA !== null && settleB !== null) {
      settlesA.push(settleA);
      settlesB.push(settleB);
    }
    const topA = sa.layers?.at(-1)?.top[0]?.token;
    const topB = sb.layers?.at(-1)?.top[0]?.token;
    if (topA !== undefined && topB !== undefined && topA !== topB) disagreements++;
    // Layer counts can differ (a base and a fine-tune never do, but two unrelated
    // models might): line the stacks up by depth fraction, not by index.
    const layerDelta = Array.from({ length: layers }, (_, l) => {
      const at = (run: typeof sa) => {
        const ls = run.layers;
        if (!ls?.length) return 0;
        return layerP(ls[Math.round((l / Math.max(1, layers - 1)) * (ls.length - 1))], token);
      };
      return at(sb) - at(sa);
    });
    positions.push({ index: i, token, pA: sa.p, pB: sb.p, settleA, settleB, layerDelta });
  }
  const mean = (xs: number[]) => (xs.length ? xs.reduce((s, x) => s + x, 0) / xs.length : null);
  return {
    aligned,
    positions,
    layers,
    meanLogpA: meanLogp(a.steps.slice(0, n).map((s) => s.p)),
    meanLogpB: meanLogp(b.steps.slice(0, n).map((s) => s.p)),
    disagreements,
    meanSettleA: mean(settlesA),
    meanSettleB: mean(settlesB),
  };
}

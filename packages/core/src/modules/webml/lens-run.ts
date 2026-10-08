/**
 * The window's latest logit-lens run: what the GGUF engine read out, layer by
 * layer, at each token of the last generation that asked for the lens — from the
 * playground, a `{webllm}` block, any surface. The model explorer draws it as a
 * layer × token grid beside the model's structure.
 *
 * It fills as the tokens arrive, so the explorer shows a reply's lens while it is
 * still being written. One run is kept: a lens is per reply, and the newest is the
 * one being looked at.
 */
import { useSyncExternalStore } from 'react';

import type { LayerLens, StepEvent } from '@horrible/webml';

export interface LensToken {
  /** The token chosen. */
  token: string;
  /** Its probability under the sampler's distribution. */
  p: number;
  /** First layer to last. */
  layers: LayerLens[];
}

export interface LensRun {
  /** Bumped per run, so a view can tell a new reply from more of the same one. */
  id: number;
  model: string;
  /** Layers per token: the model's block count. */
  layers: number;
  tokens: LensToken[];
  /** Still generating. */
  live: boolean;
}

/** A long reply's lens is kept to its last this-many tokens. */
export const LENS_RUN_TOKENS = 512;

let run: LensRun | null = null;
let seq = 0;
const listeners = new Set<() => void>();

function emit(): void {
  for (const l of listeners) l();
}

export const lensRuns = {
  get: (): LensRun | null => run,

  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },

  /** A generation with the lens began. */
  start(model: string): void {
    run = { id: ++seq, model, layers: 0, tokens: [], live: true };
    emit();
  },

  /** One generated token's step; steps without a lens (ONNX models) are skipped. */
  push(step: Pick<StepEvent, 'token' | 'p' | 'layers'>): void {
    if (!run?.live || !step.layers?.length) return;
    const tokens = [...run.tokens, { token: step.token, p: step.p, layers: step.layers }];
    run = {
      ...run,
      layers: step.layers.length,
      tokens: tokens.length > LENS_RUN_TOKENS ? tokens.slice(-LENS_RUN_TOKENS) : tokens,
    };
    emit();
  },

  /** The generation ended, however it ended. */
  finish(): void {
    if (!run?.live) return;
    run = { ...run, live: false };
    emit();
  },

  /** Tests. */
  reset(): void {
    run = null;
    emit();
  },
};

export function useLensRun(): LensRun | null {
  return useSyncExternalStore(lensRuns.subscribe, lensRuns.get, lensRuns.get);
}

/**
 * Where a token's prediction settles: the first layer from which the lens's best
 * token is the token chosen, at that layer and every one after it. Null when the
 * last layer's best is something else (a sampled runner-up).
 */
export function settlesAt(t: LensToken): number | null {
  let layer: number | null = null;
  for (let l = t.layers.length - 1; l >= 0; l--) {
    if (t.layers[l].top[0]?.token !== t.token) break;
    layer = l;
  }
  return layer;
}

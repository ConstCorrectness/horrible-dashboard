/**
 * Picking the next token from a row of logits, the way llama.cpp's default sampler
 * chain does: top-k, then top-p and min-p on the softmax of what is left, then
 * temperature, then a draw. Temperature 0 is greedy.
 *
 * GGUF files carry no generation config, so the filters use llama.cpp's defaults
 * (top-k 40, top-p 0.95, min-p 0.05); the caller sets the temperature.
 *
 * The returned distribution is the one the token was drawn from — removed
 * candidates at probability 0 — which is what the protocol's `step` events
 * promise (see protocol.ts).
 */
import { distribution, topK as largest, type Distribution } from '../distribution';

export interface SamplerOptions {
  /** 0 = greedy. */
  temperature: number;
  topK: number;
  topP: number;
  minP: number;
}

export const DEFAULT_SAMPLER: SamplerOptions = {
  temperature: 0.8,
  topK: 40,
  topP: 0.95,
  minP: 0.05,
};

export interface Sampled {
  id: number;
  /** Present when `record > 0`: the distribution sampled from, with that many alternatives. */
  dist: Distribution | null;
}

export function sample(
  logits: Float32Array,
  options: SamplerOptions,
  random: () => number = Math.random,
  record = 0,
): Sampled {
  if (options.temperature <= 0) {
    let best = 0;
    for (let i = 1; i < logits.length; i++) if (logits[i] > logits[best]) best = i;
    return { id: best, dist: record > 0 ? distribution(logits, record) : null };
  }

  // Top-k by logit (k ≤ 0 keeps everything).
  const k = options.topK > 0 ? Math.min(options.topK, logits.length) : logits.length;
  let candidates =
    k < logits.length
      ? largest(logits, k).map((c) => ({ id: c.id, logit: c.p }))
      : Array.from(logits, (logit, id) => ({ id, logit })).sort((a, b) => b.logit - a.logit);

  // Top-p and min-p on the softmax of the survivors, before temperature.
  const max = candidates[0].logit;
  const exps = candidates.map((c) => Math.exp(c.logit - max));
  const sum = exps.reduce((a, b) => a + b, 0);
  const probs = exps.map((e) => e / sum);
  let keep = candidates.length;
  if (options.topP < 1) {
    let cum = 0;
    for (let i = 0; i < probs.length; i++) {
      cum += probs[i];
      if (cum >= options.topP) {
        keep = i + 1;
        break;
      }
    }
  }
  if (options.minP > 0) {
    const floor = probs[0] * options.minP;
    let i = 1;
    while (i < keep && probs[i] >= floor) i++;
    keep = i;
  }
  candidates = candidates.slice(0, keep);

  // Temperature, then draw.
  const scaled = candidates.map((c) => c.logit / options.temperature);
  const top = scaled[0];
  const weights = scaled.map((s) => Math.exp(s - top));
  const total = weights.reduce((a, b) => a + b, 0);
  let r = random() * total;
  let id = candidates[candidates.length - 1].id;
  for (let i = 0; i < candidates.length; i++) {
    r -= weights[i];
    if (r <= 0) {
      id = candidates[i].id;
      break;
    }
  }

  let dist: Distribution | null = null;
  if (record > 0) {
    const row = new Float32Array(logits.length).fill(-Infinity);
    candidates.forEach((c, i) => (row[c.id] = scaled[i]));
    dist = distribution(row, record);
  }
  return { id, dist };
}

/** A seeded uniform [0, 1) source (mulberry32), for reproducible sampling. */
export function seededRandom(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

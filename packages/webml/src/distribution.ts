/**
 * The per-token view of a logits row: softmax probabilities, the top-k alternatives
 * and the entropy. Pure, so it is tested without a model.
 *
 * Masked entries (`-Infinity`, which top-k and min-p warpers write) get probability
 * 0 and contribute nothing to the entropy.
 */

export interface Distribution {
  /** Softmax over the row; reused across calls when `into` is passed. */
  probs: Float32Array;
  /** Highest-probability ids first. */
  top: { id: number; p: number }[];
  /** Shannon entropy in bits. */
  entropy: number;
}

export function distribution(
  logits: ArrayLike<number>,
  k: number,
  into?: Float32Array,
): Distribution {
  const n = logits.length;
  const probs = into && into.length === n ? into : new Float32Array(n);

  let max = -Infinity;
  for (let i = 0; i < n; i++) if (logits[i] > max) max = logits[i];
  if (max === -Infinity) {
    probs.fill(0);
    return { probs, top: [], entropy: 0 };
  }

  let sum = 0;
  for (let i = 0; i < n; i++) {
    const e = Math.exp(logits[i] - max);
    probs[i] = e;
    sum += e;
  }
  let entropy = 0;
  for (let i = 0; i < n; i++) {
    const p = probs[i] / sum;
    probs[i] = p;
    if (p > 0) entropy -= p * Math.log2(p);
  }

  return { probs, top: topK(probs, k), entropy };
}

/** The `k` largest entries, descending. A small insertion buffer: k is tiny, n is a vocab. */
export function topK(values: ArrayLike<number>, k: number): { id: number; p: number }[] {
  if (k <= 0) return [];
  const ids: number[] = [];
  const vals: number[] = [];
  for (let i = 0; i < values.length; i++) {
    const v = values[i];
    if (ids.length === k && v <= vals[k - 1]) continue;
    let j = ids.length < k ? ids.length : k - 1;
    while (j > 0 && vals[j - 1] < v) {
      if (j < k) {
        ids[j] = ids[j - 1];
        vals[j] = vals[j - 1];
      }
      j--;
    }
    ids[j] = i;
    vals[j] = v;
  }
  return ids.map((id, i) => ({ id, p: vals[i] }));
}

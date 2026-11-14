/**
 * Activation heat from a capture: a `Float32Array` of `layers × ffn` values (layer
 * major), e.g. each neuron's activation at the last prompt token.
 */

export interface TopNeuron {
  layer: number;
  neuron: number;
  value: number;
}

function checkCapture(capture: Float32Array, layers: number, ffn: number): void {
  if (capture.length < layers * ffn) {
    throw new Error(
      `heat: capture has ${capture.length} values, expected ${layers}×${ffn} = ${layers * ffn}`,
    );
  }
}

/**
 * One layer's activations, signed and divided by that layer's max |value| so they
 * lie in [-1, 1]. An all-zero (or all non-finite) layer gives zeros; non-finite
 * entries become 0.
 */
export function layerHeat(capture: Float32Array, layer: number, ffn: number): Float32Array {
  checkCapture(capture, layer + 1, ffn);
  const row = capture.subarray(layer * ffn, (layer + 1) * ffn);
  let max = 0;
  for (const v of row) if (Number.isFinite(v) && Math.abs(v) > max) max = Math.abs(v);
  const out = new Float32Array(ffn);
  if (max === 0) return out;
  for (let i = 0; i < ffn; i++) {
    const v = row[i]!;
    out[i] = Number.isFinite(v) ? v / max : 0;
  }
  return out;
}

/**
 * The `k` neurons with the largest |activation|, descending; ties go to the lower
 * layer, then the lower neuron. `opts.layer` restricts the search to one layer.
 * Runs in O(n log k) with a sorted window, so a full 36×9728 capture is cheap.
 */
export function topNeurons(
  capture: Float32Array,
  layers: number,
  ffn: number,
  k: number,
  opts: { layer?: number } = {},
): TopNeuron[] {
  checkCapture(capture, layers, ffn);
  if (!(k > 0)) return [];
  let from = 0;
  let to = layers;
  if (opts.layer !== undefined) {
    if (!Number.isInteger(opts.layer) || opts.layer < 0 || opts.layer >= layers) {
      throw new Error(`heat: layer ${opts.layer} is not in [0, ${layers - 1}]`);
    }
    from = opts.layer;
    to = opts.layer + 1;
  }
  // `best` holds flat indices sorted by |value| descending (scan order breaks ties).
  const best: number[] = [];
  const mag = (i: number): number => Math.abs(capture[i]!);
  for (let i = from * ffn; i < to * ffn; i++) {
    const v = capture[i]!;
    if (!Number.isFinite(v)) continue;
    const a = Math.abs(v);
    if (best.length === k && a <= mag(best[k - 1]!)) continue;
    let lo = 0;
    let hi = best.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (mag(best[mid]!) >= a) lo = mid + 1;
      else hi = mid;
    }
    best.splice(lo, 0, i);
    if (best.length > k) best.pop();
  }
  return best.map((i) => ({ layer: Math.floor(i / ffn), neuron: i % ffn, value: capture[i]! }));
}

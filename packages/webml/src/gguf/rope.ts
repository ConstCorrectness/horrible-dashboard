/**
 * The rotary-embedding angle table: cos and sin for every position and pair.
 *
 * Built the way ggml's CPU backend builds its rope cache
 * (`ggml_rope_cache_init`): theta starts at the position as a float32 and is
 * multiplied by `base^(-2 / dims)` once per pair, rounding to float32 each time,
 * and cos/sin are taken of that float32. Computing the "exact" angle in float64
 * instead drifts from llama.cpp by up to ~1e-2 radians late in a long context,
 * which is the kind of difference that only shows up as a wrong token at position
 * 3,000. A GPU's own sin/cos are also unreliable at angles in the thousands,
 * which is why this is a table and not shader math.
 *
 * Layout: `table[pos * dims + 2i]` = cos, `+ 1` = sin, for pair i < dims / 2.
 */
export function ropeTable(
  positions: number,
  dims: number,
  base: number,
): Float32Array<ArrayBuffer> {
  const f = Math.fround;
  const scale = f(Math.pow(f(base), f(-2 / dims)));
  const table = new Float32Array(positions * dims);
  for (let p = 0; p < positions; p++) {
    let theta = f(p);
    for (let i = 0; i < dims / 2; i++) {
      table[p * dims + 2 * i] = Math.cos(theta);
      table[p * dims + 2 * i + 1] = Math.sin(theta);
      theta = f(theta * scale);
    }
  }
  return table;
}

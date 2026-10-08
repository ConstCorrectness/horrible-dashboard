// F32 weights, read four at a time. Rows must be a multiple of 4 elements.
const UNIT: u32 = 4u;
const UNITS: u32 = 1u;
const BLOCK_BYTES: u32 = 16u;

fn unit_dot(block: u32, sub: u32, x: u32) -> f32 {
  let w = block >> 2u;
  return bitcast<f32>(W[w]) * X[x] + bitcast<f32>(W[w + 1u]) * X[x + 1u]
    + bitcast<f32>(W[w + 2u]) * X[x + 2u] + bitcast<f32>(W[w + 3u]) * X[x + 3u];
}

fn block_get(block: u32, j: u32) -> f32 {
  return bitcast<f32>(W[(block >> 2u) + j]);
}

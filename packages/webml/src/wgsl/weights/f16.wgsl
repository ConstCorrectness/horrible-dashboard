// F16 weights, read as pairs from one word. Rows must be a multiple of 2 elements.
const UNIT: u32 = 2u;
const UNITS: u32 = 1u;
const BLOCK_BYTES: u32 = 4u;

fn unit_dot(block: u32, sub: u32, x: u32) -> f32 {
  let v = unpack2x16float(W[block >> 2u]);
  return v.x * X[x] + v.y * X[x + 1u];
}

fn block_get(block: u32, j: u32) -> f32 {
  return unpack2x16float(W[block >> 2u])[j];
}

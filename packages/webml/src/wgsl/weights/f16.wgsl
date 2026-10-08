// F16 weights, read as pairs from one word. Rows must be a multiple of 2 elements.
const BLOCK: u32 = 2u;
const BLOCK_BYTES: u32 = 4u;

fn block_dot(off: u32, x: u32) -> f32 {
  let v = unpack2x16float(W[off >> 2u]);
  return v.x * X[x] + v.y * X[x + 1u];
}

fn block_get(off: u32, j: u32) -> f32 {
  return unpack2x16float(W[off >> 2u])[j];
}

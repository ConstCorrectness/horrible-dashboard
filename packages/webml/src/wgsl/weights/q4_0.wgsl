// Q4_0: 18 bytes per 32 values — f16 scale d, then 16 bytes of nibbles. Byte j holds
// value j in its low nibble and value j + 16 in its high one; x = d * (nibble - 8).
const UNIT: u32 = 32u;
const UNITS: u32 = 1u;
const BLOCK_BYTES: u32 = 18u;

fn unit_dot(block: u32, sub: u32, x: u32) -> f32 {
  var sum = 0.0;
  for (var j = 0u; j < 16u; j++) {
    let q = byte_at(block + 2u + j);
    sum += (f32(q & 15u) - 8.0) * X[x + j] + (f32(q >> 4u) - 8.0) * X[x + j + 16u];
  }
  return f16_at(block) * sum;
}

fn block_get(block: u32, j: u32) -> f32 {
  let q = byte_at(block + 2u + (j & 15u));
  let nibble = select(q & 15u, q >> 4u, j >= 16u);
  return f16_at(block) * (f32(nibble) - 8.0);
}

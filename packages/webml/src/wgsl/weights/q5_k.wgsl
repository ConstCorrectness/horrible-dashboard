// Q5_K: 176 bytes per 256 values — as Q4_K, plus 32 bytes `qh` at offset 16 whose
// bit s adds 16 to value l of sub-block s; the nibbles move to offset 48.
const UNIT: u32 = 32u;
const UNITS: u32 = 8u;
const BLOCK_BYTES: u32 = 176u;

fn q5k_value(block: u32, sub: u32, l: u32) -> f32 {
  let low = (byte_at(block + 48u + 32u * (sub >> 1u) + l) >> ((sub & 1u) * 4u)) & 15u;
  let high = (byte_at(block + 16u + l) >> sub) & 1u;
  return f32(low + 16u * high);
}

fn unit_dot(block: u32, sub: u32, x: u32) -> f32 {
  let sm = scale_min_k4(block + 4u, sub);
  var sqx = 0.0;
  var sx = 0.0;
  for (var l = 0u; l < 32u; l++) {
    let xv = X[x + l];
    sqx += q5k_value(block, sub, l) * xv;
    sx += xv;
  }
  return f16_at(block) * sm.x * sqx - f16_at(block + 2u) * sm.y * sx;
}

fn block_get(block: u32, j: u32) -> f32 {
  let sub = j / 32u;
  let sm = scale_min_k4(block + 4u, sub);
  return f16_at(block) * sm.x * q5k_value(block, sub, j % 32u) - f16_at(block + 2u) * sm.y;
}

// Q5_K: 176 bytes per 256 values — as Q4_K, plus 32 bytes `qh` at offset 16 whose
// bit s adds 16 to value l of sub-block s; the nibbles move to offset 48.
const UNIT: u32 = 32u;
const UNITS: u32 = 8u;
const BLOCK_BYTES: u32 = 176u;

// Values 4k … 4k + 3 of sub-block `sub`, before scale and min.
fn q5k_quads(block: u32, sub: u32, k: u32) -> vec4<f32> {
  let low = (word_at(block + 48u + 32u * (sub >> 1u) + 4u * k) >> ((sub & 1u) * 4u)) & 0x0F0F0F0Fu;
  let high = (word_at(block + 16u + 4u * k) >> sub) & 0x01010101u;
  return bytes4(low | (high << 4u));
}

fn q5k_value(block: u32, sub: u32, l: u32) -> f32 {
  let low = (byte_at(block + 48u + 32u * (sub >> 1u) + l) >> ((sub & 1u) * 4u)) & 15u;
  let high = (byte_at(block + 16u + l) >> sub) & 1u;
  return f32(low + 16u * high);
}

fn unit_dot(block: u32, sub: u32) -> f32 {
  let sm = scale_min_k4(block + 4u, sub);
  var sq = vec4<f32>(0.0);
  var sx = vec4<f32>(0.0);
  for (var k = 0u; k < 8u; k++) {
    sq += q5k_quads(block, sub, k) * xv[k];
    sx += xv[k];
  }
  return f16_at(block) * sm.x * hsum(sq) - f16_at(block + 2u) * sm.y * hsum(sx);
}

fn vals4(block: u32, j: u32) -> vec4<f32> {
  let sub = j / 32u;
  let sm = scale_min_k4(block + 4u, sub);
  return f16_at(block) * sm.x * q5k_quads(block, sub, (j % 32u) / 4u) - f16_at(block + 2u) * sm.y;
}

fn unit_prep(block: u32, sub: u32) -> vec4<f32> {
  let sm = scale_min_k4(block + 4u, sub);
  return vec4<f32>(f16_at(block) * sm.x, f16_at(block + 2u) * sm.y, 0.0, 0.0);
}

fn unit_quad(block: u32, sub: u32, k: u32, prep: vec4<f32>) -> vec4<f32> {
  return prep.x * q5k_quads(block, sub, k) - prep.y;
}

fn block_get(block: u32, j: u32) -> f32 {
  let sub = j / 32u;
  let sm = scale_min_k4(block + 4u, sub);
  return f16_at(block) * sm.x * q5k_value(block, sub, j % 32u) - f16_at(block + 2u) * sm.y;
}

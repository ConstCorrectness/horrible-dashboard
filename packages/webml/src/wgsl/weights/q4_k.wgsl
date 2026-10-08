// Q4_K: 144 bytes per 256 values — f16 d, f16 dmin, 12 bytes of 6-bit scales and
// mins, 128 bytes of nibbles. Eight sub-blocks of 32 (the units here):
// x = d·sc·q − dmin·m. Sub-block s reads bytes 32·(s/2)… of qs, low nibbles for
// even s, high for odd.
const UNIT: u32 = 32u;
const UNITS: u32 = 8u;
const BLOCK_BYTES: u32 = 144u;

fn q4k_nibbles(block: u32, sub: u32, k: u32) -> vec4<f32> {
  let w = word_at(block + 16u + 32u * (sub >> 1u) + 4u * k);
  return bytes4((w >> ((sub & 1u) * 4u)) & 0x0F0F0F0Fu);
}

fn unit_dot(block: u32, sub: u32) -> f32 {
  let sm = scale_min_k4(block + 4u, sub);
  var sq = vec4<f32>(0.0);
  var sx = vec4<f32>(0.0);
  for (var k = 0u; k < 8u; k++) {
    sq += q4k_nibbles(block, sub, k) * xv[k];
    sx += xv[k];
  }
  return f16_at(block) * sm.x * hsum(sq) - f16_at(block + 2u) * sm.y * hsum(sx);
}

fn vals4(block: u32, j: u32) -> vec4<f32> {
  let sub = j / 32u;
  let sm = scale_min_k4(block + 4u, sub);
  return f16_at(block) * sm.x * q4k_nibbles(block, sub, (j % 32u) / 4u) - f16_at(block + 2u) * sm.y;
}

// d·sc (x) and dmin·m (y) of the sub-block.
fn unit_prep(block: u32, sub: u32) -> vec4<f32> {
  let sm = scale_min_k4(block + 4u, sub);
  return vec4<f32>(f16_at(block) * sm.x, f16_at(block + 2u) * sm.y, 0.0, 0.0);
}

fn unit_quad(block: u32, sub: u32, k: u32, prep: vec4<f32>) -> vec4<f32> {
  return prep.x * q4k_nibbles(block, sub, k) - prep.y;
}

fn block_get(block: u32, j: u32) -> f32 {
  let sub = j / 32u;
  let l = j % 32u;
  let sm = scale_min_k4(block + 4u, sub);
  let v = f32((byte_at(block + 16u + 32u * (sub >> 1u) + l) >> ((sub & 1u) * 4u)) & 15u);
  return f16_at(block) * sm.x * v - f16_at(block + 2u) * sm.y;
}

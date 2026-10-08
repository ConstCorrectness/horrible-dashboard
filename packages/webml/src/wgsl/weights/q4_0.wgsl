// Q4_0: 18 bytes per 32 values — f16 scale d, then 16 bytes of nibbles. Byte j holds
// value j in its low nibble and value j + 16 in its high one; x = d * (nibble - 8).
const UNIT: u32 = 32u;
const UNITS: u32 = 1u;
const BLOCK_BYTES: u32 = 18u;

fn unit_dot(block: u32, sub: u32) -> f32 {
  var s = vec4<f32>(0.0);
  for (var k = 0u; k < 4u; k++) {
    let w = word_at(block + 2u + 4u * k);
    s += (bytes4(w & 0x0F0F0F0Fu) - 8.0) * xv[k];
    s += (bytes4((w >> 4u) & 0x0F0F0F0Fu) - 8.0) * xv[k + 4u];
  }
  return f16_at(block) * hsum(s);
}

fn vals4(block: u32, j: u32) -> vec4<f32> {
  let w = word_at(block + 2u + (j & 15u));
  let shift = select(0u, 4u, j >= 16u);
  return f16_at(block) * (bytes4((w >> shift) & 0x0F0F0F0Fu) - 8.0);
}

fn unit_prep(block: u32, sub: u32) -> vec4<f32> {
  return vec4<f32>(f16_at(block), 0.0, 0.0, 0.0);
}

fn unit_quad(block: u32, sub: u32, k: u32, prep: vec4<f32>) -> vec4<f32> {
  let w = word_at(block + 2u + 4u * (k & 3u));
  return prep.x * (bytes4((w >> select(0u, 4u, k >= 4u)) & 0x0F0F0F0Fu) - 8.0);
}

fn block_get(block: u32, j: u32) -> f32 {
  let q = byte_at(block + 2u + (j & 15u));
  let nibble = select(q & 15u, q >> 4u, j >= 16u);
  return f16_at(block) * (f32(nibble) - 8.0);
}

// Q5_0: 22 bytes per 32 values — f16 d, 4 bytes qh, 16 bytes of nibbles. As Q4_0
// with a fifth bit: bit j of qh is value j's (bit j + 16 is value j + 16's);
// x = d * (q - 16). llama.cpp's K-quant files use it for rows that are not a
// multiple of 256 values (SmolLM2's 960).
const UNIT: u32 = 32u;
const UNITS: u32 = 1u;
const BLOCK_BYTES: u32 = 22u;

// Bits 0–3 of `b` as the lowest bit of each of four bytes.
fn spread4(b: u32) -> u32 {
  return (b & 1u) | ((b & 2u) << 7u) | ((b & 4u) << 14u) | ((b & 8u) << 21u);
}

// Values 4k … 4k + 3 (k < 8), before the scale.
fn q50_quad(block: u32, k: u32, qh: u32) -> vec4<f32> {
  let w = word_at(block + 6u + 4u * (k & 3u));
  let low = (w >> select(0u, 4u, k >= 4u)) & 0x0F0F0F0Fu;
  let high = spread4(qh >> (4u * (k & 3u) + select(0u, 16u, k >= 4u)));
  return bytes4(low | (high << 4u)) - 16.0;
}

fn unit_dot(block: u32, sub: u32) -> f32 {
  let qh = word_at(block + 2u);
  var s = vec4<f32>(0.0);
  for (var k = 0u; k < 8u; k++) {
    s += q50_quad(block, k, qh) * xv[k];
  }
  return f16_at(block) * hsum(s);
}

fn unit_prep(block: u32, sub: u32) -> vec4<f32> {
  return vec4<f32>(f16_at(block), 0.0, 0.0, 0.0);
}

fn unit_quad(block: u32, sub: u32, k: u32, prep: vec4<f32>) -> vec4<f32> {
  return prep.x * q50_quad(block, k, word_at(block + 2u));
}

fn vals4(block: u32, j: u32) -> vec4<f32> {
  return f16_at(block) * q50_quad(block, j / 4u, word_at(block + 2u));
}

fn block_get(block: u32, j: u32) -> f32 {
  let q = byte_at(block + 6u + (j & 15u));
  let nibble = select(q & 15u, q >> 4u, j >= 16u);
  let high = (word_at(block + 2u) >> j) & 1u;
  return f16_at(block) * (f32(nibble | (high << 4u)) - 16.0);
}

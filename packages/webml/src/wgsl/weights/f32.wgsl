// F32 weights, as 32-value units of 128 bytes. Rows must be a multiple of 32
// elements.
const UNIT: u32 = 32u;
const UNITS: u32 = 1u;
const BLOCK_BYTES: u32 = 128u;

fn vals4(block: u32, j: u32) -> vec4<f32> {
  let w = (block >> 2u) + j;
  return bitcast<vec4<f32>>(vec4<u32>(W[w], W[w + 1u], W[w + 2u], W[w + 3u]));
}

fn unit_dot(block: u32, sub: u32) -> f32 {
  var s = vec4<f32>(0.0);
  for (var k = 0u; k < 8u; k++) {
    s += vals4(block, 4u * k) * xv[k];
  }
  return hsum(s);
}

fn unit_prep(block: u32, sub: u32) -> vec4<f32> {
  return vec4<f32>(0.0);
}

fn unit_quad(block: u32, sub: u32, k: u32, prep: vec4<f32>) -> vec4<f32> {
  return vals4(block, 4u * k);
}

fn block_get(block: u32, j: u32) -> f32 {
  return bitcast<f32>(W[(block >> 2u) + j]);
}

// F16 weights, as 32-value units of 64 bytes. Rows must be a multiple of 32
// elements.
const UNIT: u32 = 32u;
const UNITS: u32 = 1u;
const BLOCK_BYTES: u32 = 64u;

fn vals4(block: u32, j: u32) -> vec4<f32> {
  let w = (block + 2u * j) >> 2u;
  return vec4<f32>(unpack2x16float(W[w]), unpack2x16float(W[w + 1u]));
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
  return unpack2x16float(W[(block + 2u * j) >> 2u])[j & 1u];
}

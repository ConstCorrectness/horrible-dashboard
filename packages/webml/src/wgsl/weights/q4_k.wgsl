// Q4_K: 144 bytes per 256 values — f16 d, f16 dmin, 12 bytes of 6-bit scales and
// mins, 128 bytes of nibbles. Eight sub-blocks of 32 (the units here):
// x = d·sc·q − dmin·m. Sub-block s reads bytes 32·(s/2)… of qs, low nibbles for
// even s, high for odd.
const UNIT: u32 = 32u;
const UNITS: u32 = 8u;
const BLOCK_BYTES: u32 = 144u;

fn unit_dot(block: u32, sub: u32, x: u32) -> f32 {
  let sm = scale_min_k4(block + 4u, sub);
  let q = block + 16u + 32u * (sub >> 1u);
  let shift = (sub & 1u) * 4u;
  var sqx = 0.0;
  var sx = 0.0;
  for (var l = 0u; l < 32u; l++) {
    let xv = X[x + l];
    sqx += f32((byte_at(q + l) >> shift) & 15u) * xv;
    sx += xv;
  }
  return f16_at(block) * sm.x * sqx - f16_at(block + 2u) * sm.y * sx;
}

fn block_get(block: u32, j: u32) -> f32 {
  let sub = j / 32u;
  let l = j % 32u;
  let sm = scale_min_k4(block + 4u, sub);
  let v = f32((byte_at(block + 16u + 32u * (sub >> 1u) + l) >> ((sub & 1u) * 4u)) & 15u);
  return f16_at(block) * sm.x * v - f16_at(block + 2u) * sm.y;
}

// Q8_0: 34 bytes per 32 values — f16 scale d, then 32 signed bytes. x = d * q.
const BLOCK: u32 = 32u;
const BLOCK_BYTES: u32 = 34u;

fn block_dot(off: u32, x: u32) -> f32 {
  var sum = 0.0;
  for (var j = 0u; j < 32u; j++) {
    sum += i8_at(off + 2u + j) * X[x + j];
  }
  return f16_at(off) * sum;
}

fn block_get(off: u32, j: u32) -> f32 {
  return f16_at(off) * i8_at(off + 2u + j);
}

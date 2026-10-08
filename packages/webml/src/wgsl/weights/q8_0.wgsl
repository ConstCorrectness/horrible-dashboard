// Q8_0: 34 bytes per 32 values — f16 scale d, then 32 signed bytes. x = d * q.
const UNIT: u32 = 32u;
const UNITS: u32 = 1u;
const BLOCK_BYTES: u32 = 34u;

fn unit_dot(block: u32, sub: u32, x: u32) -> f32 {
  var sum = 0.0;
  for (var j = 0u; j < 32u; j++) {
    sum += i8_at(block + 2u + j) * X[x + j];
  }
  return f16_at(block) * sum;
}

fn block_get(block: u32, j: u32) -> f32 {
  return f16_at(block) * i8_at(block + 2u + j);
}

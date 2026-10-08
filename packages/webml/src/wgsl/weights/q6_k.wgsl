// Q6_K: 210 bytes per 256 values — ql[128] (low 4 bits), qh[64] (high 2 bits),
// 16 signed 8-bit scales, f16 d at the end. x = d·sc·(q − 32).
//
// Unit s (0–7) is values 32s…32s+31: half n = s/4 of the block, quarter qq = s%4.
// Quarter qq reads ql at +32·(qq&1), its high nibble for qq ≥ 2, qh bits 2·qq, and
// scale 2·qq (values 0–15) or 2·qq + 1 (values 16–31) of the half's eight.
const UNIT: u32 = 32u;
const UNITS: u32 = 8u;
const BLOCK_BYTES: u32 = 210u;

fn q6k_value(block: u32, sub: u32, l: u32) -> f32 {
  let n = sub >> 2u;
  let qq = sub & 3u;
  let low = (byte_at(block + 64u * n + 32u * (qq & 1u) + l) >> ((qq >> 1u) * 4u)) & 15u;
  let high = (byte_at(block + 128u + 32u * n + l) >> (2u * qq)) & 3u;
  return f32(i32(low | (high << 4u)) - 32);
}

fn q6k_scale(block: u32, sub: u32, l: u32) -> f32 {
  return i8_at(block + 192u + 8u * (sub >> 2u) + 2u * (sub & 3u) + (l >> 4u));
}

fn unit_dot(block: u32, sub: u32, x: u32) -> f32 {
  var lo = 0.0;
  var hi = 0.0;
  for (var l = 0u; l < 16u; l++) {
    lo += q6k_value(block, sub, l) * X[x + l];
    hi += q6k_value(block, sub, l + 16u) * X[x + l + 16u];
  }
  return f16_at(block + 208u) * (q6k_scale(block, sub, 0u) * lo + q6k_scale(block, sub, 16u) * hi);
}

fn block_get(block: u32, j: u32) -> f32 {
  let sub = j / 32u;
  let l = j % 32u;
  return f16_at(block + 208u) * q6k_scale(block, sub, l) * q6k_value(block, sub, l);
}

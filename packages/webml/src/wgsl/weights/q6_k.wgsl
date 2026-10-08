// Q6_K: 210 bytes per 256 values — ql[128] (low 4 bits), qh[64] (high 2 bits),
// 16 signed 8-bit scales, f16 d at the end. x = d·sc·(q − 32).
//
// Unit s (0–7) is values 32s…32s+31: half n = s/4 of the block, quarter qq = s%4.
// Quarter qq reads ql at +32·(qq&1), its high nibble for qq ≥ 2, qh bits 2·qq, and
// scale 2·qq (values 0–15) or 2·qq + 1 (values 16–31) of the half's eight.
const UNIT: u32 = 32u;
const UNITS: u32 = 8u;
const BLOCK_BYTES: u32 = 210u;

// Values 4k … 4k + 3 of unit `sub`, as q − 32, before scales.
fn q6k_quads(block: u32, sub: u32, k: u32) -> vec4<f32> {
  let n = sub >> 2u;
  let qq = sub & 3u;
  let low = (word_at(block + 64u * n + 32u * (qq & 1u) + 4u * k) >> ((qq >> 1u) * 4u)) & 0x0F0F0F0Fu;
  let high = (word_at(block + 128u + 32u * n + 4u * k) >> (2u * qq)) & 0x03030303u;
  return bytes4(low | (high << 4u)) - 32.0;
}

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

fn unit_dot(block: u32, sub: u32) -> f32 {
  var lo = vec4<f32>(0.0);
  var hi = vec4<f32>(0.0);
  for (var k = 0u; k < 4u; k++) {
    lo += q6k_quads(block, sub, k) * xv[k];
    hi += q6k_quads(block, sub, k + 4u) * xv[k + 4u];
  }
  return f16_at(block + 208u) * (q6k_scale(block, sub, 0u) * hsum(lo) + q6k_scale(block, sub, 16u) * hsum(hi));
}

fn vals4(block: u32, j: u32) -> vec4<f32> {
  let sub = j / 32u;
  let l = j % 32u;
  return f16_at(block + 208u) * q6k_scale(block, sub, l) * q6k_quads(block, sub, l / 4u);
}

// d·scale for the unit's first (x) and second (y) sixteen values.
fn unit_prep(block: u32, sub: u32) -> vec4<f32> {
  let d = f16_at(block + 208u);
  return vec4<f32>(d * q6k_scale(block, sub, 0u), d * q6k_scale(block, sub, 16u), 0.0, 0.0);
}

fn unit_quad(block: u32, sub: u32, k: u32, prep: vec4<f32>) -> vec4<f32> {
  return select(prep.x, prep.y, k >= 4u) * q6k_quads(block, sub, k);
}

fn block_get(block: u32, j: u32) -> f32 {
  let sub = j / 32u;
  let l = j % 32u;
  return f16_at(block + 208u) * q6k_scale(block, sub, l) * q6k_value(block, sub, l);
}

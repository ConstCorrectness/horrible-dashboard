// f32 → f16 rounding to nearest even, as llama.cpp converts into its f16 cache.
// Not pack2x16float: WGSL leaves its rounding to the implementation, and on an
// Intel iGPU (D3D12) it rounds toward zero — a one-ulp bias in every cached value.

fn f16_bits(x: f32) -> u32 {
  let u = bitcast<u32>(x);
  let sign = (u >> 16u) & 0x8000u;
  let biased = (u >> 23u) & 0xFFu;
  var mant = u & 0x7FFFFFu;
  if (biased == 0xFFu) {
    return sign | 0x7C00u | select(0u, 0x200u, mant != 0u);
  }
  let e = i32(biased) - 112;
  if (e >= 31) {
    return sign | 0x7C00u;
  }
  var shift = 13u;
  var base = 0u;
  if (e <= 0) {
    if (e < -10) {
      return sign;
    }
    mant |= 0x800000u;
    shift = u32(14 - e);
  } else {
    base = u32(e) << 10u;
  }
  var half = base | (mant >> shift);
  let rem = mant & ((1u << shift) - 1u);
  let mid = 1u << (shift - 1u);
  // A carry out of the mantissa correctly bumps the exponent.
  if (rem > mid || (rem == mid && (half & 1u) != 0u)) {
    half += 1u;
  }
  return sign | half;
}

fn f16_pair(v: vec2<f32>) -> u32 {
  return f16_bits(v.x) | (f16_bits(v.y) << 16u);
}

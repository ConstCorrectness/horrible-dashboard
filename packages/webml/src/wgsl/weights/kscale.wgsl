// The 6-bit scale (x) and min (y) of sub-block j (0–7) of a Q4_K / Q5_K
// super-block, packed into the 12 bytes at `at` (ggml's get_scale_min_k4).
fn scale_min_k4(at: u32, j: u32) -> vec2<f32> {
  if (j < 4u) {
    return vec2<f32>(f32(byte_at(at + j) & 63u), f32(byte_at(at + j + 4u) & 63u));
  }
  let sc = (byte_at(at + j + 4u) & 15u) | ((byte_at(at + j - 4u) >> 6u) << 4u);
  let m = (byte_at(at + j + 4u) >> 4u) | ((byte_at(at + j) >> 6u) << 4u);
  return vec2<f32>(f32(sc), f32(m));
}

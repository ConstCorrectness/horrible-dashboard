// Byte-level reads from the weight buffer `W` (declared by the kernel as
// array<u32>). ggml blocks are 18 or 34 bytes, so a block is not word-aligned and
// every field is read out of the words that hold it. Little-endian throughout.

fn byte_at(off: u32) -> u32 {
  return (W[off >> 2u] >> ((off & 3u) * 8u)) & 0xFFu;
}

fn i8_at(off: u32) -> f32 {
  return f32(bitcast<i32>(byte_at(off) << 24u) >> 24u);
}

// An f16 at an even byte offset.
fn f16_at(off: u32) -> f32 {
  let w = W[off >> 2u];
  let h = select(w & 0xFFFFu, w >> 16u, (off & 2u) != 0u);
  return unpack2x16float(h).x;
}

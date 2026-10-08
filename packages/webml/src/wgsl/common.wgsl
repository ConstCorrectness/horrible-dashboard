// Reads from the weight buffer `W` (declared by the kernel as array<u32>). ggml
// blocks are 18, 34 or 210 bytes, so a block is not always word-aligned — but
// every block size and every field offset inside one is even, so any field starts
// on a 2-byte boundary. Little-endian throughout.
//
// The hot paths read whole words (`word_at`) and unpack four values at a time;
// the byte reads are for scales and the odd single value (`block_get`).

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

// The four bytes at an even byte offset, as one word.
fn word_at(off: u32) -> u32 {
  let i = off >> 2u;
  if ((off & 2u) == 0u) {
    return W[i];
  }
  return (W[i] >> 16u) | (W[i + 1u] << 16u);
}

// A word's bytes as four unsigned values, lowest byte first.
fn bytes4(w: u32) -> vec4<f32> {
  return vec4<f32>(vec4<u32>(w & 0xFFu, (w >> 8u) & 0xFFu, (w >> 16u) & 0xFFu, w >> 24u));
}

// A word's bytes as four signed values, lowest byte first.
fn ibytes4(w: u32) -> vec4<f32> {
  return vec4<f32>(bitcast<vec4<i32>>(vec4<u32>(w << 24u, w << 16u, w << 8u, w)) >> vec4<u32>(24u));
}

fn hsum(v: vec4<f32>) -> f32 {
  return v.x + v.y + v.z + v.w;
}

// Q8_0: 34 bytes per 32 values — f16 scale d, then 32 signed bytes. x = d * q.
//
// Every snippet defines: UNIT (32: values per unit of work), UNITS (units per
// block), BLOCK_BYTES; unit_dot(block, sub), the dot of unit `sub` with the 32
// activations in the kernel's private `xv`; unit_prep(block, sub), the unit's
// scales decoded once, and unit_quad(block, sub, k, prep), its values 4k … 4k + 3;
// vals4(block, j), values j … j + 3 of the block (j a multiple of 4); and
// block_get(block, j), one value.
const UNIT: u32 = 32u;
const UNITS: u32 = 1u;
const BLOCK_BYTES: u32 = 34u;

fn unit_dot(block: u32, sub: u32) -> f32 {
  // The 32 quants span nine words, starting 0 or 2 bytes into the first: read
  // each word once and shift pairs together.
  let first = (block + 2u) >> 2u;
  let odd = ((block + 2u) & 2u) != 0u;
  var s = vec4<f32>(0.0);
  var lo = W[first];
  for (var j = 0u; j < 8u; j++) {
    let hi = W[first + j + 1u];
    s += ibytes4(select(lo, (lo >> 16u) | (hi << 16u), odd)) * xv[j];
    lo = hi;
  }
  return f16_at(block) * hsum(s);
}

fn vals4(block: u32, j: u32) -> vec4<f32> {
  return f16_at(block) * ibytes4(word_at(block + 2u + j));
}

// The unit's scale (x), decoded once for unit_quad.
fn unit_prep(block: u32, sub: u32) -> vec4<f32> {
  return vec4<f32>(f16_at(block), 0.0, 0.0, 0.0);
}

// Values 4k … 4k + 3 of unit `sub`, with its unit_prep.
fn unit_quad(block: u32, sub: u32, k: u32, prep: vec4<f32>) -> vec4<f32> {
  return prep.x * ibytes4(word_at(block + 2u + 4u * k));
}

fn block_get(block: u32, j: u32) -> f32 {
  return f16_at(block) * i8_at(block + 2u + j);
}

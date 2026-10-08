// Rotary position embedding, in place, for `heads` heads of `head_dim` starting at
// base + pos * pos_stride (pos_stride is 0 for Q, the cache row width for K).
//
// The angles come from a table built on the host the way ggml builds its rope
// cache (float32 theta, multiplied down dimension by dimension), so positions far
// into the context round the way llama.cpp's do. T[pos * rope_dims + 2i] is
// cos, T[... + 2i + 1] is sin, for pair i.
//
// `neox` = 0: llama.cpp's "normal" rope, pairs (2i, 2i + 1).
// `neox` = 1: NEOX rope, pairs (i, i + rope_dims / 2).

const WG: u32 = 64u;

struct Params {
  heads: u32,
  head_dim: u32,
  rope_dims: u32,
  base: u32,
  pos_stride: u32,
  neox: u32,
}

struct Step {
  pos: u32,
  tok_row: u32,
}

@group(0) @binding(0) var<storage, read_write> V: array<f32>;
@group(0) @binding(1) var<storage, read> T: array<f32>;
@group(0) @binding(2) var<uniform> P: Params;
@group(0) @binding(3) var<uniform> S: Step;

@compute @workgroup_size(WG)
fn main(@builtin(global_invocation_id) gid: vec3<u32>) {
  let pairs = P.rope_dims / 2u;
  let k = gid.x;
  if (k >= P.heads * pairs) {
    return;
  }
  let head = k / pairs;
  let i = k % pairs;
  let start = P.base + S.pos * P.pos_stride + head * P.head_dim;
  let a = start + select(2u * i, i, P.neox != 0u);
  let b = a + select(1u, pairs, P.neox != 0u);
  let c = T[S.pos * P.rope_dims + 2u * i];
  let s = T[S.pos * P.rope_dims + 2u * i + 1u];
  let x0 = V[a];
  let x1 = V[b];
  V[a] = x0 * c - x1 * s;
  V[b] = x0 * s + x1 * c;
}

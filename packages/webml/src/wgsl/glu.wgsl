// The gated feed-forward's activation, elementwise over the batch's S.n rows of
// `n`: Y = silu(G) * U, or with `gelu` set, Y = gelu(G) * U (Gemma), GELU's tanh
// approximation as ggml's.

const WG: u32 = 256u;

struct Params {
  n: u32,
  gelu: u32,
}

struct Step {
  pos: u32,
  n: u32,
}

@group(0) @binding(0) var<storage, read> G: array<f32>;
@group(0) @binding(1) var<storage, read> U: array<f32>;
@group(0) @binding(2) var<storage, read_write> Y: array<f32>;
@group(0) @binding(3) var<uniform> P: Params;
@group(0) @binding(4) var<uniform> S: Step;

// √(2/π)
const GELU_K: f32 = 0.7978845608028654;

fn gelu(x: f32) -> f32 {
  // tanh's argument is clamped where tanh is already ±1 in f32, so no
  // implementation's exp overflows into inf / inf.
  let inner = clamp(GELU_K * x * (1.0 + 0.044715 * x * x), -15.0, 15.0);
  return 0.5 * x * (1.0 + tanh(inner));
}

@compute @workgroup_size(WG)
fn main(
  @builtin(global_invocation_id) gid: vec3<u32>,
  @builtin(num_workgroups) groups: vec3<u32>,
) {
  let i = gid.x + gid.y * groups.x * WG;
  if (i < S.n * P.n) {
    let g = G[i];
    var a: f32;
    if (P.gelu != 0u) {
      a = gelu(g);
    } else {
      a = g / (1.0 + exp(-g));
    }
    Y[i] = a * U[i];
  }
}

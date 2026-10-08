// Y = silu(G) * U, elementwise: the gated feed-forward's activation.

const WG: u32 = 256u;

struct Params {
  n: u32,
}

@group(0) @binding(0) var<storage, read> G: array<f32>;
@group(0) @binding(1) var<storage, read> U: array<f32>;
@group(0) @binding(2) var<storage, read_write> Y: array<f32>;
@group(0) @binding(3) var<uniform> P: Params;

@compute @workgroup_size(WG)
fn main(@builtin(global_invocation_id) gid: vec3<u32>) {
  let i = gid.x;
  if (i < P.n) {
    let g = G[i];
    Y[i] = g / (1.0 + exp(-g)) * U[i];
  }
}

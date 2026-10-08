// Per-head RMS norm, in place: each of `heads` vectors of `head_dim`, starting at
// base + pos * pos_stride, becomes v / sqrt(mean(v²) + eps) * G, with one weight
// vector G shared by every head. Qwen3 applies it to Q and K before rope.
// One workgroup per head.

const WG: u32 = 64u;

struct Params {
  head_dim: u32,
  eps: f32,
  base: u32,
  pos_stride: u32,
}

struct Step {
  pos: u32,
  tok_row: u32,
}

@group(0) @binding(0) var<storage, read_write> V: array<f32>;
@group(0) @binding(1) var<storage, read> G: array<f32>;
@group(0) @binding(2) var<uniform> P: Params;
@group(0) @binding(3) var<uniform> S: Step;

var<workgroup> partial: array<f32, WG>;

@compute @workgroup_size(WG)
fn main(@builtin(workgroup_id) wid: vec3<u32>, @builtin(local_invocation_index) tid: u32) {
  let start = P.base + S.pos * P.pos_stride + wid.x * P.head_dim;
  var acc = 0.0;
  for (var i = tid; i < P.head_dim; i += WG) {
    let v = V[start + i];
    acc += v * v;
  }
  partial[tid] = acc;
  workgroupBarrier();
  for (var s = WG / 2u; s > 0u; s >>= 1u) {
    if (tid < s) {
      partial[tid] += partial[tid + s];
    }
    workgroupBarrier();
  }
  let scale = 1.0 / sqrt(partial[0] / f32(P.head_dim) + P.eps);
  for (var i = tid; i < P.head_dim; i += WG) {
    V[start + i] = V[start + i] * scale * G[i];
  }
}

// Y = X / sqrt(mean(X²) + eps) * G over one row of `n`, in one workgroup.

const WG: u32 = 256u;

struct Params {
  n: u32,
  eps: f32,
}

@group(0) @binding(0) var<storage, read> X: array<f32>;
@group(0) @binding(1) var<storage, read> G: array<f32>;
@group(0) @binding(2) var<storage, read_write> Y: array<f32>;
@group(0) @binding(3) var<uniform> P: Params;

var<workgroup> partial: array<f32, WG>;

@compute @workgroup_size(WG)
fn main(@builtin(local_invocation_index) tid: u32) {
  var acc = 0.0;
  for (var i = tid; i < P.n; i += WG) {
    acc += X[i] * X[i];
  }
  partial[tid] = acc;
  workgroupBarrier();
  for (var s = WG / 2u; s > 0u; s >>= 1u) {
    if (tid < s) {
      partial[tid] += partial[tid + s];
    }
    workgroupBarrier();
  }
  let scale = 1.0 / sqrt(partial[0] / f32(P.n) + P.eps);
  for (var i = tid; i < P.n; i += WG) {
    Y[i] = X[i] * scale * G[i];
  }
}

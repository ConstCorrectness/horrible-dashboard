// Y = X / sqrt(mean(X²) + eps) * G over rows of `n`: one workgroup per row of the
// batch (S.n rows). With `last` set, only the batch's last row is normalized, into
// row 0 of Y: the input to the LM head, which runs on the last position only.

const WG: u32 = 256u;

struct Params {
  n: u32,
  eps: f32,
  last: u32,
}

struct Step {
  pos: u32,
  n: u32,
}

@group(0) @binding(0) var<storage, read> X: array<f32>;
@group(0) @binding(1) var<storage, read> G: array<f32>;
@group(0) @binding(2) var<storage, read_write> Y: array<f32>;
@group(0) @binding(3) var<uniform> P: Params;
@group(0) @binding(4) var<uniform> S: Step;

var<workgroup> partial: array<f32, WG>;

@compute @workgroup_size(WG)
fn main(@builtin(workgroup_id) wid: vec3<u32>, @builtin(local_invocation_index) tid: u32) {
  if (wid.x >= select(S.n, 1u, P.last != 0u)) {
    return;
  }
  let src = select(wid.x, S.n - 1u, P.last != 0u) * P.n;
  let dst = select(wid.x, 0u, P.last != 0u) * P.n;
  var acc = 0.0;
  for (var i = tid; i < P.n; i += WG) {
    acc += X[src + i] * X[src + i];
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
    Y[dst + i] = X[src + i] * scale * G[i];
  }
}

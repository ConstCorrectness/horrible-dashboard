// The logit lens's input at one layer: the residual stream of the pass's last row
// (the position whose next token is being chosen), normed with the model's final
// norm into row `layer` of L, ready for the LM head. Its L2 norm goes into the
// layer's readout. One workgroup; dispatched after each layer's last residual add,
// before the next layer overwrites the stream.
//
// A readout is `stride` words per layer (lens_topk.wgsl writes the rest):
// [0] the residual's L2 norm, [1] the lens distribution's entropy in bits, then
// (id, p) for its top tokens. Floats are stored as their bits.

const WG: u32 = 256u;

struct Params {
  n: u32,
  eps: f32,
  layer: u32,
  stride: u32,
}

struct Step {
  pos: u32,
  n: u32,
}

@group(0) @binding(0) var<storage, read> X: array<f32>;
@group(0) @binding(1) var<storage, read> G: array<f32>;
@group(0) @binding(2) var<storage, read_write> L: array<f32>;
@group(0) @binding(3) var<storage, read_write> R: array<u32>;
@group(0) @binding(4) var<uniform> P: Params;
@group(0) @binding(5) var<uniform> S: Step;

var<workgroup> partial: array<f32, WG>;

@compute @workgroup_size(WG)
fn main(@builtin(local_invocation_index) tid: u32) {
  let src = (S.n - 1u) * P.n;
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
  let ss = partial[0];
  let scale = 1.0 / sqrt(ss / f32(P.n) + P.eps);
  for (var i = tid; i < P.n; i += WG) {
    L[P.layer * P.n + i] = X[src + i] * scale * G[i];
  }
  if (tid == 0u) {
    R[P.layer * P.stride] = bitcast<u32>(sqrt(ss));
  }
}

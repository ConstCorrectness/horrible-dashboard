// The logit lens's readout: for each layer's row of lens logits (the LM head over
// lens_in.wgsl's normed residuals), the softmax's entropy and its K most likely
// tokens with their probabilities. One workgroup per layer; the row is read K + 2
// times (max, sums, then one arg-max per pick), and only the readout leaves the GPU.
//
// Readout layout (words, `stride` per layer, after lens_in's norm at [0]):
// [1] entropy in bits, then (id, p) pairs from [2]. Ties go to the lower id.

const WG: u32 = 256u;
const K: u32 = 5u;
const LN2: f32 = 0.6931471805599453;

struct Params {
  vocab: u32,
  stride: u32,
}

@group(0) @binding(0) var<storage, read> Z: array<f32>;
@group(0) @binding(1) var<storage, read_write> R: array<u32>;
@group(0) @binding(2) var<uniform> P: Params;

var<workgroup> rv: array<f32, WG>;
var<workgroup> ri: array<u32, WG>;
var<workgroup> chosen: array<u32, K>;

fn reduce_sum(tid: u32) {
  for (var s = WG / 2u; s > 0u; s >>= 1u) {
    if (tid < s) {
      rv[tid] += rv[tid + s];
    }
    workgroupBarrier();
  }
}

@compute @workgroup_size(WG)
fn main(@builtin(workgroup_id) wid: vec3<u32>, @builtin(local_invocation_index) tid: u32) {
  let row = wid.x;
  let base = row * P.vocab;
  let dst = row * P.stride;

  var m = -3.4e38;
  for (var i = tid; i < P.vocab; i += WG) {
    m = max(m, Z[base + i]);
  }
  rv[tid] = m;
  workgroupBarrier();
  for (var s = WG / 2u; s > 0u; s >>= 1u) {
    if (tid < s) {
      rv[tid] = max(rv[tid], rv[tid + s]);
    }
    workgroupBarrier();
  }
  let mx = rv[0];
  workgroupBarrier();

  // Z = Σ e^(x − mx) and Σ e^(x − mx)(x − mx): entropy = ln Z − that / Z.
  var z = 0.0;
  var zd = 0.0;
  for (var i = tid; i < P.vocab; i += WG) {
    let d = Z[base + i] - mx;
    let e = exp(d);
    z += e;
    zd += e * d;
  }
  rv[tid] = z;
  workgroupBarrier();
  reduce_sum(tid);
  let total = rv[0];
  workgroupBarrier();
  rv[tid] = zd;
  workgroupBarrier();
  reduce_sum(tid);
  if (tid == 0u) {
    R[dst + 1u] = bitcast<u32>((log(total) - rv[0] / total) / LN2);
  }
  workgroupBarrier();

  for (var k = 0u; k < K; k++) {
    var best = -3.4e38;
    var bi = 0xffffffffu;
    for (var i = tid; i < P.vocab; i += WG) {
      let v = Z[base + i];
      if (v > best) {
        var taken = false;
        for (var j = 0u; j < k; j++) {
          taken = taken || chosen[j] == i;
        }
        if (!taken) {
          best = v;
          bi = i;
        }
      }
    }
    rv[tid] = best;
    ri[tid] = bi;
    workgroupBarrier();
    for (var s = WG / 2u; s > 0u; s >>= 1u) {
      if (tid < s) {
        let o = tid + s;
        if (rv[o] > rv[tid] || (rv[o] == rv[tid] && ri[o] < ri[tid])) {
          rv[tid] = rv[o];
          ri[tid] = ri[o];
        }
      }
      workgroupBarrier();
    }
    if (tid == 0u) {
      chosen[k] = ri[0];
      R[dst + 2u + 2u * k] = ri[0];
      R[dst + 3u + 2u * k] = bitcast<u32>(exp(rv[0] - mx) / total);
    }
    workgroupBarrier();
  }
}

// Decode attention: one workgroup per query head over positions 0..=pos.
// Scores go to a scratch row per head, then softmax, then the weighted sum of V.
// Grouped-query attention: query head h reads KV head h / group.
//
// K and V caches are [ctx][kv_heads * head_dim]; Q and O are [heads * head_dim].

const WG: u32 = 256u;

struct Params {
  heads: u32,
  head_dim: u32,
  kv_dim: u32,
  group: u32,
  ctx: u32,
  scale: f32,
}

struct Step {
  pos: u32,
  tok_row: u32,
}

@group(0) @binding(0) var<storage, read> Q: array<f32>;
@group(0) @binding(1) var<storage, read> K: array<f32>;
@group(0) @binding(2) var<storage, read> Vc: array<f32>;
@group(0) @binding(3) var<storage, read_write> O: array<f32>;
@group(0) @binding(4) var<storage, read_write> SC: array<f32>;
@group(0) @binding(5) var<uniform> P: Params;
@group(0) @binding(6) var<uniform> S: Step;

var<workgroup> red: array<f32, WG>;

fn reduce_max(tid: u32, v: f32) -> f32 {
  red[tid] = v;
  workgroupBarrier();
  for (var s = WG / 2u; s > 0u; s >>= 1u) {
    if (tid < s) {
      red[tid] = max(red[tid], red[tid + s]);
    }
    workgroupBarrier();
  }
  let out = red[0];
  workgroupBarrier();
  return out;
}

fn reduce_sum(tid: u32, v: f32) -> f32 {
  red[tid] = v;
  workgroupBarrier();
  for (var s = WG / 2u; s > 0u; s >>= 1u) {
    if (tid < s) {
      red[tid] += red[tid + s];
    }
    workgroupBarrier();
  }
  let out = red[0];
  workgroupBarrier();
  return out;
}

@compute @workgroup_size(WG)
fn main(@builtin(workgroup_id) wid: vec3<u32>, @builtin(local_invocation_index) tid: u32) {
  let h = wid.x;
  let n = S.pos + 1u;
  let q = h * P.head_dim;
  let kv = (h / P.group) * P.head_dim;
  let row = h * P.ctx;

  var local_max = -3.4e38;
  for (var t = tid; t < n; t += WG) {
    var d = 0.0;
    for (var j = 0u; j < P.head_dim; j++) {
      d += Q[q + j] * K[t * P.kv_dim + kv + j];
    }
    let s = d * P.scale;
    SC[row + t] = s;
    local_max = max(local_max, s);
  }
  let m = reduce_max(tid, local_max);

  var local_sum = 0.0;
  for (var t = tid; t < n; t += WG) {
    let e = exp(SC[row + t] - m);
    SC[row + t] = e;
    local_sum += e;
  }
  let total = reduce_sum(tid, local_sum);
  // The exponentials were written by other threads of this workgroup.
  storageBarrier();

  for (var j = tid; j < P.head_dim; j += WG) {
    var acc = 0.0;
    for (var t = 0u; t < n; t++) {
      acc += SC[row + t] * Vc[t * P.kv_dim + kv + j];
    }
    O[q + j] = acc / total;
  }
}

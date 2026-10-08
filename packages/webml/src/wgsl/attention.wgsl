// Decode attention, split over positions (flash-decoding), first pass: one
// workgroup per (KV head, chunk of CH positions) of 0..=pos, serving the GROUP
// query heads that read that KV head (grouped-query attention: query head h reads
// KV head h / GROUP). Each writes, per query head, the chunk's max m, its sum of
// exp(score − m), and the unnormalized Σ exp(score − m)·V into that head's slot of
// the partials buffer; attn_combine.wgsl merges the chunks. Splitting keeps a long
// context parallel; serving the whole group reads each K and V row once.
//
// Scores: KL lanes share a key, each reading every KL-th 16-byte piece of its K
// row (KPV values: 8 halves or 4 floats), so a key's row is read in contiguous
// pieces; KPP keys are scored per pass, one head of the group at a time.
// Weighted sums: each thread owns a pair of dimensions (one f16 word) of every
// head, and the workgroup's threads beyond one per pair take every PHASES-th key;
// the phases meet in workgroup memory.
//
// kernels.ts prepends HD (head size), GROUP and the cache's views for this kernel
// (KP, kp_dot, VW, v_pair — f16 or f32), so every array here is sized exactly and
// indexed by constant loops. K and V caches are [ctx][kv_dim]; Q is [heads * HD].
// A partials slot is HD + 2 floats: the weighted sum, then m, then the sum.

const WG: u32 = 128u;
const CH: u32 = 128u;
const KL: u32 = 4u;
const KPP: u32 = WG / KL;
const HD4: u32 = HD / 4u;
const HD2: u32 = HD / 2u;
const HDP: u32 = HD / KPV;
const PHASES: u32 = WG / HD2;
const MASKED: f32 = -3.4e38;

struct Params {
  kv_dim: u32,
  splits: u32,
  scale: f32,
}

struct Step {
  pos: u32,
  n: u32,
}

@group(0) @binding(0) var<storage, read> Q: array<vec4<f32>>;
@group(0) @binding(1) var<storage, read> K: array<KP>;
@group(0) @binding(2) var<storage, read> Vc: array<VW>;
@group(0) @binding(3) var<storage, read_write> PART: array<f32>;
@group(0) @binding(4) var<uniform> P: Params;
@group(0) @binding(5) var<uniform> S: Step;

var<workgroup> qs: array<vec4<f32>, GROUP * HD4>;
var<workgroup> sc: array<f32, GROUP * CH>;
var<workgroup> red: array<f32, WG>;
var<workgroup> stats: array<f32, GROUP * 2u>;
var<workgroup> vred: array<vec2<f32>, PHASES * GROUP * HD2>;

@compute @workgroup_size(WG)
fn main(@builtin(workgroup_id) wid: vec3<u32>, @builtin(local_invocation_index) tid: u32) {
  let kvh = wid.x;
  let t0 = wid.y * CH;
  let total = S.pos + 1u;
  if (t0 >= total) {
    return;
  }
  let count = min(CH, total - t0);

  // The group's query vectors.
  for (var e = tid; e < GROUP * HD4; e += WG) {
    qs[e] = Q[kvh * GROUP * HD4 + e];
  }
  workgroupBarrier();

  // Scores for every (head of the group, key of the chunk).
  let key = tid / KL;
  let lane = tid % KL;
  for (var k0 = 0u; k0 < CH; k0 += KPP) {
    let t = k0 + key;
    let row = (t0 + t) * (P.kv_dim / KPV) + kvh * HDP;
    for (var h = 0u; h < GROUP; h++) {
      // The group's heads re-read the same K slice, from cache.
      var d = 0.0;
      if (t < count) {
        for (var i = lane; i < HDP; i += KL) {
          d += kp_dot(h * HD4 + i * (KPV / 4u), K[row + i]);
        }
      }
      red[tid] = d;
      workgroupBarrier();
      if (lane == 0u) {
        var s = MASKED;
        if (t < count) {
          s = (red[tid] + red[tid + 1u] + red[tid + 2u] + red[tid + 3u]) * P.scale;
        }
        sc[h * CH + t] = s;
      }
      workgroupBarrier();
    }
  }

  // Per head: the chunk's max and sum of exponentials.
  for (var h = 0u; h < GROUP; h++) {
    red[tid] = sc[h * CH + tid];
    workgroupBarrier();
    for (var s = WG / 2u; s > 0u; s >>= 1u) {
      if (tid < s) {
        red[tid] = max(red[tid], red[tid + s]);
      }
      workgroupBarrier();
    }
    let m = red[0];
    workgroupBarrier();
    var p = 0.0;
    if (tid < count) {
      p = exp(sc[h * CH + tid] - m);
    }
    sc[h * CH + tid] = p;
    red[tid] = p;
    workgroupBarrier();
    for (var s = WG / 2u; s > 0u; s >>= 1u) {
      if (tid < s) {
        red[tid] += red[tid + s];
      }
      workgroupBarrier();
    }
    if (tid == 0u) {
      stats[2u * h] = m;
      stats[2u * h + 1u] = red[0];
    }
    workgroupBarrier();
  }

  // Weighted sums of V: a dimension pair per thread, keys split into phases.
  let pair = tid % HD2;
  let phase = tid / HD2;
  var acc: array<vec2<f32>, GROUP>;
  if (phase < PHASES) {
    for (var i = phase; i < count; i += PHASES) {
      let v = v_pair(Vc[(t0 + i) * (P.kv_dim / 2u) + kvh * HD2 + pair]);
      for (var h = 0u; h < GROUP; h++) {
        acc[h] += sc[h * CH + i] * v;
      }
    }
    for (var h = 0u; h < GROUP; h++) {
      vred[(phase * GROUP + h) * HD2 + pair] = acc[h];
    }
  }
  workgroupBarrier();
  for (var e = tid; e < GROUP * HD2; e += WG) {
    var sum = vec2<f32>(0.0);
    for (var ph = 0u; ph < PHASES; ph++) {
      sum += vred[ph * GROUP * HD2 + e];
    }
    let h = e / HD2;
    let slot = ((kvh * GROUP + h) * P.splits + wid.y) * (HD + 2u);
    PART[slot + 2u * (e % HD2)] = sum.x;
    PART[slot + 2u * (e % HD2) + 1u] = sum.y;
  }
  if (tid < GROUP) {
    let slot = ((kvh * GROUP + tid) * P.splits + wid.y) * (HD + 2u);
    PART[slot + HD] = stats[2u * tid];
    PART[slot + HD + 1u] = stats[2u * tid + 1u];
  }
}

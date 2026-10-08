// Causal attention for a batch of `S.n` queries at positions pos … pos + n − 1,
// over the KV cache rows 0 … pos + n − 1: flash attention, so no score matrix is
// ever stored. A workgroup takes TQ queries of one head and walks the keys KB at
// a time, keeping each query's running max and sum (online softmax).
//
// LANES threads share a query, and each owns every LANES-th vec4 of it: its slice
// of Q and of the output stay in registers, and it reads the same slice of each
// K and V row straight from memory — the rows every query tile of the head reads,
// so they come from cache. Workgroup memory holds only the partial dot products
// of one key block, which keeps it small enough for many workgroups to share a
// GPU core (staging K and V tiles there left an iGPU nearly idle).
//
// kernels.ts prepends HD (head size), LANES (8, or 4 for heads of 16 or 48) and
// the cache type (kv_f16.wgsl or kv_f32.wgsl). Q and O are [n][q_stride]
// (q_stride = heads × HD); K and V are the caches, [ctx][kv_dim]. Query head h
// reads KV head h / group.

const WG: u32 = 64u;
const TQ: u32 = WG / LANES;
const VPL: u32 = HD / (4u * LANES);
const KB: u32 = 16u;
const MASKED: f32 = -3.4e38;

struct Params {
  q_stride: u32,
  kv_dim: u32,
  group: u32,
  scale: f32,
}

struct Step {
  pos: u32,
  n: u32,
}

@group(0) @binding(0) var<storage, read> Q: array<vec4<f32>>;
@group(0) @binding(1) var<storage, read> K: array<KV4>;
@group(0) @binding(2) var<storage, read> V: array<KV4>;
@group(0) @binding(3) var<storage, read_write> O: array<vec4<f32>>;
@group(0) @binding(4) var<uniform> P: Params;
@group(0) @binding(5) var<uniform> S: Step;

var<workgroup> part: array<f32, TQ * KB * LANES>;

@compute @workgroup_size(WG)
fn main(@builtin(workgroup_id) wid: vec3<u32>, @builtin(local_invocation_index) tid: u32) {
  let h = wid.x;
  let q0 = wid.y * TQ;
  if (q0 >= S.n) {
    return;
  }
  let qi = tid / LANES;
  let lane = tid % LANES;
  let r = q0 + qi;
  let hd4 = HD / 4u;
  let kv4w = P.kv_dim / 4u;
  let kv = (h / P.group) * hd4 + lane;

  var q: array<vec4<f32>, VPL>;
  if (r < S.n) {
    for (var i = 0u; i < VPL; i++) {
      q[i] = Q[r * (P.q_stride / 4u) + h * hd4 + i * LANES + lane];
    }
  }

  // This query's position, and the end of the keys any query of the tile sees.
  let my_pos = S.pos + r;
  let kv_end = S.pos + min(q0 + TQ, S.n);

  var m = MASKED;
  var l = 0.0;
  var acc: array<vec4<f32>, VPL>;
  for (var t0 = 0u; t0 < kv_end; t0 += KB) {
    for (var kk = 0u; kk < KB; kk++) {
      let t = t0 + kk;
      var d = 0.0;
      if (t < kv_end) {
        for (var i = 0u; i < VPL; i++) {
          d += dot(q[i], kv4(K[t * kv4w + kv + i * LANES]));
        }
      }
      part[(qi * KB + kk) * LANES + lane] = d;
    }
    workgroupBarrier();

    // Every lane of a query sums the same partials, so they agree on m and l.
    var s: array<f32, KB>;
    var tmax = m;
    for (var kk = 0u; kk < KB; kk++) {
      let t = t0 + kk;
      var x = MASKED;
      if (t <= my_pos && t < kv_end) {
        var d = 0.0;
        for (var j = 0u; j < LANES; j++) {
          d += part[(qi * KB + kk) * LANES + j];
        }
        x = d * P.scale;
      }
      s[kk] = x;
      tmax = max(tmax, x);
    }
    let corr = exp(m - tmax);
    l *= corr;
    for (var i = 0u; i < VPL; i++) {
      acc[i] *= corr;
    }
    for (var kk = 0u; kk < KB; kk++) {
      if (s[kk] > MASKED) {
        let p = exp(s[kk] - tmax);
        l += p;
        let t = t0 + kk;
        for (var i = 0u; i < VPL; i++) {
          acc[i] += p * kv4(V[t * kv4w + kv + i * LANES]);
        }
      }
    }
    m = tmax;
    // Everyone has read `part` before the next block overwrites it.
    workgroupBarrier();
  }

  if (r < S.n) {
    for (var i = 0u; i < VPL; i++) {
      O[r * (P.q_stride / 4u) + h * hd4 + i * LANES + lane] = acc[i] / l;
    }
  }
}

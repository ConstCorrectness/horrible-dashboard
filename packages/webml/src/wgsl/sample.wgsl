// Picking the next token on the GPU, as gguf/sample.ts does on the CPU, so a
// decode step reads back one small record instead of a vocabulary of logits —
// and writes the chosen id straight into the token buffer the next step's embed
// reads, so that step can be queued before the host has seen this one.
//
// One workgroup:
//   1. max (and argmax, lowest id on ties) and min of the logits;
//   2. greedy with alternatives: the full softmax's normalizer Z and Σ e·(l − max),
//      which give each alternative's probability and the entropy;
//   3. the K best candidates, exactly: a threshold t with K ≤ #(l ≥ t) ≤ CAP, found
//      by bisection between min and max (one counting pass per step), then every
//      logit ≥ t collected and sorted (bitonic, best first, lower id on ties);
//   4. one thread runs the chain on the sorted candidates: top-k, top-p and min-p
//      on their softmax, temperature, a draw with the host's random number.
//
// Record R: [id, p(id), entropy (bits), n, then n × (id, p)] — the distribution
// the token was drawn from, as protocol.ts's `step` events promise: the full
// softmax for greedy, the surviving candidates' tempered softmax otherwise. Only
// candidates with p > 0 are listed.

const WG: u32 = 256u;
const CAP: u32 = 1024u;
const MAXK: u32 = 64u;
const LN2: f32 = 0.6931471805599453;
const LOWEST: f32 = -3.4e38;

struct Params {
  vocab: u32,
  top_k: u32,
  record: u32,
  greedy: u32,
  temperature: f32,
  top_p: f32,
  min_p: f32,
  random: f32,
}

@group(0) @binding(0) var<storage, read> L: array<f32>;
@group(0) @binding(1) var<storage, read_write> T: array<u32>;
@group(0) @binding(2) var<storage, read_write> R: array<u32>;
@group(0) @binding(3) var<uniform> P: Params;

var<workgroup> rv: array<f32, WG>;
var<workgroup> ri: array<u32, WG>;
var<workgroup> rc: array<u32, WG>;
var<workgroup> shared_count: u32;
var<workgroup> shared_value: f32;
var<workgroup> collected: atomic<u32>;
var<workgroup> cv: array<f32, CAP>;
var<workgroup> ci: array<u32, CAP>;

fn better(av: f32, ai: u32, bv: f32, bi: u32) -> bool {
  return av > bv || (av == bv && ai < bi);
}

fn reduce_sum(tid: u32, v: f32) -> f32 {
  rv[tid] = v;
  workgroupBarrier();
  for (var s = WG / 2u; s > 0u; s >>= 1u) {
    if (tid < s) {
      rv[tid] += rv[tid + s];
    }
    workgroupBarrier();
  }
  let out = rv[0];
  workgroupBarrier();
  return out;
}

/** rv[0] as a value every thread may branch on. */
fn uniform_rv0(tid: u32) -> f32 {
  if (tid == 0u) {
    shared_value = rv[0];
  }
  return workgroupUniformLoad(&shared_value);
}

/** How many logits are ≥ t, as a value every thread may branch on. */
fn count_at_least(tid: u32, t: f32) -> u32 {
  var c = 0u;
  for (var i = tid; i < P.vocab; i += WG) {
    if (L[i] >= t) {
      c++;
    }
  }
  rc[tid] = c;
  workgroupBarrier();
  for (var s = WG / 2u; s > 0u; s >>= 1u) {
    if (tid < s) {
      rc[tid] += rc[tid + s];
    }
    workgroupBarrier();
  }
  if (tid == 0u) {
    shared_count = rc[0];
  }
  return workgroupUniformLoad(&shared_count);
}

@compute @workgroup_size(WG)
fn main(@builtin(local_invocation_index) tid: u32) {
  // 1. Max, argmax and min.
  var bv = LOWEST;
  var bi = 0xFFFFFFFFu;
  var lo = 3.4e38;
  for (var i = tid; i < P.vocab; i += WG) {
    let v = L[i];
    if (better(v, i, bv, bi)) {
      bv = v;
      bi = i;
    }
    lo = min(lo, v);
  }
  rv[tid] = bv;
  ri[tid] = bi;
  workgroupBarrier();
  for (var s = WG / 2u; s > 0u; s >>= 1u) {
    if (tid < s && better(rv[tid + s], ri[tid + s], rv[tid], ri[tid])) {
      rv[tid] = rv[tid + s];
      ri[tid] = ri[tid + s];
    }
    workgroupBarrier();
  }
  let argmax = ri[0];
  let m = uniform_rv0(tid);
  rv[tid] = lo;
  workgroupBarrier();
  for (var s = WG / 2u; s > 0u; s >>= 1u) {
    if (tid < s) {
      rv[tid] = min(rv[tid], rv[tid + s]);
    }
    workgroupBarrier();
  }
  let vmin = uniform_rv0(tid);

  // 2. Greedy's distribution is the full softmax.
  var z = 1.0;
  var ez = 0.0;
  if (P.greedy != 0u && P.record > 0u) {
    var zl = 0.0;
    var el = 0.0;
    for (var i = tid; i < P.vocab; i += WG) {
      let d = L[i] - m;
      let x = exp(d);
      zl += x;
      el += x * d;
    }
    z = reduce_sum(tid, zl);
    ez = reduce_sum(tid, el);
  }

  // 3. The best k candidates, sorted.
  let k = select(min(P.top_k, MAXK), min(P.record, MAXK), P.greedy != 0u);
  var n = 0u;
  if (k > 0u) {
    var lo_t = vmin;
    var hi_t = m;
    var t = max(vmin, m - 3.0);
    for (var step = 0u; step < 40u; step++) {
      let c = count_at_least(tid, t);
      if (c > CAP) {
        lo_t = t;
      } else if (c < k && t > vmin) {
        hi_t = t;
      } else {
        break;
      }
      t = 0.5 * (lo_t + hi_t);
    }

    if (tid == 0u) {
      atomicStore(&collected, 0u);
    }
    for (var j = tid; j < CAP; j += WG) {
      cv[j] = LOWEST;
      ci[j] = 0xFFFFFFFFu;
    }
    workgroupBarrier();
    for (var i = tid; i < P.vocab; i += WG) {
      let v = L[i];
      if (v >= t) {
        let at = atomicAdd(&collected, 1u);
        if (at < CAP) {
          cv[at] = v;
          ci[at] = i;
        }
      }
    }
    workgroupBarrier();

    // Bitonic sort, best first.
    for (var size = 2u; size <= CAP; size <<= 1u) {
      for (var stride = size / 2u; stride > 0u; stride >>= 1u) {
        for (var j = tid; j < CAP / 2u; j += WG) {
          let a = (j / stride) * stride * 2u + j % stride;
          let b = a + stride;
          let first = (a & size) == 0u;
          if (better(cv[b], ci[b], cv[a], ci[a]) == first) {
            let tv = cv[a];
            let ti = ci[a];
            cv[a] = cv[b];
            ci[a] = ci[b];
            cv[b] = tv;
            ci[b] = ti;
          }
        }
        workgroupBarrier();
      }
    }
    if (tid == 0u) {
      shared_count = min(atomicLoad(&collected), CAP);
    }
    n = workgroupUniformLoad(&shared_count);
  }

  // 4. The chain, on one thread.
  if (tid != 0u) {
    return;
  }
  var id = argmax;
  var chosen_p = 0.0;
  var entropy = 0.0;
  var listed = 0u;
  if (P.greedy != 0u) {
    if (P.record > 0u) {
      chosen_p = 1.0 / z;
      entropy = (log(z) - ez / z) / LN2;
      listed = min(P.record, n);
      for (var j = 0u; j < listed; j++) {
        R[4u + 2u * j] = ci[j];
        R[5u + 2u * j] = bitcast<u32>(exp(cv[j] - m) / z);
      }
    }
  } else {
    let kk = min(k, n);
    let top = cv[0];
    // Top-p and min-p on the softmax of the top-k, before temperature.
    var sum = 0.0;
    for (var j = 0u; j < kk; j++) {
      sum += exp(cv[j] - top);
    }
    var keep = kk;
    if (P.top_p < 1.0) {
      var cum = 0.0;
      for (var j = 0u; j < kk; j++) {
        cum += exp(cv[j] - top) / sum;
        if (cum >= P.top_p) {
          keep = j + 1u;
          break;
        }
      }
    }
    if (P.min_p > 0.0) {
      let floor = P.min_p / sum;
      var j = 1u;
      while (j < keep && exp(cv[j] - top) / sum >= floor) {
        j++;
      }
      keep = j;
    }
    // Temperature, then the draw.
    var total = 0.0;
    for (var j = 0u; j < keep; j++) {
      total += exp((cv[j] - top) / P.temperature);
    }
    var r = P.random * total;
    var chosen = keep - 1u;
    for (var j = 0u; j < keep; j++) {
      r -= exp((cv[j] - top) / P.temperature);
      if (r <= 0.0) {
        chosen = j;
        break;
      }
    }
    id = ci[chosen];
    for (var j = 0u; j < keep; j++) {
      let p = exp((cv[j] - top) / P.temperature) / total;
      if (p > 0.0) {
        entropy -= p * log2(p);
      }
    }
    chosen_p = exp((cv[chosen] - top) / P.temperature) / total;
    listed = min(P.record, keep);
    for (var j = 0u; j < listed; j++) {
      R[4u + 2u * j] = ci[j];
      R[5u + 2u * j] = bitcast<u32>(exp((cv[j] - top) / P.temperature) / total);
    }
  }
  T[0] = id;
  R[0] = id;
  R[1] = bitcast<u32>(chosen_p);
  R[2] = bitcast<u32>(entropy);
  R[3] = listed;
}

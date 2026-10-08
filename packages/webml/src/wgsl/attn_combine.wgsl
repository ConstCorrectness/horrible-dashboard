// Decode attention, second pass: merge attention.wgsl's per-chunk partials into
// each head's output. With chunk maxima m_s, sums l_s and weighted sums o_s, and
// M = max m_s: O = Σ o_s·exp(m_s − M) / Σ l_s·exp(m_s − M). One workgroup per head.

const WG: u32 = 128u;
// Positions per chunk: attention.wgsl's CH.
const CH: u32 = 128u;

struct Params {
  head_dim: u32,
  splits: u32,
}

struct Step {
  pos: u32,
  n: u32,
}

@group(0) @binding(0) var<storage, read> PART: array<f32>;
@group(0) @binding(1) var<storage, read_write> O: array<f32>;
@group(0) @binding(2) var<uniform> P: Params;
@group(0) @binding(3) var<uniform> S: Step;

@compute @workgroup_size(WG)
fn main(@builtin(workgroup_id) wid: vec3<u32>, @builtin(local_invocation_index) tid: u32) {
  let h = wid.x;
  let hd = P.head_dim;
  let used = (S.pos + CH) / CH;
  let first = h * P.splits * (hd + 2u);
  var big = -3.4e38;
  for (var s = 0u; s < used; s++) {
    big = max(big, PART[first + s * (hd + 2u) + hd]);
  }
  var total = 0.0;
  for (var s = 0u; s < used; s++) {
    let at = first + s * (hd + 2u);
    total += PART[at + hd + 1u] * exp(PART[at + hd] - big);
  }
  for (var j = tid; j < hd; j += WG) {
    var acc = 0.0;
    for (var s = 0u; s < used; s++) {
      let at = first + s * (hd + 2u);
      acc += PART[at + j] * exp(PART[at + hd] - big);
    }
    O[h * hd + j] = acc / total;
  }
}

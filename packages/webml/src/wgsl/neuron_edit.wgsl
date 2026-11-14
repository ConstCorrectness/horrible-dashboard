// Neuron edits on the gated feed-forward's activation, in place, after glu.wgsl
// and before the down projection reads it: for each of the batch's S.n rows and
// each of this layer's edits, A[row][idx] = A[row][idx] * scale + offset. An
// additive edit has scale 1; a multiplicative one has offset 0 (0 ablates).
//
// Every layer's edits sit in one buffer of (idx, scale, offset, _) records; this
// layer's are `count` of them from `start`. One workgroup per row, one thread per
// edit: a recipe holds at most WG neurons, so one layer never has more.

const WG: u32 = 64u;

struct Edit {
  idx: u32,
  scale: f32,
  offset: f32,
  _pad: u32,
}

struct Params {
  n: u32,
  start: u32,
  count: u32,
}

struct Step {
  pos: u32,
  n: u32,
}

@group(0) @binding(0) var<storage, read_write> A: array<f32>;
@group(0) @binding(1) var<storage, read> E: array<Edit>;
@group(0) @binding(2) var<uniform> P: Params;
@group(0) @binding(3) var<uniform> S: Step;

@compute @workgroup_size(WG)
fn main(@builtin(workgroup_id) wid: vec3<u32>, @builtin(local_invocation_index) tid: u32) {
  let row = wid.x;
  if (row >= S.n || tid >= P.count) {
    return;
  }
  let e = E[P.start + tid];
  let i = row * P.n + e.idx;
  A[i] = A[i] * e.scale + e.offset;
}

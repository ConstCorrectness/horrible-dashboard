// Y[r] = row T[r] of the embedding matrix, dequantized and multiplied by `scale`
// (1, or Gemma's √embd), for the batch's S.n tokens: one thread per value. Composed like matvec.wgsl. The token ids are read
// from a GPU buffer, so a sampled id can feed the next step with no round-trip.
//
// An embedding split into row chunks gets one dispatch per chunk; each writes
// only the tokens whose row it holds (first_row ≤ id < first_row + rows).

const WG: u32 = 256u;

struct Params {
  n: u32,
  row_bytes: u32,
  first_row: u32,
  rows: u32,
  scale: f32,
}

struct Step {
  pos: u32,
  n: u32,
}

@group(0) @binding(0) var<storage, read> W: array<u32>;
@group(0) @binding(2) var<storage, read_write> Y: array<f32>;
@group(0) @binding(3) var<uniform> P: Params;
@group(0) @binding(4) var<uniform> S: Step;
@group(0) @binding(5) var<storage, read> T: array<u32>;

// The weight snippets' unit_dot reads activations from here; unused in this kernel.
var<private> xv: array<vec4<f32>, 8>;

@compute @workgroup_size(WG)
fn main(
  @builtin(global_invocation_id) gid: vec3<u32>,
  @builtin(num_workgroups) groups: vec3<u32>,
) {
  let i = gid.x + gid.y * groups.x * WG;
  if (i >= S.n * P.n) {
    return;
  }
  let tok = T[i / P.n];
  if (tok < P.first_row || tok >= P.first_row + P.rows) {
    return;
  }
  let c = i % P.n;
  let per = UNIT * UNITS;
  Y[i] = block_get((tok - P.first_row) * P.row_bytes + (c / per) * BLOCK_BYTES, c % per) * P.scale;
}

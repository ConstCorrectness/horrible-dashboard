// Y = row `S.tok_row` of the embedding matrix, dequantized: one thread per value.
// Composed like matvec.wgsl. The token's row is chosen on the host and written to
// the Step buffer with the position (its chunk picks the bind group).

const WG: u32 = 256u;

struct Params {
  n: u32,
  row_bytes: u32,
}

struct Step {
  pos: u32,
  tok_row: u32,
}

@group(0) @binding(0) var<storage, read> W: array<u32>;
// Declared because the weight snippets' unit_dot reads it; unused here, so the
// auto layout leaves it out.
@group(0) @binding(1) var<storage, read> X: array<f32>;
@group(0) @binding(2) var<storage, read_write> Y: array<f32>;
@group(0) @binding(3) var<uniform> P: Params;
@group(0) @binding(4) var<uniform> S: Step;

@compute @workgroup_size(WG)
fn main(@builtin(global_invocation_id) gid: vec3<u32>) {
  let i = gid.x;
  if (i >= P.n) {
    return;
  }
  let per = UNIT * UNITS;
  Y[i] = block_get(S.tok_row * P.row_bytes + (i / per) * BLOCK_BYTES, i % per);
}

// One position's feed-forward activation (after any neuron edits, so what the
// down projection actually reads) into row `layer` of C. The pass covers
// positions S.pos .. S.pos + S.n - 1; a pass that does not hold `T.position`
// copies nothing, so a prompt's batches can all run with this in their lists.

const WG: u32 = 256u;

struct Params {
  n: u32,
  layer: u32,
}

struct Target {
  position: u32,
}

struct Step {
  pos: u32,
  n: u32,
}

@group(0) @binding(0) var<storage, read> A: array<f32>;
@group(0) @binding(1) var<storage, read_write> C: array<f32>;
@group(0) @binding(2) var<uniform> P: Params;
@group(0) @binding(3) var<uniform> T: Target;
@group(0) @binding(4) var<uniform> S: Step;

@compute @workgroup_size(WG)
fn main(
  @builtin(global_invocation_id) gid: vec3<u32>,
  @builtin(num_workgroups) groups: vec3<u32>,
) {
  if (T.position < S.pos || T.position >= S.pos + S.n) {
    return;
  }
  let i = gid.x + gid.y * groups.x * WG;
  if (i < P.n) {
    let row = T.position - S.pos;
    C[P.layer * P.n + i] = A[row * P.n + i];
  }
}

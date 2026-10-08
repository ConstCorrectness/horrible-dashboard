// Y[r] += B for each of the batch's S.n rows of `n`: a projection's bias (Qwen2's
// Q, K and V), added after the matrix product wrote Y.

const WG: u32 = 256u;

struct Params {
  n: u32,
}

struct Step {
  pos: u32,
  n: u32,
}

@group(0) @binding(0) var<storage, read> B: array<f32>;
@group(0) @binding(1) var<storage, read_write> Y: array<f32>;
@group(0) @binding(2) var<uniform> P: Params;
@group(0) @binding(3) var<uniform> S: Step;

@compute @workgroup_size(WG)
fn main(
  @builtin(global_invocation_id) gid: vec3<u32>,
  @builtin(num_workgroups) groups: vec3<u32>,
) {
  let i = gid.x + gid.y * groups.x * WG;
  if (i < S.n * P.n) {
    Y[i] += B[i % P.n];
  }
}

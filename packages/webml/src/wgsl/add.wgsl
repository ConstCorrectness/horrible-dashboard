// out = a + b, elementwise over `n` f32s. The residual add, and the harness's first
// kernel: small enough that a failure points at the plumbing, not the math.

const WG: u32 = 256u;

struct Params {
  n: u32,
}

@group(0) @binding(0) var<storage, read> a: array<f32>;
@group(0) @binding(1) var<storage, read> b: array<f32>;
@group(0) @binding(2) var<storage, read_write> out: array<f32>;
@group(0) @binding(3) var<uniform> params: Params;

@compute @workgroup_size(WG)
fn main(
  @builtin(global_invocation_id) gid: vec3<u32>,
  @builtin(num_workgroups) groups: vec3<u32>,
) {
  // Flat index across a dispatch that spilled into y (see gpu/dispatch.ts).
  let i = gid.x + gid.y * groups.x * WG;
  if (i < params.n) {
    out[i] = a[i] + b[i];
  }
}

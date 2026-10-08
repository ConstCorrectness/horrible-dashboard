// X += D, elementwise: the residual connection. (A separate kernel from add.wgsl
// because WebGPU forbids binding one buffer as both a read and a writable input.)

const WG: u32 = 256u;

struct Params {
  n: u32,
}

@group(0) @binding(0) var<storage, read_write> X: array<f32>;
@group(0) @binding(1) var<storage, read> D: array<f32>;
@group(0) @binding(2) var<uniform> P: Params;

@compute @workgroup_size(WG)
fn main(@builtin(global_invocation_id) gid: vec3<u32>) {
  let i = gid.x;
  if (i < P.n) {
    X[i] += D[i];
  }
}

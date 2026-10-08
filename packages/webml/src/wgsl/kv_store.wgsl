// KC/VC rows pos … pos + n − 1 ← the pass's new K and V rows (KN, VN: [n][kv_dim],
// f32, already normed and rotated), in the cache's type: kernels.ts prepends
// `CacheWord` and `store` for f16 or f32. Two values per thread, so an f16 pair is
// packed into one word by one thread. llama.cpp converts to its cache type at
// the same point.

const WG: u32 = 256u;

struct Params {
  kv_dim: u32,
}

struct Step {
  pos: u32,
  n: u32,
}

@group(0) @binding(0) var<storage, read> KN: array<vec2<f32>>;
@group(0) @binding(1) var<storage, read> VN: array<vec2<f32>>;
@group(0) @binding(2) var<storage, read_write> KC: array<CacheWord>;
@group(0) @binding(3) var<storage, read_write> VC: array<CacheWord>;
@group(0) @binding(4) var<uniform> P: Params;
@group(0) @binding(5) var<uniform> S: Step;

@compute @workgroup_size(WG)
fn main(
  @builtin(global_invocation_id) gid: vec3<u32>,
  @builtin(num_workgroups) groups: vec3<u32>,
) {
  let half = P.kv_dim / 2u;
  let i = gid.x + gid.y * groups.x * WG;
  if (i >= S.n * half) {
    return;
  }
  let at = S.pos * half + i;
  store(at, KN[i], VN[i]);
}

// Y[out] = W · X for one row per workgroup: the decode-time (M = 1) matrix-vector
// product over quantized weights, dequantized in registers. Composed with
// common.wgsl and one weights/<type>.wgsl, which define BLOCK, BLOCK_BYTES and
// block_dot.
//
// The output index is out_base + pos * out_pos_stride + row, with `pos` from the
// per-token Step buffer: that is how K and V projections land straight in their
// cache slot, with no copy kernel.

const WG: u32 = 64u;

struct Params {
  rows: u32,
  blocks: u32,
  row_bytes: u32,
  out_base: u32,
  out_pos_stride: u32,
}

struct Step {
  pos: u32,
  tok_row: u32,
}

@group(0) @binding(0) var<storage, read> W: array<u32>;
@group(0) @binding(1) var<storage, read> X: array<f32>;
@group(0) @binding(2) var<storage, read_write> Y: array<f32>;
@group(0) @binding(3) var<uniform> P: Params;
@group(0) @binding(4) var<uniform> S: Step;

var<workgroup> partial: array<f32, WG>;

@compute @workgroup_size(WG)
fn main(
  @builtin(workgroup_id) wid: vec3<u32>,
  @builtin(num_workgroups) groups: vec3<u32>,
  @builtin(local_invocation_index) tid: u32,
) {
  let row = wid.x + wid.y * groups.x;
  if (row >= P.rows) {
    return;
  }
  var acc = 0.0;
  for (var b = tid; b < P.blocks; b += WG) {
    acc += block_dot(row * P.row_bytes + b * BLOCK_BYTES, b * BLOCK);
  }
  partial[tid] = acc;
  workgroupBarrier();
  for (var s = WG / 2u; s > 0u; s >>= 1u) {
    if (tid < s) {
      partial[tid] += partial[tid + s];
    }
    workgroupBarrier();
  }
  if (tid == 0u) {
    Y[P.out_base + S.pos * P.out_pos_stride + row] = partial[0];
  }
}

// Y[out] = W · X: the decode-time (M = 1) matrix-vector product over quantized
// weights, dequantized in registers — the per-token hot path, bound by how fast
// the weights stream in. Composed with common.wgsl and one weights/<type>.wgsl
// (UNIT = 32, UNITS, BLOCK_BYTES, unit_dot).
//
// A workgroup takes RPW rows: two groups of LANES threads, R rows each. A row's
// 32-value units are dealt round-robin to the lanes, so neighbouring threads read
// neighbouring memory. A lane loads a unit's 32 activations once, into `xv`, and
// uses them for all R rows of its group: X is read once per R rows rather than
// once per row (in f32 it is ~4× the bytes of a Q8_0 weight row). The rows'
// partial sums meet in workgroup memory.
//
// The output index is out_base + pos * out_pos_stride + row, with `pos` from the
// Step buffer. With `accumulate`, the product is added to what is there: the
// residual connection fused into the projection that feeds it.

const WG: u32 = 64u;
const LANES: u32 = 32u;
const R: u32 = 4u;
const RPW: u32 = (WG / LANES) * R;

struct Params {
  rows: u32,
  units: u32,
  row_bytes: u32,
  out_base: u32,
  out_pos_stride: u32,
  accumulate: u32,
}

struct Step {
  pos: u32,
  n: u32,
}

@group(0) @binding(0) var<storage, read> W: array<u32>;
@group(0) @binding(1) var<storage, read> X: array<vec4<f32>>;
@group(0) @binding(2) var<storage, read_write> Y: array<f32>;
@group(0) @binding(3) var<uniform> P: Params;
@group(0) @binding(4) var<uniform> S: Step;

var<private> xv: array<vec4<f32>, 8>;
var<workgroup> partial: array<f32, WG * R>;

@compute @workgroup_size(WG)
fn main(
  @builtin(workgroup_id) wid: vec3<u32>,
  @builtin(num_workgroups) groups: vec3<u32>,
  @builtin(local_invocation_index) tid: u32,
) {
  let lane = tid % LANES;
  let group = tid / LANES;
  let row0 = (wid.x + wid.y * groups.x) * RPW + group * R;
  var acc: array<f32, R>;
  if (row0 < P.rows) {
    for (var u = lane; u < P.units; u += LANES) {
      for (var k = 0u; k < 8u; k++) {
        xv[k] = X[u * 8u + k];
      }
      let at = (u / UNITS) * BLOCK_BYTES;
      for (var r = 0u; r < R; r++) {
        if (row0 + r < P.rows) {
          acc[r] += unit_dot((row0 + r) * P.row_bytes + at, u % UNITS);
        }
      }
    }
  }
  for (var r = 0u; r < R; r++) {
    partial[(group * R + r) * LANES + lane] = acc[r];
  }
  workgroupBarrier();
  if (tid < RPW) {
    let row = (wid.x + wid.y * groups.x) * RPW + tid;
    if (row < P.rows) {
      var sum = 0.0;
      for (var i = 0u; i < LANES; i++) {
        sum += partial[tid * LANES + i];
      }
      let at = P.out_base + S.pos * P.out_pos_stride + row;
      Y[at] = select(sum, Y[at] + sum, P.accumulate != 0u);
    }
  }
}

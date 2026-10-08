// Y = X · Wᵀ for a batch of `S.n` rows of X: the prefill (M > 1) matrix product
// over quantized weights. Composed like matvec.wgsl with common.wgsl and one
// weights/<type>.wgsl, whose unit_prep/unit_quad dequantize a 32-value unit: each tile of
// weights is dequantized once into workgroup memory and reused by every row of the
// batch, which is what makes a prompt cheaper than one decode step per token. TK
// is one unit, so a thread's share of a weight tile is exactly one unit of its row
// (scales decoded once); row lengths are whole units, as every type's block is.
//
// A workgroup computes a TN × TM tile of the output (weight rows × batch rows),
// stepping through the shared width TK values at a time; each thread holds an
// 8 × 4 block of it in registers. The tiles sit in workgroup memory transposed,
// k-major, so each step of k is two vec4 loads of weights (eight rows) and one of
// four batch rows per thread (workgroup memory is slow on iGPUs; scalar loads made it
// the bottleneck).
//
// Output index: out_base + S.pos * out_pos_stride + m * out_row_stride + row, for
// batch row m and weight row `row`. With out_pos_stride = out_row_stride = the
// cache row width, the K and V projections land straight in their cache slots
// for positions pos … pos + n − 1. With `accumulate`, the product is added to
// what is there: the residual connection fused into the projection that feeds it.

const WG: u32 = 64u;
const TN: u32 = 64u;
const TM: u32 = 32u;
const TK: u32 = 32u;
// Values per weight block.
const PER: u32 = UNIT * UNITS;

struct Params {
  rows: u32,
  cols: u32,
  row_bytes: u32,
  out_base: u32,
  out_pos_stride: u32,
  out_row_stride: u32,
  accumulate: u32,
}

struct Step {
  pos: u32,
  n: u32,
}

@group(0) @binding(0) var<storage, read> W: array<u32>;
@group(0) @binding(1) var<storage, read> X: array<f32>;
@group(0) @binding(2) var<storage, read_write> Y: array<f32>;
@group(0) @binding(3) var<uniform> P: Params;
@group(0) @binding(4) var<uniform> S: Step;

// The weight snippets' unit_dot reads activations from here; unused in this kernel.
var<private> xv: array<vec4<f32>, 8>;

// [k][rows / 4] and [k][batch / 4].
var<workgroup> ws: array<vec4<f32>, TK * TN / 4u>;
var<workgroup> xs: array<vec4<f32>, TK * TM / 4u>;

@compute @workgroup_size(WG)
fn main(@builtin(workgroup_id) wid: vec3<u32>, @builtin(local_invocation_index) tid: u32) {
  let n0 = wid.x * TN;
  let m0 = wid.y * TM;
  if (m0 >= S.n) {
    return;
  }
  let tn = (tid % 8u) * 8u;
  let tm = (tid / 8u) * 4u;
  // Each thread loads one whole weight row of the tile (row tid) and half a batch
  // row (row tid / 2, values xk … xk + 15).
  let xr = tid / 2u;
  let xk = (tid % 2u) * 16u;
  // lo[j] / hi[j] hold weight rows tn … tn + 3 / tn + 4 … tn + 7 for batch row tm + j.
  var lo: array<vec4<f32>, 4>;
  var hi: array<vec4<f32>, 4>;

  for (var k0 = 0u; k0 < P.cols; k0 += TK) {
    // Value k of weight row r goes to ws[k][r / 4], component r % 4.
    let wr = n0 + tid;
    let block = wr * P.row_bytes + (k0 / PER) * BLOCK_BYTES;
    let sub = (k0 % PER) / UNIT;
    var prep = vec4<f32>(0.0);
    if (wr < P.rows) {
      prep = unit_prep(block, sub);
    }
    for (var j = 0u; j < TK; j += 4u) {
      var w = vec4<f32>(0.0);
      if (wr < P.rows) {
        w = unit_quad(block, sub, j / 4u, prep);
      }
      let at = j * (TN / 4u) + tid / 4u;
      ws[at][tid % 4u] = w.x;
      ws[at + TN / 4u][tid % 4u] = w.y;
      ws[at + 2u * (TN / 4u)][tid % 4u] = w.z;
      ws[at + 3u * (TN / 4u)][tid % 4u] = w.w;
    }
    let xm = m0 + xr;
    for (var j = 0u; j < 16u; j += 4u) {
      let c = k0 + xk + j;
      var x = vec4<f32>(0.0);
      if (c < P.cols && xm < S.n) {
        let at = xm * P.cols + c;
        x = vec4<f32>(X[at], X[at + 1u], X[at + 2u], X[at + 3u]);
      }
      let at = (xk + j) * (TM / 4u) + xr / 4u;
      xs[at][xr % 4u] = x.x;
      xs[at + TM / 4u][xr % 4u] = x.y;
      xs[at + 2u * (TM / 4u)][xr % 4u] = x.z;
      xs[at + 3u * (TM / 4u)][xr % 4u] = x.w;
    }
    workgroupBarrier();
    for (var kk = 0u; kk < TK; kk++) {
      let a0 = ws[kk * (TN / 4u) + tn / 4u];
      let a1 = ws[kk * (TN / 4u) + tn / 4u + 1u];
      let b = xs[kk * (TM / 4u) + tm / 4u];
      lo[0] += a0 * b.x;
      lo[1] += a0 * b.y;
      lo[2] += a0 * b.z;
      lo[3] += a0 * b.w;
      hi[0] += a1 * b.x;
      hi[1] += a1 * b.y;
      hi[2] += a1 * b.z;
      hi[3] += a1 * b.w;
    }
    workgroupBarrier();
  }

  for (var j = 0u; j < 4u; j++) {
    let m = m0 + tm + j;
    for (var i = 0u; i < 8u; i++) {
      let row = n0 + tn + i;
      if (row < P.rows && m < S.n) {
        let v = select(lo[j][i % 4u], hi[j][i % 4u], i >= 4u);
        let at = P.out_base + S.pos * P.out_pos_stride + m * P.out_row_stride + row;
        Y[at] = select(v, Y[at] + v, P.accumulate != 0u);
      }
    }
  }
}

/**
 * Every kernel against a CPU reference on seeded random inputs. The matvec, matmul
 * and embed references dequantize with `quant.ts`, itself checked against the
 * `gguf` package's dequantizers, so a mismatch here is the kernel's.
 *
 * The row-wise kernels run over a batch of several rows (the prefill shape); a
 * decode step is the same kernels with one row.
 */
import { describe, expect, it } from 'vitest';

import { distribution } from '../src/distribution';
import { tensorBytes } from '../src/gguf/ggml';
import { dequantize, F16, F32, Q4_0, Q4_K, Q5_0, Q5_K, Q6_K, Q8_0 } from '../src/gguf/quant';
import { ropeTable } from '../src/gguf/rope';
import { dispatch1d } from '../src/gpu/dispatch';
import {
  ATTN_CHUNK,
  attnPrefillTile,
  blockValues,
  Kernels,
  KERNEL_BLOCK,
  type KvType,
  LENS_K,
  LENS_STRIDE,
  MATMUL_TILE,
  uniformBuffer,
  WG,
} from '../src/gpu/kernels';
import {
  dispatchOnce,
  fromF16Bits,
  gpu,
  maxRelError,
  output,
  randomWeights,
  run,
  seeded,
  stepBuffer,
  toF16,
  tokenBuffer,
  upload,
  uploadBytes,
} from './harness';

const TYPES = [
  ['F32', F32],
  ['F16', F16],
  ['Q8_0', Q8_0],
  ['Q4_0', Q4_0],
  ['Q5_0', Q5_0],
  ['Q4_K', Q4_K],
  ['Q5_K', Q5_K],
  ['Q6_K', Q6_K],
] as const;

/** A row length that is two whole blocks of `type`, and at least 96. */
const colsFor = (type: number) => Math.max(96, 2 * blockValues(type));

async function setup() {
  const d = await gpu();
  return { device: d.device, k: new Kernels(d.device) };
}

/** W·x for each of `xs`' rows, and Σ|w·x| per output as the error scale. */
function reference(type: number, w: Uint8Array, rows: number, cols: number, xs: Float32Array) {
  const rowBytes = tensorBytes(type, cols)!;
  const n = xs.length / cols;
  const want = new Float64Array(n * rows);
  const scale = new Float64Array(n * rows);
  for (let r = 0; r < rows; r++) {
    const row = dequantize(type, w, r * rowBytes, cols);
    for (let m = 0; m < n; m++) {
      let sum = 0;
      let mag = 0;
      for (let j = 0; j < cols; j++) {
        sum += row[j] * xs[m * cols + j];
        mag += Math.abs(row[j] * xs[m * cols + j]);
      }
      want[m * rows + r] = sum;
      scale[m * rows + r] = mag;
    }
  }
  return { want, scale };
}

describe('matvec', () => {
  for (const [name, type] of TYPES) {
    it(`${name}: W·x into an offset, position-strided slot`, async () => {
      const { device, k } = await setup();
      const rows = 37;
      const cols = colsFor(type);
      const pos = 2;
      const outBase = 5;
      const posStride = 50;
      const w = randomWeights(type, rows, cols, 11 + type);
      const x = seeded(cols, 3);
      const rowBytes = tensorBytes(type, cols)!;

      const total = outBase + pos * posStride + rows;
      const out = output(device, total);
      const got = await dispatchOnce(
        device,
        k.matvec(type),
        {
          0: uploadBytes(device, w),
          1: upload(device, x),
          2: out,
          3: uniformBuffer(device, 'p', [
            rows,
            cols / KERNEL_BLOCK[type].unit,
            rowBytes,
            outBase,
            posStride,
          ]),
          4: stepBuffer(device, pos),
        },
        dispatch1d(rows, WG.matvecRows, 65535),
        out,
        total,
      );

      const { want, scale } = reference(type, w, rows, cols, x);
      const at = outBase + pos * posStride;
      expect(maxRelError(got.subarray(at, at + rows), want, scale)).toBeLessThan(1e-5);
      // Nothing written outside the slot.
      expect(got.subarray(0, at).every((v) => v === 0)).toBe(true);
    });
  }
});

describe('matmul', () => {
  /** Run matmul over `n` rows of X and return what it wrote. */
  async function matmul(
    type: number,
    rows: number,
    cols: number,
    n: number,
    layout: { pos: number; base: number; posStride: number; rowStride: number },
    seed: number,
  ) {
    const { device, k } = await setup();
    const w = randomWeights(type, rows, cols, seed);
    const xs = seeded(n * cols, seed + 1);
    const total = layout.base + (layout.pos + n) * Math.max(layout.posStride, layout.rowStride);
    const out = output(device, total);
    const got = await dispatchOnce(
      device,
      k.matmul(type),
      {
        0: uploadBytes(device, w),
        1: upload(device, xs),
        2: out,
        3: uniformBuffer(device, 'p', [
          rows,
          cols,
          tensorBytes(type, cols)!,
          layout.base,
          layout.posStride,
          layout.rowStride,
        ]),
        4: stepBuffer(device, layout.pos, n),
      },
      [Math.ceil(rows / MATMUL_TILE.rows), Math.ceil(n / MATMUL_TILE.batch)],
      out,
      total,
    );
    return { got, ...reference(type, w, rows, cols, xs) };
  }

  for (const [name, type] of TYPES) {
    it(`${name}: a batch into the rows of an activation buffer, partial tiles both ways`, async () => {
      const rows = 37;
      const cols = colsFor(type);
      const n = 35;
      const { got, want, scale } = await matmul(
        type,
        rows,
        cols,
        n,
        { pos: 9, base: 0, posStride: 0, rowStride: rows },
        31 + type,
      );
      expect(maxRelError(got.subarray(0, n * rows), want, scale)).toBeLessThan(1e-5);
    });
  }

  it('Q4_K: a batch into cache rows at positions pos … pos + n − 1', async () => {
    const rows = 40;
    const cols = 512;
    const n = 5;
    const layout = { pos: 3, base: 7, posStride: 48, rowStride: 48 };
    const { got, want, scale } = await matmul(Q4_K, rows, cols, n, layout, 77);
    for (let m = 0; m < n; m++) {
      const at = layout.base + (layout.pos + m) * 48;
      expect(
        maxRelError(
          got.subarray(at, at + rows),
          want.subarray(m * rows, (m + 1) * rows),
          scale.subarray(m * rows, (m + 1) * rows),
        ),
      ).toBeLessThan(1e-5);
    }
    // Earlier positions untouched.
    expect(got.subarray(0, layout.base + layout.pos * 48).every((v) => v === 0)).toBe(true);
  });
});

describe('residual fused into the projection (accumulate)', () => {
  it('matvec Q4_K and matmul Q8_0 add W·x to what the output holds', async () => {
    const { device, k } = await setup();
    const rows = 40;
    const n = 3;
    for (const [type, cols, batch] of [
      [Q4_K, 512, 1],
      [Q8_0, 96, n],
    ] as const) {
      const w = randomWeights(type, rows, cols, 51 + type);
      const xs = seeded(batch * cols, 52);
      const before = seeded(batch * rows, 53);
      const out = upload(device, before, 'residual');
      const rowBytes = tensorBytes(type, cols)!;
      const got =
        batch === 1
          ? await dispatchOnce(
              device,
              k.matvec(type),
              {
                0: uploadBytes(device, w),
                1: upload(device, xs),
                2: out,
                3: uniformBuffer(device, 'p', [
                  rows,
                  cols / KERNEL_BLOCK[type].unit,
                  rowBytes,
                  0,
                  0,
                  1,
                ]),
                4: stepBuffer(device, 0),
              },
              dispatch1d(rows, WG.matvecRows, 65535),
              out,
              rows,
            )
          : await dispatchOnce(
              device,
              k.matmul(type),
              {
                0: uploadBytes(device, w),
                1: upload(device, xs),
                2: out,
                3: uniformBuffer(device, 'p', [rows, cols, rowBytes, 0, 0, rows, 1]),
                4: stepBuffer(device, 0, batch),
              },
              [Math.ceil(rows / MATMUL_TILE.rows), Math.ceil(batch / MATMUL_TILE.batch)],
              out,
              batch * rows,
            );
      const { want, scale } = reference(type, w, rows, cols, xs);
      const total = Array.from(want, (v, i) => v + before[i]);
      expect(
        maxRelError(
          got,
          total,
          Array.from(scale, (v, i) => v + Math.abs(before[i])),
        ),
      ).toBeLessThan(1e-5);
    }
  });
});

describe('embed', () => {
  for (const [name, type] of TYPES) {
    it(`${name}: dequantizes each token's row exactly`, async () => {
      const { device, k } = await setup();
      const rows = 9;
      const cols = colsFor(type);
      const tokens = [6, 2, 6];
      const w = randomWeights(type, rows, cols, 21 + type);
      const rowBytes = tensorBytes(type, cols)!;
      const out = output(device, tokens.length * cols);
      const got = await dispatchOnce(
        device,
        k.embed(type),
        {
          0: uploadBytes(device, w),
          2: out,
          3: uniformBuffer(device, 'p', [cols, rowBytes, 0, rows, { f32: 1 }]),
          4: stepBuffer(device, 0, tokens.length),
          5: tokenBuffer(device, tokens),
        },
        dispatch1d(tokens.length * cols, WG.embed, 65535),
        out,
        tokens.length * cols,
      );
      const want = tokens.flatMap((t) => Array.from(dequantize(type, w, t * rowBytes, cols)));
      expect(Array.from(got)).toEqual(want);
    });
  }

  it('a chunk writes only the tokens whose rows it holds', async () => {
    const { device, k } = await setup();
    const cols = 64;
    const all = randomWeights(Q8_0, 9, cols, 5);
    const rowBytes = tensorBytes(Q8_0, cols)!;
    // The chunk holding rows 4–6.
    const chunk = all.slice(4 * rowBytes, 7 * rowBytes);
    const tokens = [6, 2, 4];
    const out = output(device, tokens.length * cols);
    const got = await dispatchOnce(
      device,
      k.embed(Q8_0),
      {
        0: uploadBytes(device, chunk),
        2: out,
        3: uniformBuffer(device, 'p', [cols, rowBytes, 4, 3, { f32: 1 }]),
        4: stepBuffer(device, 0, tokens.length),
        5: tokenBuffer(device, tokens),
      },
      dispatch1d(tokens.length * cols, WG.embed, 65535),
      out,
      tokens.length * cols,
    );
    expect(Array.from(got.subarray(0, cols))).toEqual(
      Array.from(dequantize(Q8_0, all, 6 * rowBytes, cols)),
    );
    expect(got.subarray(cols, 2 * cols).every((v) => v === 0)).toBe(true);
    expect(Array.from(got.subarray(2 * cols))).toEqual(
      Array.from(dequantize(Q8_0, all, 4 * rowBytes, cols)),
    );
  });

  it('multiplies every value by the scale (Gemma’s √embd)', async () => {
    const { device, k } = await setup();
    const cols = 64;
    const rows = 5;
    const w = randomWeights(Q8_0, rows, cols, 8);
    const rowBytes = tensorBytes(Q8_0, cols)!;
    const tokens = [3, 1];
    const scale = Math.fround(Math.sqrt(cols));
    const out = output(device, tokens.length * cols);
    const got = await dispatchOnce(
      device,
      k.embed(Q8_0),
      {
        0: uploadBytes(device, w),
        2: out,
        3: uniformBuffer(device, 'p', [cols, rowBytes, 0, rows, { f32: scale }]),
        4: stepBuffer(device, 0, tokens.length),
        5: tokenBuffer(device, tokens),
      },
      dispatch1d(tokens.length * cols, WG.embed, 65535),
      out,
      tokens.length * cols,
    );
    const want = tokens.flatMap((t) =>
      Array.from(dequantize(Q8_0, w, t * rowBytes, cols), (v) => Math.fround(v * scale)),
    );
    expect(Array.from(got)).toEqual(want);
  });
});

describe('rmsnorm', () => {
  const n = 300;
  const rows = 3;
  const eps = 1e-5;
  const norm = (x: Float32Array, g: Float32Array) => {
    let ss = 0;
    for (const v of x) ss += v * v;
    const s = 1 / Math.sqrt(ss / x.length + eps);
    return Array.from(x, (v, i) => v * s * g[i]);
  };

  for (const last of [false, true]) {
    it(
      last
        ? 'last: only the batch’s last row, into row 0'
        : 'each row of a batch, rows longer than one pass of the workgroup',
      async () => {
        const { device, k } = await setup();
        const x = seeded(rows * n, 4);
        const g = seeded(n, 5);
        const out = output(device, rows * n);
        const got = await dispatchOnce(
          device,
          k.rmsnorm(),
          {
            0: upload(device, x),
            1: upload(device, g),
            2: out,
            3: uniformBuffer(device, 'p', [n, { f32: eps }, last ? 1 : 0]),
            4: stepBuffer(device, 0, rows),
          },
          [last ? 1 : rows, 1],
          out,
          rows * n,
        );
        const want = last
          ? norm(x.subarray((rows - 1) * n), g)
          : Array.from({ length: rows }, (_, r) => norm(x.subarray(r * n, (r + 1) * n), g)).flat();
        expect(
          maxRelError(
            got.subarray(0, want.length),
            want,
            want.map((v) => Math.abs(v) + 1e-3),
          ),
        ).toBeLessThan(1e-5);
        if (last) expect(got.subarray(n).every((v) => v === 0)).toBe(true);
      },
    );
  }

  it('accumulate: adds each normed row to what the output holds', async () => {
    const { device, k } = await setup();
    const x = seeded(rows * n, 24);
    const g = seeded(n, 25);
    const before = seeded(rows * n, 26);
    const out = upload(device, before);
    const got = await dispatchOnce(
      device,
      k.rmsnorm(),
      {
        0: upload(device, x),
        1: upload(device, g),
        2: out,
        3: uniformBuffer(device, 'p', [n, { f32: eps }, 0, 1]),
        4: stepBuffer(device, 0, rows),
      },
      [rows, 1],
      out,
      rows * n,
    );
    const want = Array.from({ length: rows }, (_, r) => norm(x.subarray(r * n, (r + 1) * n), g))
      .flat()
      .map((v, i) => v + before[i]);
    expect(
      maxRelError(
        got,
        want,
        want.map((v) => Math.abs(v) + 1e-3),
      ),
    ).toBeLessThan(1e-5);
  });
});

describe('headnorm', () => {
  it('normalizes each head of each row of a position-strided slot with one shared weight', async () => {
    const { device, k } = await setup();
    const heads = 3;
    const headDim = 40;
    const pos = 2;
    const rows = 2;
    const stride = 130;
    const eps = 1e-6;
    const n = (pos + rows) * stride;
    const v = seeded(n, 14);
    const g = seeded(headDim, 15);
    const buf = upload(device, v);
    const got = await dispatchOnce(
      device,
      k.headnorm(),
      {
        0: buf,
        1: upload(device, g),
        2: uniformBuffer(device, 'p', [headDim, { f32: eps }, 0, stride, stride]),
        3: stepBuffer(device, pos, rows),
      },
      [heads, rows],
      buf,
      n,
    );
    const want = Float32Array.from(v);
    for (let r = 0; r < rows; r++) {
      for (let h = 0; h < heads; h++) {
        const at = (pos + r) * stride + h * headDim;
        let ss = 0;
        for (let i = 0; i < headDim; i++) ss += v[at + i] ** 2;
        const s = 1 / Math.sqrt(ss / headDim + eps);
        for (let i = 0; i < headDim; i++) want[at + i] = v[at + i] * s * g[i];
      }
    }
    expect(maxRelError(got, want, new Float32Array(n).fill(1))).toBeLessThan(1e-5);
  });
});

describe('rope', () => {
  for (const neox of [false, true]) {
    it(`${neox ? 'neox' : 'norm'}: rotates the right pairs of partially rotated heads, row by row`, async () => {
      const { device, k } = await setup();
      const heads = 3;
      const headDim = 16;
      const ropeDims = 12;
      const pos = 5;
      const rows = 3;
      const base = 7;
      const stride = 60;
      const table = ropeTable(pos + rows, ropeDims, 10000);
      const n = base + (pos + rows) * stride;
      const v = seeded(n, 6);
      const buf = upload(device, v);
      const got = await dispatchOnce(
        device,
        k.rope(),
        {
          0: buf,
          1: upload(device, table),
          2: uniformBuffer(device, 'p', [
            heads,
            headDim,
            ropeDims,
            base,
            stride,
            neox ? 1 : 0,
            stride,
          ]),
          3: stepBuffer(device, pos, rows),
        },
        dispatch1d((rows * heads * ropeDims) / 2, WG.rope, 65535),
        buf,
        n,
      );

      const want = Float32Array.from(v);
      for (let r = 0; r < rows; r++) {
        const p = pos + r;
        for (let h = 0; h < heads; h++) {
          const start = base + p * stride + h * headDim;
          for (let i = 0; i < ropeDims / 2; i++) {
            const a = start + (neox ? i : 2 * i);
            const b = neox ? a + ropeDims / 2 : a + 1;
            const c = table[p * ropeDims + 2 * i];
            const s = table[p * ropeDims + 2 * i + 1];
            want[a] = v[a] * c - v[b] * s;
            want[b] = v[a] * s + v[b] * c;
          }
        }
      }
      expect(maxRelError(got, want, new Float32Array(n).fill(1))).toBeLessThan(1e-6);
    });
  }
});

/**
 * softmax(q·Kᵀ·scale)·V over cache positions 0 … last, for one query head; with a
 * sliding `window`, over its last `window` positions only.
 */
function attendCpu(
  q: ArrayLike<number>,
  qAt: number,
  kc: Float32Array,
  vc: Float32Array,
  kvAt: number,
  kvDim: number,
  headDim: number,
  last: number,
  scale: number,
  window = 0,
): number[] {
  const first = window ? Math.max(0, last + 1 - window) : 0;
  const s: number[] = [];
  for (let t = first; t <= last; t++) {
    let d = 0;
    for (let j = 0; j < headDim; j++) d += q[qAt + j] * kc[t * kvDim + kvAt + j];
    s.push(d * scale);
  }
  const m = Math.max(...s);
  const e = s.map((x) => Math.exp(x - m));
  const z = e.reduce((a, b) => a + b, 0);
  const out: number[] = [];
  for (let j = 0; j < headDim; j++) {
    let acc = 0;
    for (let t = first; t <= last; t++) acc += e[t - first] * vc[t * kvDim + kvAt + j];
    out.push(acc / z);
  }
  return out;
}

/** Cache contents as the kernels see them: rounded to f16 for an f16 cache. */
function cacheValues(values: Float32Array, kv: KvType): Float32Array {
  return kv === 'f16' ? toF16(values).values : values;
}

/** A cache buffer holding `values` (already rounded by `cacheValues`). */
function cacheBuffer(device: GPUDevice, values: Float32Array, kv: KvType): GPUBuffer {
  return kv === 'f16'
    ? uploadBytes(device, new Uint8Array(toF16(values).bits.buffer))
    : upload(device, values);
}

describe('kv_store', () => {
  for (const kv of ['f32', 'f16'] as const) {
    it(`${kv}: the pass's new rows into cache rows pos … pos + n − 1`, async () => {
      const { device, k } = await setup();
      const kvDim = 24;
      const pos = 3;
      const n = 4;
      const ctx = 9;
      const kn = seeded(n * kvDim, 31);
      const vn = seeded(n * kvDim, 32);
      const bytes = ctx * kvDim * (kv === 'f16' ? 2 : 4);
      const kc = device.createBuffer({
        size: bytes,
        usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_SRC,
      });
      const vc = device.createBuffer({
        size: bytes,
        usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_SRC,
      });
      const read = (buffer: GPUBuffer) =>
        dispatchOnce(
          device,
          k.kvStore(kv),
          {
            0: upload(device, kn),
            1: upload(device, vn),
            2: kc,
            3: vc,
            4: uniformBuffer(device, 'p', [kvDim]),
            5: stepBuffer(device, pos, n),
          },
          dispatch1d((n * kvDim) / 2, WG.kvStore, 65535),
          buffer,
          bytes / 4,
        );
      for (const [buffer, src] of [
        [kc, kn],
        [vc, vn],
      ] as const) {
        const words = await read(buffer);
        const got =
          kv === 'f16' ? Float32Array.from(new Uint16Array(words.buffer), fromF16Bits) : words;
        const want = new Float32Array(ctx * kvDim);
        want.set(cacheValues(src, kv), pos * kvDim);
        expect(Array.from(got)).toEqual(Array.from(want));
      }
    });
  }
});

describe('attention (decode)', () => {
  for (const [ctx, pos, headDim, kv, window] of [
    [10, 6, 16, 'f32', 0],
    [300, 290, 16, 'f32', 0],
    [1000, 999, 64, 'f32', 0],
    [260, 128, 128, 'f32', 0],
    [300, 290, 16, 'f16', 0],
    [1000, 999, 64, 'f16', 0],
    [10, 6, 16, 'f32', 4],
    [1000, 700, 64, 'f32', 300],
    [600, 512, 256, 'f16', 512],
    [300, 100, 16, 'f32', 512],
  ] as const) {
    const windowed = window ? `, a window of ${window}` : '';
    it(`split over chunks, then merged: ${kv} cache, heads of ${headDim}, ${pos + 1} positions${windowed}`, async () => {
      const { device, k } = await setup();
      const heads = 4;
      const kvHeads = 2;
      const kvDim = kvHeads * headDim;
      const scale = 1 / Math.sqrt(headDim);
      const splits = Math.ceil(ctx / ATTN_CHUNK);
      const q = seeded(heads * headDim, 7);
      const kc = cacheValues(seeded(ctx * kvDim, 8), kv);
      const vc = cacheValues(seeded(ctx * kvDim, 9), kv);
      const partials = output(device, heads * splits * (headDim + 2), 'partials');
      const out = output(device, heads * headDim);
      const step = stepBuffer(device, pos);
      const bind = (pipeline: GPUComputePipeline, buffers: Record<number, GPUBuffer>) =>
        device.createBindGroup({
          layout: pipeline.getBindGroupLayout(0),
          entries: Object.entries(buffers).map(([b, buffer]) => ({
            binding: Number(b),
            resource: { buffer },
          })),
        });
      const split = k.attention(kv, headDim, heads / kvHeads);
      const combine = k.attnCombine();
      const got = await run(
        device,
        (enc) => {
          const pass = enc.beginComputePass();
          pass.setPipeline(split);
          pass.setBindGroup(
            0,
            bind(split, {
              0: upload(device, q),
              1: cacheBuffer(device, kc, kv),
              2: cacheBuffer(device, vc, kv),
              3: partials,
              4: uniformBuffer(device, 'p', [kvDim, splits, { f32: scale }, window]),
              5: step,
            }),
          );
          pass.dispatchWorkgroups(kvHeads, splits);
          pass.setPipeline(combine);
          pass.setBindGroup(
            0,
            bind(combine, {
              0: partials,
              1: out,
              2: uniformBuffer(device, 'p', [headDim, splits, window]),
              3: step,
            }),
          );
          pass.dispatchWorkgroups(heads, 1);
          pass.end();
        },
        out,
        heads * headDim,
      );

      const want: number[] = [];
      for (let h = 0; h < heads; h++) {
        const kvAt = Math.floor(h / (heads / kvHeads)) * headDim;
        want.push(...attendCpu(q, h * headDim, kc, vc, kvAt, kvDim, headDim, pos, scale, window));
      }
      expect(maxRelError(got, want, new Float32Array(want.length).fill(1))).toBeLessThan(1e-5);
    });
  }
});

describe('attention (prefill)', () => {
  for (const [headDim, pos, n, kv, window] of [
    [16, 5, 21, 'f32', 0],
    [64, 0, 40, 'f32', 0],
    [128, 70, 17, 'f32', 0],
    [48, 9, 30, 'f32', 0],
    [64, 3, 40, 'f16', 0],
    [16, 5, 21, 'f32', 7],
    [256, 40, 37, 'f32', 19],
    [64, 3, 40, 'f16', 100],
  ] as const) {
    const windowed = window ? `, a window of ${window}` : '';
    it(`causal over cache + batch, ${kv} cache, heads of ${headDim}: ${n} queries from position ${pos}${windowed}`, async () => {
      const { device, k } = await setup();
      const heads = 4;
      const kvHeads = 2;
      const qDim = heads * headDim;
      const kvDim = kvHeads * headDim;
      const ctx = pos + n + 3;
      const scale = 1 / Math.sqrt(headDim);
      const q = seeded(n * qDim, 17);
      // Rows past pos + n − 1 hold noise the causal mask must never read.
      const kc = cacheValues(seeded(ctx * kvDim, 18), kv);
      const vc = cacheValues(seeded(ctx * kvDim, 19), kv);
      const out = output(device, n * qDim);
      const tile = attnPrefillTile(headDim);
      const got = await dispatchOnce(
        device,
        k.attnPrefill(headDim, kv),
        {
          0: upload(device, q),
          1: cacheBuffer(device, kc, kv),
          2: cacheBuffer(device, vc, kv),
          3: out,
          4: uniformBuffer(device, 'p', [qDim, kvDim, heads / kvHeads, { f32: scale }, window]),
          5: stepBuffer(device, pos, n),
        },
        [heads, Math.ceil(n / tile)],
        out,
        n * qDim,
      );

      const want: number[] = [];
      for (let r = 0; r < n; r++) {
        for (let h = 0; h < heads; h++) {
          const kvAt = Math.floor(h / (heads / kvHeads)) * headDim;
          want.push(
            ...attendCpu(
              q,
              r * qDim + h * headDim,
              kc,
              vc,
              kvAt,
              kvDim,
              headDim,
              pos + r,
              scale,
              window,
            ),
          );
        }
      }
      expect(maxRelError(got, want, new Float32Array(want.length).fill(1))).toBeLessThan(1e-5);
    });
  }
});

describe('elementwise', () => {
  const n = 150;
  const rows = 2;

  // GELU's tanh approximation, as ggml_gelu_f32.
  const gelu = (x: number) =>
    0.5 * x * (1 + Math.tanh(Math.sqrt(2 / Math.PI) * x * (1 + 0.044715 * x * x)));

  for (const [name, flag, act] of [
    ['silu', 0, (x: number) => x / (1 + Math.exp(-x))],
    ['gelu', 1, gelu],
  ] as const) {
    it(`glu: ${name}(g) · u over every row, large gates included`, async () => {
      const { device, k } = await setup();
      // Gates out to ±60: GELU's tanh must saturate there, not overflow to NaN.
      const g = seeded(rows * n, 10).map((v, i) => (i % 50 === 0 ? 60 * Math.sign(v) : v * 6));
      const u = seeded(rows * n, 11);
      const out = output(device, rows * n);
      const got = await dispatchOnce(
        device,
        k.glu(),
        {
          0: upload(device, g),
          1: upload(device, u),
          2: out,
          3: uniformBuffer(device, 'p', [n, flag]),
          4: stepBuffer(device, 0, rows),
        },
        dispatch1d(rows * n, WG.glu, 65535),
        out,
        rows * n,
      );
      const want = Array.from(g, (x, i) => act(x) * u[i]);
      expect(got.every(Number.isFinite)).toBe(true);
      expect(
        maxRelError(
          got,
          want,
          want.map((v) => Math.abs(v) + 1),
        ),
      ).toBeLessThan(1e-6);
    });
  }

  it('bias: adds one vector to every row', async () => {
    const { device, k } = await setup();
    const b = seeded(n, 12);
    const y = seeded(rows * n, 13);
    const out = upload(device, y);
    const got = await dispatchOnce(
      device,
      k.bias(),
      {
        0: upload(device, b),
        1: out,
        2: uniformBuffer(device, 'p', [n]),
        3: stepBuffer(device, 0, rows),
      },
      dispatch1d(rows * n, WG.bias, 65535),
      out,
      rows * n,
    );
    const want = Array.from(y, (v, i) => v + b[i % n]);
    expect(maxRelError(got, want, new Float32Array(rows * n).fill(1))).toBeLessThan(1e-6);
  });
});

describe('logit lens', () => {
  it('lens_in: the last row of the pass, normed into its layer’s row, and its L2 norm', async () => {
    const { device, k } = await setup();
    const n = 300;
    const rows = 3;
    const layers = 4;
    const layer = 2;
    const eps = 1e-6;
    const x = seeded(rows * n, 40);
    const g = seeded(n, 41);
    const lensIn = output(device, layers * n);
    const readouts = output(device, layers * LENS_STRIDE);
    const bindings = {
      0: upload(device, x),
      1: upload(device, g),
      2: lensIn,
      3: readouts,
      4: uniformBuffer(device, 'p', [n, { f32: eps }, layer, LENS_STRIDE]),
      5: stepBuffer(device, 7, rows),
    };
    const got = await dispatchOnce(device, k.lensIn(), bindings, [1, 1], lensIn, layers * n);
    const last = x.subarray((rows - 1) * n);
    let ss = 0;
    for (const v of last) ss += v * v;
    const scale = 1 / Math.sqrt(ss / n + eps);
    const want = Array.from(last, (v, i) => v * scale * g[i]);
    const row = got.subarray(layer * n, (layer + 1) * n);
    expect(
      maxRelError(
        row,
        want,
        want.map((v) => Math.abs(v) + 1e-3),
      ),
    ).toBeLessThan(1e-5);
    // Other layers' rows are untouched.
    expect(got.subarray(0, layer * n).every((v) => v === 0)).toBe(true);
    const words = await dispatchOnce(
      device,
      k.lensIn(),
      bindings,
      [1, 1],
      readouts,
      layers * LENS_STRIDE,
    );
    expect(words[layer * LENS_STRIDE]).toBeCloseTo(Math.sqrt(ss), 3);
  });

  it('lens_topk: per row, the entropy and the top tokens with their probabilities', async () => {
    const { device, k } = await setup();
    const vocab = 1000;
    const layers = 3;
    const logits = seeded(layers * vocab, 42).map((v) => v * 4);
    // A tie inside the top tokens of row 1: the lower id must come first.
    const row1 = logits.subarray(vocab);
    const peak = Math.max(...row1) + 1;
    row1[700] = peak;
    row1[300] = peak;
    const readouts = output(device, layers * LENS_STRIDE);
    const words = await dispatchOnce(
      device,
      k.lensTopk(),
      {
        0: upload(device, logits),
        1: readouts,
        2: uniformBuffer(device, 'p', [vocab, LENS_STRIDE]),
      },
      [layers, 1],
      readouts,
      layers * LENS_STRIDE,
    );
    const ids = new Uint32Array(words.buffer);
    for (let l = 0; l < layers; l++) {
      const want = distribution(logits.subarray(l * vocab, (l + 1) * vocab), LENS_K);
      const at = l * LENS_STRIDE;
      expect(words[at + 1]).toBeCloseTo(want.entropy, 4);
      for (let j = 0; j < LENS_K; j++) {
        expect(ids[at + 2 + 2 * j], `row ${l} pick ${j}`).toBe(want.top[j].id);
        expect(words[at + 3 + 2 * j]).toBeCloseTo(want.top[j].p, 5);
      }
    }
    expect([ids[LENS_STRIDE + 2], ids[LENS_STRIDE + 4]]).toEqual([300, 700]);
  });
});

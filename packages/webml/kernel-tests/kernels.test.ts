/**
 * Every 6.1 kernel against a CPU reference on seeded random inputs. The matvec and
 * embed references dequantize with `quant.ts`, itself checked against the `gguf`
 * package's dequantizers, so a mismatch here is the kernel's.
 */
import { describe, expect, it } from 'vitest';

import { tensorBytes } from '../src/gguf/ggml';
import { dequantize, F16, F32, Q4_0, Q4_K, Q5_K, Q6_K, Q8_0 } from '../src/gguf/quant';
import { ropeTable } from '../src/gguf/rope';
import { dispatch1d } from '../src/gpu/dispatch';
import { blockValues, Kernels, KERNEL_BLOCK, uniformBuffer, WG } from '../src/gpu/kernels';
import {
  dispatchOnce,
  gpu,
  maxRelError,
  output,
  randomWeights,
  seeded,
  stepBuffer,
  upload,
  uploadBytes,
} from './harness';

const TYPES = [
  ['F32', F32],
  ['F16', F16],
  ['Q8_0', Q8_0],
  ['Q4_0', Q4_0],
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
        dispatch1d(rows, 1, 65535),
        out,
        total,
      );

      const want: number[] = [];
      const scale: number[] = [];
      for (let r = 0; r < rows; r++) {
        const row = dequantize(type, w, r * rowBytes, cols);
        let sum = 0;
        let mag = 0;
        for (let j = 0; j < cols; j++) {
          sum += row[j] * x[j];
          mag += Math.abs(row[j] * x[j]);
        }
        want.push(sum);
        scale.push(mag);
      }
      const at = outBase + pos * posStride;
      expect(maxRelError(got.subarray(at, at + rows), want, scale)).toBeLessThan(1e-5);
      // Nothing written outside the slot.
      expect(got.subarray(0, at).every((v) => v === 0)).toBe(true);
    });
  }
});

describe('embed', () => {
  for (const [name, type] of TYPES) {
    it(`${name}: dequantizes the token's row exactly`, async () => {
      const { device, k } = await setup();
      const rows = 9;
      const cols = colsFor(type);
      const token = 6;
      const w = randomWeights(type, rows, cols, 21 + type);
      const rowBytes = tensorBytes(type, cols)!;
      const out = output(device, cols);
      const got = await dispatchOnce(
        device,
        k.embed(type),
        {
          0: uploadBytes(device, w),
          2: out,
          3: uniformBuffer(device, 'p', [cols, rowBytes]),
          4: stepBuffer(device, 0, token),
        },
        dispatch1d(cols, WG.embed, 65535),
        out,
        cols,
      );
      expect(Array.from(got)).toEqual(Array.from(dequantize(type, w, token * rowBytes, cols)));
    });
  }
});

describe('rmsnorm', () => {
  it('normalizes a row longer than one pass of the workgroup', async () => {
    const { device, k } = await setup();
    const n = 300;
    const eps = 1e-5;
    const x = seeded(n, 4);
    const g = seeded(n, 5);
    const out = output(device, n);
    const got = await dispatchOnce(
      device,
      k.rmsnorm(),
      {
        0: upload(device, x),
        1: upload(device, g),
        2: out,
        3: uniformBuffer(device, 'p', [n, { f32: eps }]),
      },
      [1, 1],
      out,
      n,
    );
    let ss = 0;
    for (const v of x) ss += v * v;
    const s = 1 / Math.sqrt(ss / n + eps);
    const want = Array.from(x, (v, i) => v * s * g[i]);
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
  it('normalizes each head of a position-strided slot with one shared weight', async () => {
    const { device, k } = await setup();
    const heads = 3;
    const headDim = 40;
    const pos = 2;
    const posStride = 130;
    const eps = 1e-6;
    const n = pos * posStride + heads * headDim;
    const v = seeded(n, 14);
    const g = seeded(headDim, 15);
    const buf = upload(device, v);
    const got = await dispatchOnce(
      device,
      k.headnorm(),
      {
        0: buf,
        1: upload(device, g),
        2: uniformBuffer(device, 'p', [headDim, { f32: eps }, 0, posStride]),
        3: stepBuffer(device, pos),
      },
      [heads, 1],
      buf,
      n,
    );
    const want = Float32Array.from(v);
    for (let h = 0; h < heads; h++) {
      const at = pos * posStride + h * headDim;
      let ss = 0;
      for (let i = 0; i < headDim; i++) ss += v[at + i] ** 2;
      const s = 1 / Math.sqrt(ss / headDim + eps);
      for (let i = 0; i < headDim; i++) want[at + i] = v[at + i] * s * g[i];
    }
    expect(maxRelError(got, want, new Float32Array(n).fill(1))).toBeLessThan(1e-5);
  });
});

describe('rope', () => {
  for (const neox of [false, true]) {
    it(`${neox ? 'neox' : 'norm'}: rotates the right pairs of a partially rotated head`, async () => {
      const { device, k } = await setup();
      const heads = 3;
      const headDim = 16;
      const ropeDims = 12;
      const pos = 5;
      const base = 7;
      const posStride = 60;
      const table = ropeTable(8, ropeDims, 10000);
      const n = base + pos * posStride + heads * headDim;
      const v = seeded(n, 6);
      const buf = upload(device, v);
      const got = await dispatchOnce(
        device,
        k.rope(),
        {
          0: buf,
          1: upload(device, table),
          2: uniformBuffer(device, 'p', [heads, headDim, ropeDims, base, posStride, neox ? 1 : 0]),
          3: stepBuffer(device, pos),
        },
        dispatch1d((heads * ropeDims) / 2, WG.rope, 65535),
        buf,
        n,
      );

      const want = Float32Array.from(v);
      for (let h = 0; h < heads; h++) {
        const start = base + pos * posStride + h * headDim;
        for (let i = 0; i < ropeDims / 2; i++) {
          const a = start + (neox ? i : 2 * i);
          const b = neox ? a + ropeDims / 2 : a + 1;
          const c = table[pos * ropeDims + 2 * i];
          const s = table[pos * ropeDims + 2 * i + 1];
          want[a] = v[a] * c - v[b] * s;
          want[b] = v[a] * s + v[b] * c;
        }
      }
      expect(maxRelError(got, want, new Float32Array(n).fill(1))).toBeLessThan(1e-6);
    });
  }
});

describe('attention', () => {
  for (const [ctx, pos] of [
    [10, 6],
    [300, 290],
  ]) {
    it(`softmax(QK/√d)·V with grouped KV heads, ${pos + 1} positions`, async () => {
      const { device, k } = await setup();
      const heads = 4;
      const kvHeads = 2;
      const headDim = 16;
      const kvDim = kvHeads * headDim;
      const scale = 1 / Math.sqrt(headDim);
      const q = seeded(heads * headDim, 7);
      const kc = seeded(ctx * kvDim, 8);
      const vc = seeded(ctx * kvDim, 9);
      const out = output(device, heads * headDim);
      const got = await dispatchOnce(
        device,
        k.attention(),
        {
          0: upload(device, q),
          1: upload(device, kc),
          2: upload(device, vc),
          3: out,
          4: output(device, heads * ctx),
          5: uniformBuffer(device, 'p', [
            heads,
            headDim,
            kvDim,
            heads / kvHeads,
            ctx,
            { f32: scale },
          ]),
          6: stepBuffer(device, pos),
        },
        [heads, 1],
        out,
        heads * headDim,
      );

      const want: number[] = [];
      for (let h = 0; h < heads; h++) {
        const g = Math.floor(h / (heads / kvHeads)) * headDim;
        const s: number[] = [];
        for (let t = 0; t <= pos; t++) {
          let d = 0;
          for (let j = 0; j < headDim; j++) d += q[h * headDim + j] * kc[t * kvDim + g + j];
          s.push(d * scale);
        }
        const m = Math.max(...s);
        const e = s.map((x) => Math.exp(x - m));
        const z = e.reduce((a, b) => a + b, 0);
        for (let j = 0; j < headDim; j++) {
          let acc = 0;
          for (let t = 0; t <= pos; t++) acc += e[t] * vc[t * kvDim + g + j];
          want.push(acc / z);
        }
      }
      expect(maxRelError(got, want, new Float32Array(want.length).fill(1))).toBeLessThan(1e-5);
    });
  }
});

describe('elementwise', () => {
  it('swiglu: silu(g) · u', async () => {
    const { device, k } = await setup();
    const n = 300;
    const g = seeded(n, 10).map((v) => v * 6);
    const u = seeded(n, 11);
    const out = output(device, n);
    const got = await dispatchOnce(
      device,
      k.swiglu(),
      { 0: upload(device, g), 1: upload(device, u), 2: out, 3: uniformBuffer(device, 'p', [n]) },
      dispatch1d(n, WG.swiglu, 65535),
      out,
      n,
    );
    const want = Array.from(g, (x, i) => (x / (1 + Math.exp(-x))) * u[i]);
    expect(maxRelError(got, want, new Float32Array(n).fill(1))).toBeLessThan(1e-6);
  });

  it('accumulate: x += d, in place', async () => {
    const { device, k } = await setup();
    const n = 300;
    const x = seeded(n, 12);
    const d = seeded(n, 13);
    const xb = upload(device, x);
    const got = await dispatchOnce(
      device,
      k.accumulate(),
      { 0: xb, 1: upload(device, d), 2: uniformBuffer(device, 'p', [n]) },
      dispatch1d(n, WG.accumulate, 65535),
      xb,
      n,
    );
    expect(Array.from(got)).toEqual(Array.from(x, (v, i) => Math.fround(v + d[i])));
  });
});

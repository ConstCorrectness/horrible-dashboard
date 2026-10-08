import { describe, expect, it } from 'vitest';

import { AddKernel } from '../src/gpu/add';
import { paddedSize, planRowChunks } from '../src/gpu/chunks';
import { gpu, output, run, seeded, upload } from './harness';

function cpuAdd(a: Float32Array, b: Float32Array): Float32Array {
  const out = new Float32Array(a.length);
  for (let i = 0; i < a.length; i++) out[i] = a[i] + b[i];
  return out;
}

describe('add', () => {
  it('matches the CPU exactly, including a partial last workgroup', async () => {
    const { device } = await gpu();
    const n = 1000;
    const a = seeded(n, 1);
    const b = seeded(n, 2);
    const out = output(device, n);
    const add = new AddKernel(device);
    const got = await run(
      device,
      (enc) => add.encode(enc, { a: upload(device, a), b: upload(device, b), out, n }),
      out,
      n,
    );
    expect(got).toEqual(cpuAdd(a, b));
  });

  it('covers every element when the dispatch spills into y', async () => {
    const { device } = await gpu();
    // At most 4 workgroups per dimension: 3089 items need 13 groups → a 4 × 4 grid.
    const n = 256 * 4 * 3 + 17;
    const a = seeded(n, 3);
    const b = seeded(n, 4);
    const out = output(device, n);
    const add = new AddKernel(device, 4);
    const got = await run(
      device,
      (enc) => add.encode(enc, { a: upload(device, a), b: upload(device, b), out, n }),
      out,
      n,
    );
    expect(got).toEqual(cpuAdd(a, b));
  });

  it('gives the same answer over a tensor split into row chunks', async () => {
    const { device } = await gpu();
    // 37 rows of 24 floats under a 1000-byte "binding limit": 10 rows a chunk.
    const rowFloats = 24;
    const rows = 37;
    const a = seeded(rows * rowFloats, 5);
    const b = seeded(rows * rowFloats, 6);
    const chunks = planRowChunks(rows, rowFloats * 4, 1000);
    expect(chunks.length).toBe(4);

    const add = new AddKernel(device);
    const got = new Float32Array(rows * rowFloats);
    for (const c of chunks) {
      const from = c.byteOffset / 4;
      const to = from + c.byteLength / 4;
      expect(paddedSize(c.byteLength)).toBeLessThanOrEqual(1000);
      const out = output(device, c.byteLength / 4);
      const slice = await run(
        device,
        (enc) =>
          add.encode(enc, {
            a: upload(device, a.subarray(from, to)),
            b: upload(device, b.subarray(from, to)),
            out,
            n: c.byteLength / 4,
          }),
        out,
        c.byteLength / 4,
      );
      got.set(slice, from);
    }
    expect(got).toEqual(cpuAdd(a, b));
  });
});

/**
 * Kernel timings at real model shapes, for tuning (6.4): each kernel alone, timed
 * with timestamp queries (best of several runs), reported as GB/s for the
 * bandwidth-bound ones and GFLOP/s for the compute-bound ones. Writes
 * `kernel-tests/.results/perf-<tag>.json`. Opt-in, and only meaningful on a real GPU:
 *
 *   WEBML_GPU=hardware WEBML_PERF=1 WEBML_BENCH_TAG=x \
 *     pnpm --filter @horrible/webml exec vitest run --project kernels perf
 *
 * Shapes are Qwen3-0.6B's: embd 1024, ffn 3072, 16 heads of 128 over 8 KV heads.
 * The inputs are noise; only time is measured (the kernel tests check values).
 */
import { describe, expect, inject, it } from 'vitest';

import { tensorBytes } from '../src/gguf/ggml';
import { Q4_K, Q6_K, Q8_0 } from '../src/gguf/quant';
import { dispatch1d } from '../src/gpu/dispatch';
import {
  ATTN_CHUNK,
  attnPrefillTile,
  Kernels,
  KERNEL_BLOCK,
  MATMUL_TILE,
  uniformBuffer,
  WG,
} from '../src/gpu/kernels';
import {
  gpu,
  output,
  randomWeights,
  report,
  seeded,
  stepBuffer,
  toF16,
  upload,
  uploadBytes,
} from './harness';

declare module 'vitest' {
  interface ProvidedContext {
    perf: boolean;
    perfOnly: string;
  }
}

/** Best-of-`runs` GPU time of one dispatch, in ms. */
async function time(
  device: GPUDevice,
  pipeline: GPUComputePipeline,
  bindings: Record<number, GPUBuffer>,
  workgroups: [number, number],
  runs = 8,
): Promise<number> {
  const group = device.createBindGroup({
    layout: pipeline.getBindGroupLayout(0),
    entries: Object.entries(bindings).map(([b, buffer]) => ({
      binding: Number(b),
      resource: { buffer },
    })),
  });
  const querySet = device.createQuerySet({ type: 'timestamp', count: 2 });
  const resolved = device.createBuffer({
    size: 16,
    usage: GPUBufferUsage.QUERY_RESOLVE | GPUBufferUsage.COPY_SRC,
  });
  const staging = device.createBuffer({
    size: 16,
    usage: GPUBufferUsage.MAP_READ | GPUBufferUsage.COPY_DST,
  });
  let best = Infinity;
  for (let i = 0; i < runs; i++) {
    const encoder = device.createCommandEncoder();
    const pass = encoder.beginComputePass({
      timestampWrites: { querySet, beginningOfPassWriteIndex: 0, endOfPassWriteIndex: 1 },
    });
    pass.setPipeline(pipeline);
    pass.setBindGroup(0, group);
    pass.dispatchWorkgroups(...workgroups);
    pass.end();
    encoder.resolveQuerySet(querySet, 0, 2, resolved, 0);
    encoder.copyBufferToBuffer(resolved, 0, staging, 0, 16);
    device.queue.submit([encoder.finish()]);
    await staging.mapAsync(GPUMapMode.READ);
    const t = new BigUint64Array(staging.getMappedRange().slice(0));
    staging.unmap();
    best = Math.min(best, Number(t[1] - t[0]) / 1e6);
  }
  querySet.destroy();
  return best;
}

describe.skipIf(!inject('perf'))('kernel timings', () => {
  it(
    'matvec, matmul and attention at Qwen3-0.6B shapes',
    async () => {
      const { device } = await gpu();
      const k = new Kernels(device);
      const results: Record<string, unknown> = {};
      const embd = 1024;
      const ffn = 3072;

      for (const [name, type] of [
        ['Q8_0', Q8_0],
        ['Q4_K', Q4_K],
        ['Q6_K', Q6_K],
      ] as const) {
        for (const [rows, cols] of [
          [ffn, embd],
          [embd, ffn],
        ]) {
          const rowBytes = tensorBytes(type, cols)!;
          const ms = await time(
            device,
            k.matvec(type),
            {
              0: uploadBytes(device, randomWeights(type, rows, cols, 1)),
              1: upload(device, seeded(cols, 2)),
              2: output(device, rows),
              3: uniformBuffer(device, 'p', [rows, cols / KERNEL_BLOCK[type].unit, rowBytes, 0, 0]),
              4: stepBuffer(device, 0),
            },
            dispatch1d(rows, WG.matvecRows, 65535),
          );
          results[`matvec ${name} ${rows}x${cols}`] = {
            ms: +ms.toFixed(3),
            GBps: +((rows * rowBytes) / ms / 1e6).toFixed(1),
          };
        }
      }

      for (const [name, type] of [
        ['Q8_0', Q8_0],
        ['Q4_K', Q4_K],
      ] as const) {
        const n = 256;
        const rows = ffn;
        const cols = embd;
        const ms = await time(
          device,
          k.matmul(type),
          {
            0: uploadBytes(device, randomWeights(type, rows, cols, 3)),
            1: upload(device, seeded(n * cols, 4)),
            2: output(device, n * rows),
            3: uniformBuffer(device, 'p', [rows, cols, tensorBytes(type, cols)!, 0, 0, rows]),
            4: stepBuffer(device, 0, n),
          },
          [Math.ceil(rows / MATMUL_TILE.rows), Math.ceil(n / MATMUL_TILE.batch)],
          4,
        );
        results[`matmul ${name} ${rows}x${cols} n=${n}`] = {
          ms: +ms.toFixed(2),
          GFLOPs: +((2 * n * rows * cols) / ms / 1e6).toFixed(1),
        };
      }

      const heads = 16;
      const kvHeads = 8;
      const hd = 128;
      const kvDim = kvHeads * hd;
      const ctx = 4096;
      // Each cache type; f16 is the runtime's default.
      for (const kv of ['f16', 'f32'] as const) {
        const cache = (v: Float32Array) =>
          kv === 'f16'
            ? uploadBytes(device, new Uint8Array(toF16(v).bits.buffer))
            : upload(device, v);
        const kc = cache(seeded(ctx * kvDim, 5));
        const vc = cache(seeded(ctx * kvDim, 6));
        const bytes = kv === 'f16' ? 2 : 4;
        for (const pos of [512, 3700]) {
          const splits = Math.ceil(ctx / ATTN_CHUNK);
          const ms = await time(
            device,
            k.attention(kv, hd, heads / kvHeads),
            {
              0: upload(device, seeded(heads * hd, 7)),
              1: kc,
              2: vc,
              3: output(device, heads * splits * (hd + 2)),
              4: uniformBuffer(device, 'p', [kvDim, splits, { f32: 0.088 }]),
              5: stepBuffer(device, pos),
            },
            [kvHeads, splits],
          );
          results[`attention decode ${kv} pos=${pos}`] = {
            ms: +ms.toFixed(3),
            GBps: +((2 * (pos + 1) * kvDim * bytes) / ms / 1e6).toFixed(1),
          };
        }

        for (const pos of [0, 3000]) {
          const n = 256;
          const tile = attnPrefillTile(hd);
          const ms = await time(
            device,
            k.attnPrefill(hd, kv),
            {
              0: upload(device, seeded(n * heads * hd, 8)),
              1: kc,
              2: vc,
              3: output(device, n * heads * hd),
              4: uniformBuffer(device, 'p', [heads * hd, kvDim, heads / kvHeads, { f32: 0.088 }]),
              5: stepBuffer(device, pos, n),
            },
            [heads, Math.ceil(n / tile)],
            4,
          );
          // QK and PV over the causal triangle: ~2 · 2 · heads · hd · Σ keys.
          const keys = n * pos + (n * (n + 1)) / 2;
          results[`attention prefill ${kv} n=${n} pos=${pos}`] = {
            ms: +ms.toFixed(2),
            GFLOPs: +((4 * heads * hd * keys) / ms / 1e6).toFixed(1),
          };
        }
      }

      const only = inject('perfOnly');
      for (const key of Object.keys(results))
        if (only && !key.startsWith(only)) delete results[key];
      await report(`perf-${inject('benchTag')}`, results);
      expect(Object.keys(results).length).toBeGreaterThan(0);
    },
    10 * 60 * 1000,
  );
});

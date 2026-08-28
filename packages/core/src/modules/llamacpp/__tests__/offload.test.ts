import { describe, expect, it } from 'vitest';

import type { LayerPlan } from '../api';
import { estimateOffload, maxFittingLayers, offloadColumns, VRAM_RESERVE_BYTES } from '../offload';

const GB = 1024 ** 3;

/** Four 1 GB blocks plus a 2 GB embedding/output — deliberately lopsided, because
 *  a model whose output tensors outweigh several blocks is the case that catches a
 *  preview folding them into "layers" (Gemma 4 E4B is genuinely like this). */
function plan(over: Partial<LayerPlan> = {}): LayerPlan {
  return {
    path: 'x.gguf',
    layerCount: 4,
    layerBytes: [GB, GB, GB, GB],
    overheadBytes: 2 * GB,
    totalBytes: 6 * GB,
    kvBytesPerToken: null,
    contextLength: null,
    complete: true,
    error: '',
    ...over,
  };
}

describe('estimateOffload', () => {
  it('offloads the last N blocks, the end llama.cpp starts from', () => {
    // i_gpu_start = n_layer - n_gpu_layers, so 2 layers means blocks 2 and 3.
    const uneven = plan({ layerBytes: [GB, GB, 3 * GB, 5 * GB], totalBytes: 12 * GB });
    expect(estimateOffload(uneven, 2, 0, null).weightsOnGpu).toBe(8 * GB);
  });

  it('leaves the output tensors on the CPU until the count exceeds the blocks', () => {
    expect(estimateOffload(plan(), 4, 0, null).weightsOnGpu).toBe(4 * GB);
    expect(estimateOffload(plan(), 4, 0, null).includesOutput).toBe(false);

    const all = estimateOffload(plan(), 5, 0, null);
    expect(all.includesOutput).toBe(true);
    expect(all.weightsOnGpu).toBe(6 * GB);
    expect(all.weightsOnCpu).toBe(0);
  });

  it('charges KV cache only for the offloaded blocks', () => {
    // 4 blocks, 4 KB per token across all of them → 1 KB per block per token.
    const withKv = plan({ kvBytesPerToken: 4096 });
    expect(estimateOffload(withKv, 2, 1000, null).kvOnGpu).toBe(2 * 1024 * 1000);
    expect(estimateOffload(withKv, 0, 1000, null).kvOnGpu).toBe(0);
  });

  it('counts the KV cache against the card, not just the weights', () => {
    // Weights alone fit; weights + cache do not. Folding the cache into "spare
    // room" is how a preview recommends a layer count that OOMs on load.
    const withKv = plan({ kvBytesPerToken: 4 * GB });
    const vram = 3 * GB + VRAM_RESERVE_BYTES;
    expect(estimateOffload(withKv, 2, 0, vram).fits).toBe(true);
    expect(estimateOffload(withKv, 2, 1, vram).fits).toBe(false);
  });

  it('holds back a reserve, so a full card is not reported as a fit', () => {
    const exact = 4 * GB;
    expect(estimateOffload(plan(), 4, 0, exact).fits).toBe(false);
    expect(estimateOffload(plan(), 4, 0, exact + VRAM_RESERVE_BYTES).fits).toBe(true);
  });

  it('reports unknown VRAM as null rather than as a fit', () => {
    const unknown = estimateOffload(plan(), 4, 0, null);
    expect(unknown.fits).toBeNull();
    expect(unknown.overBy).toBe(0);
  });

  it('clamps a request beyond the stack instead of inventing blocks', () => {
    const huge = estimateOffload(plan(), 999, 0, null);
    expect(huge.layers).toBe(4);
    expect(huge.includesOutput).toBe(true);
    expect(huge.weightsOnGpu).toBe(6 * GB);
  });
});

describe('maxFittingLayers', () => {
  it('returns the largest count that fits', () => {
    // 2 GB of budget holds two 1 GB blocks and no more.
    expect(maxFittingLayers(plan(), 0, 2 * GB + VRAM_RESERVE_BYTES)).toBe(2);
  });

  it('returns layerCount + 1 when everything fits — the value to actually send', () => {
    expect(maxFittingLayers(plan(), 0, 8 * GB + VRAM_RESERVE_BYTES)).toBe(5);
  });

  it('returns 0 when not even one block fits', () => {
    expect(maxFittingLayers(plan(), 0, VRAM_RESERVE_BYTES)).toBe(0);
  });

  it('has no answer without a measured budget', () => {
    expect(maxFittingLayers(plan(), 0, null)).toBeNull();
  });
});

describe('offloadColumns', () => {
  it('returns a column per block plus one for the output tensors', () => {
    const columns = offloadColumns(plan(), 0, 0);
    expect(columns).toHaveLength(5);
    expect(columns.map((c) => c.index)).toEqual([0, 1, 2, 3, -1]);
  });

  /* The rule this file's header is about: llama.cpp offloads from `n_layer -
     n_gpu_layers` upward. Filling from the left would give the same total and mark
     the wrong end of the stack. */
  it('puts the LAST block on the GPU before the first', () => {
    const columns = offloadColumns(plan(), 1, 0);
    expect(columns.find((c) => c.index === 3)?.onGpu).toBe(true);
    expect(columns.find((c) => c.index === 0)?.onGpu).toBe(false);
  });

  it('moves the output tensors only once N exceeds the block count', () => {
    expect(offloadColumns(plan(), 4, 0).find((c) => c.index === -1)?.onGpu).toBe(false);
    expect(offloadColumns(plan(), 5, 0).find((c) => c.index === -1)?.onGpu).toBe(true);
  });

  it('charges KV only to the blocks that are actually offloaded', () => {
    const columns = offloadColumns(plan({ kvBytesPerToken: 4 * 1024 }), 2, 1024);
    expect(columns.find((c) => c.index === 3)?.kvBytes).toBe(1024 * 1024);
    expect(columns.find((c) => c.index === 0)?.kvBytes).toBe(0);
  });

  /* The cumulative series is the whole point of the chart: where it crosses the
     budget is the block at which the model stops fitting. It must therefore
     accumulate in OFFLOAD order even though the columns are returned in block
     order, and it must not depend on where the slider currently sits. */
  it('accumulates from the last block down, regardless of the slider', () => {
    const columns = offloadColumns(plan(), 0, 0);
    const byIndex = new Map(columns.map((c) => [c.index, c.cumulative]));
    expect(byIndex.get(3)).toBe(GB);
    expect(byIndex.get(2)).toBe(2 * GB);
    expect(byIndex.get(0)).toBe(4 * GB);
    // The output tensors are the last thing `-ngl 99` adds, so they cap the series.
    expect(byIndex.get(-1)).toBe(6 * GB);
    expect(offloadColumns(plan(), 4, 0).find((c) => c.index === 2)?.cumulative).toBe(2 * GB);
  });

  /* The chart and the legend must not disagree: the cumulative total at the last
     column the slider has turned on is what `estimateOffload` reports for that N. */
  it('agrees with estimateOffload at every slider position', () => {
    const p = plan({ kvBytesPerToken: 4 * 1024 });
    for (let n = 0; n <= 5; n += 1) {
      const columns = offloadColumns(p, n, 1024);
      const onGpu = columns.filter((c) => c.onGpu);
      const total = onGpu.reduce((sum, c) => sum + c.weightBytes + c.kvBytes, 0);
      expect(total).toBe(estimateOffload(p, n, 1024, null).gpuBytes);
    }
  });
});

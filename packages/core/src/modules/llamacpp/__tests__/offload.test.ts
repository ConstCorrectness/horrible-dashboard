import { describe, expect, it } from 'vitest';

import type { LayerPlan } from '../api';
import { estimateOffload, maxFittingLayers, VRAM_RESERVE_BYTES } from '../offload';

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

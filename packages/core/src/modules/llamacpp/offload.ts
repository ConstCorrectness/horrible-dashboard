/**
 * What `--n-gpu-layers N` actually puts in VRAM. Pure, so it can be tested without
 * a browser — the React side only draws what this returns.
 *
 * Two rules here are llama.cpp's, not ours, and getting either backwards would draw
 * a confident picture of the wrong thing:
 *
 * - **The offloaded blocks are the LAST N.** `llm_load_tensors` computes
 *   `i_gpu_start = n_layer - n_gpu_layers` and puts blocks from there upward on the
 *   device. Taking the first N would give the same total (blocks are near-uniform)
 *   and mark the wrong end of the stack.
 * - **The output tensors only move when N exceeds the block count.** That is what
 *   `-ngl 99` is for, and on a model with a large vocabulary it is most of the file:
 *   Gemma 4 E4B keeps 2.9 GB of its 5.3 GB in the embedding and output head, so a
 *   preview that folded them into "layers" would be wrong by more than half.
 */
import type { LayerPlan } from './api';

export interface OffloadEstimate {
  /** Blocks on the GPU, clamped to what the file has. */
  layers: number;
  /** True when the count also carries the embedding/output tensors. */
  includesOutput: boolean;
  weightsOnGpu: number;
  weightsOnCpu: number;
  /** KV cache for the offloaded blocks at the given context. 0 when unknown. */
  kvOnGpu: number;
  /** What the device is being asked to hold: weights + KV for those blocks. */
  gpuBytes: number;
  /** Device memory to compare against, or null when nothing measured it. */
  vramBytes: number | null;
  /** Null when `vramBytes` is null — "unknown" must not render as "fits". */
  fits: boolean | null;
  /** Bytes over the limit; 0 when it fits or is unknown. */
  overBy: number;
}

/** Bytes of VRAM to leave for compute buffers, the context and the driver.
 *
 * Weights plus KV cache is not the whole allocation, and a preview that filled the
 * card to the last byte would recommend a number that OOMs on load. A flat reserve
 * is crude, but it is honest about being a reserve — the alternative is a fudge
 * factor buried in the total, where nobody can see it. */
export const VRAM_RESERVE_BYTES = 640 * 1024 * 1024;

export function estimateOffload(
  plan: LayerPlan,
  requestedLayers: number,
  contextSize: number,
  vramBytes: number | null,
): OffloadEstimate {
  const count = plan.layerCount;
  const layers = Math.max(0, Math.min(requestedLayers, count));
  const includesOutput = count > 0 && requestedLayers > count;

  const start = count - layers;
  let weightsOnGpu = 0;
  for (let i = start; i < count; i += 1) weightsOnGpu += plan.layerBytes[i] ?? 0;
  if (includesOutput) weightsOnGpu += plan.overheadBytes;

  const weightsOnCpu = Math.max(0, plan.totalBytes - weightsOnGpu);
  // The cache is per block, so only the offloaded ones cost VRAM.
  const kvPerLayer = plan.kvBytesPerToken && count ? plan.kvBytesPerToken / count : 0;
  const kvOnGpu = Math.round(kvPerLayer * layers * Math.max(0, contextSize));

  const gpuBytes = weightsOnGpu + kvOnGpu;
  const budget = vramBytes === null ? null : Math.max(0, vramBytes - VRAM_RESERVE_BYTES);
  return {
    layers,
    includesOutput,
    weightsOnGpu,
    weightsOnCpu,
    kvOnGpu,
    gpuBytes,
    vramBytes,
    fits: budget === null ? null : gpuBytes <= budget,
    overBy: budget === null ? 0 : Math.max(0, gpuBytes - budget),
  };
}

/**
 * The most blocks that still fit, or null when nothing measured the VRAM.
 *
 * Returns `layerCount + 1` when even the output tensors fit, because that is the
 * value to *send* — it is what tells llama.cpp to take everything.
 */
export function maxFittingLayers(
  plan: LayerPlan,
  contextSize: number,
  vramBytes: number | null,
): number | null {
  if (vramBytes === null || !plan.layerCount) return null;
  for (let n = plan.layerCount + 1; n >= 0; n -= 1) {
    if (estimateOffload(plan, n, contextSize, vramBytes).fits) return n;
  }
  return 0;
}

/** One decoder block as the planner draws it. */
export interface OffloadColumn {
  /** Block index, or -1 for the embedding/output overhead column. */
  index: number;
  weightBytes: number;
  /** KV cache this block costs at the given context, 0 when it stays on the CPU. */
  kvBytes: number;
  onGpu: boolean;
  /**
   * Everything the GPU holds up to and including this column, walking the stack in
   * the order llama.cpp offloads it — from the LAST block downward.
   *
   * This is the series that turns "does it fit" from a verdict word into a crossing
   * point: where the running total meets the budget is exactly the block at which
   * it stops fitting, which is a thing you can see and a number you can act on.
   */
  cumulative: number;
}

/**
 * The stack as columns, in offload order.
 *
 * `estimateOffload` answers "what does N cost"; this answers "what would each next
 * one cost", which is the question the slider is actually asking on every drag.
 *
 * Returned in **block order** (0 first) because that is how the stack reads on
 * screen, while `cumulative` is accumulated in **offload order** (last block
 * first). Conflating the two is the bug this comment exists to prevent: filling
 * the columns from the left would draw the first N blocks as the offloaded ones,
 * which is the wrong end of the stack — see this file's header.
 */
export function offloadColumns(
  plan: LayerPlan,
  requestedLayers: number,
  contextSize: number,
): OffloadColumn[] {
  const count = plan.layerCount;
  const layers = Math.max(0, Math.min(requestedLayers, count));
  const includesOutput = count > 0 && requestedLayers > count;
  const start = count - layers;
  const kvPerLayer = plan.kvBytesPerToken && count ? plan.kvBytesPerToken / count : 0;
  const kvEach = Math.round(kvPerLayer * Math.max(0, contextSize));

  const blocks: OffloadColumn[] = [];
  for (let i = 0; i < count; i += 1) {
    const onGpu = i >= start;
    blocks.push({
      index: i,
      weightBytes: plan.layerBytes[i] ?? 0,
      kvBytes: onGpu ? kvEach : 0,
      onGpu,
      cumulative: 0,
    });
  }

  // The embedding and output head. Their own column, not folded into "layers":
  // Gemma 4 E4B keeps 2.9 GB of its 5.3 GB here, so a picture that hid them would
  // be wrong by more than half — and they only move when N exceeds the block count.
  const overhead: OffloadColumn = {
    index: -1,
    weightBytes: plan.overheadBytes,
    kvBytes: 0,
    onGpu: includesOutput,
    cumulative: 0,
  };

  // Accumulate in offload order: the last block first, the output tensors last
  // (they are the final thing `-ngl 99` adds).
  let running = 0;
  for (let i = count - 1; i >= 0; i -= 1) {
    const column = blocks[i];
    running += column.weightBytes + column.kvBytes;
    column.cumulative = running;
  }
  overhead.cumulative = running + overhead.weightBytes;

  return [...blocks, overhead];
}

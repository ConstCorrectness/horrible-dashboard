/**
 * The stack, block by block, with the VRAM ceiling drawn across it.
 *
 * The preview used to collapse `LayerPlan.layerBytes` — the file's real per-block
 * tensor sizes — into three aggregate widths. Two facts died in that collapse and
 * both are things `offload.ts` goes out of its way to explain in prose:
 *
 * - **The offloaded blocks are the LAST N.** With one bar there is nowhere for that
 *   to show; with columns, dragging the slider fills from the right and the rule
 *   teaches itself without a sentence.
 * - **The embedding and output head are their own column.** They are 2.9 GB of
 *   Gemma 4 E4B's 5.3 GB and they only move once the count exceeds the block count,
 *   which is what `-ngl 99` is for.
 *
 * The ceiling is a **cumulative line**, not a verdict: the running total walks the
 * stack in offload order, and where it crosses the budget is the exact block at
 * which the model stops fitting. "Does it fit" becomes a position you can read off
 * rather than a word you have to trust.
 *
 * Honesty rules inherited from `OffloadPreview`: an unmeasured budget draws **no**
 * ceiling and no crossing, because a line at an invented position is worse than no
 * line at all.
 */
import { useMemo } from 'react';

import { formatBytes, type LayerPlan } from './api';
import { offloadColumns } from './offload';

export function LayerColumns({
  plan,
  layers,
  contextSize,
  budgetBytes,
  onPick,
}: {
  plan: LayerPlan;
  /** The effective GPU-layer count, as `--n-gpu-layers` would receive it. */
  layers: number;
  contextSize: number;
  /** VRAM less the compute reserve, or null when nothing measured it. */
  budgetBytes: number | null;
  /** Clicking a column offloads from that block up — the chart is the control. */
  onPick: (layers: number) => void;
}) {
  const columns = useMemo(
    () => offloadColumns(plan, layers, contextSize),
    [plan, layers, contextSize],
  );

  // The tallest single column sets the height scale. The cumulative line has its
  // own scale (it ends at the whole model), so the two are drawn against different
  // axes on purpose — one is "how big is this block", the other "how much so far".
  const tallest = columns.reduce((max, c) => Math.max(max, c.weightBytes + c.kvBytes), 0) || 1;
  const totalBytes = columns[columns.length - 1]?.cumulative || 1;

  /**
   * Where the budget sits on the cumulative axis, as a fraction of full height.
   *
   * `null` in **two** cases, and the second one is easy to get wrong: nothing
   * measured the VRAM, or the card is bigger than the whole model. Clamping the
   * second to 100% draws a line across the top of the chart, which reads as "this
   * exactly fills the card" — an assertion of a crossing that does not exist. A
   * model that fits entirely has no crossing point, and the honest drawing of no
   * crossing is no line.
   */
  const roomToSpare = budgetBytes !== null && budgetBytes >= totalBytes;
  const ceiling =
    budgetBytes === null || roomToSpare ? null : Math.min(1, budgetBytes / totalBytes);

  return (
    <div className="llama-cols-wrap">
      <div
        className="llama-cols"
        role="img"
        aria-label={
          `${plan.layerCount} decoder blocks, ` +
          `${columns.filter((c) => c.onGpu && c.index >= 0).length} of them on the GPU` +
          (budgetBytes === null ? ', no measured VRAM to compare against' : '')
        }
      >
        {ceiling !== null && (
          <div
            className="llama-cols-ceiling"
            style={{ bottom: `${ceiling * 100}%` }}
            title={`${formatBytes(budgetBytes ?? 0)} of usable VRAM — the cumulative line crosses here`}
          >
            <span>VRAM</span>
          </div>
        )}

        {columns.map((column) => {
          const height = ((column.weightBytes + column.kvBytes) / tallest) * 100;
          const kvShare = column.kvBytes / (column.weightBytes + column.kvBytes || 1);
          const overhead = column.index < 0;
          // Clicking block i asks for every block from i upward — which is exactly
          // what `--n-gpu-layers (count - i)` means.
          const request = overhead ? plan.layerCount + 1 : plan.layerCount - column.index;
          return (
            <button
              key={column.index}
              type="button"
              className={`llama-col${column.onGpu ? ' llama-col-gpu' : ''}${
                overhead ? ' llama-col-overhead' : ''
              }`}
              style={{ height: `${Math.max(2, height)}%` }}
              onClick={() => onPick(request)}
              title={
                (overhead
                  ? `Embeddings + output head — ${formatBytes(column.weightBytes)}. Only moves when the count exceeds ${plan.layerCount}.`
                  : `Block ${column.index} — ${formatBytes(column.weightBytes)}` +
                    (column.kvBytes ? ` + ${formatBytes(column.kvBytes)} KV` : '')) +
                `\n${formatBytes(column.cumulative)} on the GPU up to here` +
                `\nClick to offload ${request > plan.layerCount ? 'everything' : `${request} layers`}`
              }
            >
              {column.kvBytes > 0 && (
                <span className="llama-col-kv" style={{ height: `${kvShare * 100}%` }} />
              )}
            </button>
          );
        })}
      </div>

      <div className="llama-cols-axis">
        <span>block 0</span>
        <span className="llama-cols-axis-mid">
          {/* Which way the fill travels is the one thing a static picture cannot
              say. It travels RIGHT to LEFT: llama.cpp offloads from
              `n_layer - n_gpu_layers` upward, so the last block goes first. */}
          ← the GPU fills from this end
        </span>
        <span>{plan.layerCount - 1}</span>
        <span className="llama-cols-axis-out">emb + out</span>
      </div>

      {roomToSpare && (
        <p className="llama-why">
          The whole file is {formatBytes(totalBytes)} and there is{' '}
          {formatBytes(budgetBytes ?? 0)} of usable VRAM, so every block fits and there is no
          ceiling to draw.
        </p>
      )}
    </div>
  );
}

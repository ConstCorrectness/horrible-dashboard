import { useEffect, useState } from 'react';

import { formatBytes, getLayerPlan, type LayerPlan } from './api';
import { estimateOffload, maxFittingLayers, VRAM_RESERVE_BYTES } from './offload';

/**
 * The GPU-layers control, drawn against the card it has to fit in.
 *
 * "GPU layers" was a number typed blind — the pane knew the card had 12 GB and the
 * file was 5 GB, and answered neither "how many fit?" nor "why not more?". Every
 * input was already on disk: the GGUF's tensor directory carries a size and a block
 * index per tensor. This turns the number into a slider over a picture of the stack
 * with the VRAM ceiling drawn on it.
 *
 * The honesty rules it inherits:
 *
 * - **Unknown VRAM shows no ceiling.** A card that reports no memory size, or a probe
 *   that could not ask, gets the stack drawn and no verdict — rendering "fits" from
 *   an unmeasured budget is the failure the hardware module exists to prevent.
 * - **Unified memory is not a separate budget.** On Apple silicon the "VRAM" is the
 *   machine's RAM, so offloading moves nothing; the line is omitted and said so.
 * - **The reserve is visible.** Weights + KV is not the whole allocation, so a
 *   reserve is held back for compute buffers — stated in the legend rather than
 *   hidden inside the total.
 */

function gb(bytes: number): string {
  return formatBytes(bytes);
}

export function OffloadPreview({
  modelPath,
  contextSize,
  layers,
  vramMb,
  unified,
  onChange,
  onAuto,
  isAuto,
}: {
  modelPath: string;
  contextSize: number;
  /** The effective count — the probe's answer when the field is on auto. */
  layers: number;
  vramMb: number | null;
  unified: boolean;
  onChange: (layers: number) => void;
  onAuto: () => void;
  isAuto: boolean;
}) {
  const [plan, setPlan] = useState<LayerPlan | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!modelPath) return;
    let alive = true;
    setPlan(null);
    setError('');
    void getLayerPlan(modelPath)
      .then((next) => {
        if (!alive) return;
        if (next.error) setError(next.error);
        else setPlan(next);
      })
      .catch((exc: unknown) => alive && setError(exc instanceof Error ? exc.message : String(exc)));
    return () => {
      alive = false;
    };
  }, [modelPath]);

  if (error) return <p className="llama-note">No layer map for this file: {error}</p>;
  if (!plan || !plan.layerCount) return null;

  // Unified memory is the machine's RAM: there is no second budget to fit inside,
  // so there is no ceiling to draw and offloading is not a size question.
  const vramBytes = unified || vramMb === null ? null : vramMb * 1024 * 1024;
  const max = plan.layerCount + 1;
  const estimate = estimateOffload(plan, layers, contextSize, vramBytes);
  const best = maxFittingLayers(plan, contextSize, vramBytes);

  // The bar is scaled so the ceiling is always on it, even when the model dwarfs
  // the card — otherwise an 80 GB model on a 12 GB card draws a full bar and a line
  // pinned at the left edge, which reads as "nearly fits".
  const scale = Math.max(plan.totalBytes + estimate.kvOnGpu, vramBytes ?? 0);
  const pctOf = (bytes: number) => `${Math.min(100, (bytes / scale) * 100)}%`;
  const budget = vramBytes === null ? null : Math.max(0, vramBytes - VRAM_RESERVE_BYTES);

  return (
    <div className="llama-offload">
      <div className="llama-row llama-offload-head">
        <label className="llama-offload-slider">
          GPU layers
          <input
            type="range"
            min={0}
            max={max}
            value={estimate.includesOutput ? max : estimate.layers}
            onChange={(e) => onChange(e.target.valueAsNumber)}
          />
        </label>
        <span className="llama-offload-count">
          {estimate.includesOutput ? `all ${plan.layerCount}` : `${estimate.layers}`} /{' '}
          {plan.layerCount}
          {estimate.includesOutput ? ' + output' : ''}
        </span>
        {!isAuto && (
          <button className="llama-linkbtn" onClick={onAuto}>
            Auto
          </button>
        )}
      </div>

      <div
        className={`llama-offload-bar${estimate.fits === false ? ' llama-offload-over' : ''}`}
        role="img"
        aria-label={`${estimate.layers} of ${plan.layerCount} layers on the GPU`}
      >
        <div className="llama-offload-gpu" style={{ width: pctOf(estimate.weightsOnGpu) }} />
        <div className="llama-offload-kv" style={{ width: pctOf(estimate.kvOnGpu) }} />
        <div className="llama-offload-cpu" style={{ width: pctOf(estimate.weightsOnCpu) }} />
        {budget !== null && (
          <div
            className="llama-offload-limit"
            style={{ left: pctOf(budget) }}
            title={`${gb(vramBytes ?? 0)} VRAM less a ${gb(VRAM_RESERVE_BYTES)} reserve for compute buffers`}
          />
        )}
      </div>

      <p className="llama-why">
        <b>{gb(estimate.weightsOnGpu)}</b> of weights
        {estimate.kvOnGpu > 0 && (
          <>
            {' + '}
            <b>{gb(estimate.kvOnGpu)}</b> KV cache at {contextSize.toLocaleString()} ctx
          </>
        )}{' '}
        on the GPU, {gb(estimate.weightsOnCpu)} left in RAM.
        {estimate.fits === false && (
          <span className="llama-offload-warn"> Over the card by {gb(estimate.overBy)}.</span>
        )}
        {vramBytes === null && (
          <>
            {' '}
            {unified
              ? 'Unified memory — the GPU shares the machine’s RAM, so offloading copies nothing.'
              : 'No measured VRAM to compare against, so nothing here says whether it fits.'}
          </>
        )}
        {best !== null && best !== (estimate.includesOutput ? max : estimate.layers) && (
          <>
            {' '}
            <button className="llama-linkbtn llama-inline" onClick={() => onChange(best)}>
              Fit it: {best > plan.layerCount ? 'all layers' : `${best} layers`}
            </button>
          </>
        )}
        {!plan.complete && ' Some tensors use a quantization we cannot size, so these are floors.'}
      </p>
    </div>
  );
}

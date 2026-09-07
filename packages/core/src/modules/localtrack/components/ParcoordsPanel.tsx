import { useEffect, useMemo, useRef, useState } from 'react';

import { chartColors, subscribeThemeColors } from '../../../viz';
import { compareRuns } from '../api';
import type { CompareResult } from '../types';

/**
 * Parallel coordinates: every run as one line across the axes that varied.
 *
 * The table answers "which run won". This answers the shape question the table
 * cannot: whether the metric moves *monotonically* with a knob, whether two knobs
 * interact, and whether the best run is a genuine trend or one lucky point. Six
 * rows of numbers hide all three.
 *
 * Canvas rather than SVG, in the style of `viz/HeatCanvas`: a sweep is at most a
 * few dozen lines today, but the same panel over an imported W&B project is
 * hundreds, and hundreds of SVG paths with per-path listeners is where a chart
 * pane starts dropping frames.
 *
 * Colour encodes the *metric*, not the run, because "which of these lines is
 * good" is the question — a categorical palette would make you cross-reference a
 * legend for every line.
 */

const dim = { color: 'var(--text-dim)' } as const;

interface AxisSpec {
  key: string;
  /** Numeric axes scale; categorical ones are laid out by distinct value, since
   * "cosine" has no position between "linear" and "constant". */
  numeric: boolean;
  values: unknown[];
  min: number;
  max: number;
}

function buildAxes(result: CompareResult, metric: string): AxisSpec[] {
  const keys = [...result.varied, metric];
  return keys.map((key) => {
    const raw = result.runs.map((row) => (key === metric ? row.metrics[metric] : row.config[key]));
    const numbers = raw.filter((v): v is number => typeof v === 'number');
    const numeric = numbers.length === raw.length && numbers.length > 0;
    const distinct = [...new Set(raw.map((v) => JSON.stringify(v)))].map(
      (v) => JSON.parse(v) as unknown,
    );
    return {
      key,
      numeric,
      values: distinct,
      min: numeric ? Math.min(...numbers) : 0,
      max: numeric ? Math.max(...numbers) : Math.max(1, distinct.length - 1),
    };
  });
}

function position(axis: AxisSpec, value: unknown): number | null {
  if (value === undefined || value === null) return null;
  if (axis.numeric && typeof value === 'number') {
    if (axis.max === axis.min) return 0.5;
    return (value - axis.min) / (axis.max - axis.min);
  }
  const index = axis.values.findIndex((v) => JSON.stringify(v) === JSON.stringify(value));
  if (index < 0) return null;
  return axis.values.length <= 1 ? 0.5 : index / (axis.values.length - 1);
}

export function ParcoordsPanel({ runIds, metricKey }: { runIds: string[]; metricKey?: string }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [result, setResult] = useState<CompareResult | null>(null);
  const [colors, setColors] = useState(chartColors());
  const [error, setError] = useState('');

  useEffect(() => subscribeThemeColors(setColors), []);

  useEffect(() => {
    if (!runIds.length) {
      setResult(null);
      return;
    }
    compareRuns(runIds, metricKey || '')
      .then((r) => {
        setResult(r);
        setError('');
      })
      .catch((e: Error) => setError(e.message));
  }, [runIds, metricKey]);

  const metric = metricKey || result?.metric_keys[0] || '';
  const axes = useMemo(() => (result && metric ? buildAxes(result, metric) : []), [result, metric]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !result || axes.length < 2) return;
    const ratio = window.devicePixelRatio || 1;
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    canvas.width = Math.max(1, Math.round(width * ratio));
    canvas.height = Math.max(1, Math.round(height * ratio));
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, height);

    const padX = 34;
    const padTop = 8;
    const padBottom = 22;
    const usableW = Math.max(1, width - padX * 2);
    const usableH = Math.max(1, height - padTop - padBottom);
    const x = (i: number) => padX + (usableW * i) / Math.max(1, axes.length - 1);
    // Inverted: 0 at the bottom reads as "less", which is what a value axis means.
    const y = (t: number) => padTop + usableH * (1 - t);

    ctx.strokeStyle = colors.grid;
    ctx.lineWidth = 1;
    for (let i = 0; i < axes.length; i += 1) {
      ctx.beginPath();
      ctx.moveTo(x(i), padTop);
      ctx.lineTo(x(i), padTop + usableH);
      ctx.stroke();
    }

    const metricAxis = axes[axes.length - 1];
    const span = metricAxis.max - metricAxis.min || 1;
    for (const row of result.runs) {
      const points = axes.map((axis) =>
        position(axis, axis.key === metric ? row.metrics[metric] : row.config[axis.key]),
      );
      if (points.some((p) => p === null)) continue;
      const score = ((row.metrics[metric] ?? metricAxis.min) - metricAxis.min) / span;
      // Warm = high on the metric, cool = low. Deliberately not a run-colour
      // palette: the question is which lines are good, not which line is run 7.
      ctx.strokeStyle = `hsl(${Math.round(210 - score * 190)} 70% 60%)`;
      ctx.lineWidth = 1.5;
      ctx.globalAlpha = 0.85;
      ctx.beginPath();
      points.forEach((t, i) => {
        const px = x(i);
        const py = y(t as number);
        if (i === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      });
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
  }, [result, axes, colors, metric]);

  if (error) return <div style={{ color: 'var(--danger)', fontSize: '0.75rem' }}>{error}</div>;
  if (!result || result.runs.length < 2 || axes.length < 2) {
    return (
      <div style={{ ...dim, fontSize: '0.75rem', padding: '0.5rem' }}>
        Select two or more runs that varied at least one setting. A sweep produces exactly that.
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <canvas ref={canvasRef} style={{ width: '100%', flex: 1, minHeight: 120 }} />
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          padding: '0 0.3rem',
          fontFamily: 'var(--font-mono, monospace)',
          fontSize: '0.62rem',
          ...dim,
        }}
      >
        {axes.map((axis) => (
          <span key={axis.key} style={{ flex: 1, textAlign: 'center', overflow: 'hidden' }}>
            {axis.key}
          </span>
        ))}
      </div>
    </div>
  );
}

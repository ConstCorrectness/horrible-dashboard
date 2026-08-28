/**
 * A 2-D diverging heat grid, drawn to a canvas.
 *
 * A canvas rather than DOM cells because the sizes involved are not negotiable: an
 * attention matrix over 512 tokens is 262,144 cells, and 262,144 `<span>`s is not a
 * layout a browser will do at an interactive frame rate. One `putImageData` at the
 * grid's native size, scaled up by CSS, is O(cells) once.
 *
 * ## This is NOT a `CANVAS_EXEMPT` case
 *
 * `no-hex-literals.test.ts` keeps a small bucket of files allowed to hold literal
 * colours because "a `CanvasRenderingContext2D` cannot resolve a CSS custom
 * property". True — but the excuse only covers a canvas that has no way to *ask*.
 * This one reads its ramp endpoints through `getComputedStyle` and repaints when the
 * theme attribute changes, so it stays themed and stays out of that bucket. The
 * `MetricsPane` bug is the cautionary case: it passed `var(--accent)` straight into
 * uPlot, `ctx.strokeStyle` ignored it silently, and the chart was drawn in a colour
 * nobody chose.
 */
import { useEffect, useRef, useState } from 'react';

import './viz.css';
import { subscribeThemeColors } from './uplot-theme';

export interface HeatCanvasProps {
  /** Row-major, `rows * cols` long. `null` is "not measured" and is left blank. */
  data: (number | null)[];
  rows: number;
  cols: number;
  /** Value mapping to full strength. Per-row scaling belongs to the caller. */
  max?: number;
  label: string;
  /** Called with `null` when the pointer leaves. */
  onHover?: (cell: { row: number; col: number; value: number | null } | null) => void;
  /** Drawn height in CSS pixels. Width always fills the container. */
  height?: number;
}

/** Grey. What an unresolvable token gets, so the failure is visible. */
const UNRESOLVED: [number, number, number] = [128, 128, 128];

function parseRgb(colour: string): [number, number, number] {
  // `getComputedStyle` normalizes a custom property's value only when it is used;
  // read directly it can come back as the author wrote it, so both forms are handled.
  if (!colour.trim()) return UNRESOLVED;
  const hex = /^#([0-9a-f]{6})$/i.exec(colour.trim());
  if (hex) {
    const n = parseInt(hex[1], 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  const rgb = colour.match(/-?\d+(\.\d+)?/g);
  if (rgb && rgb.length >= 3) return [+rgb[0], +rgb[1], +rgb[2]];
  return UNRESOLVED;
}

export function HeatCanvas({
  data,
  rows,
  cols,
  max,
  label,
  onHover,
  height = 180,
}: HeatCanvasProps) {
  const ref = useRef<HTMLCanvasElement>(null);
  const [themeTick, setThemeTick] = useState(0);

  useEffect(() => subscribeThemeColors(() => setThemeTick((n) => n + 1)), []);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas || rows <= 0 || cols <= 0) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const root = canvas.ownerDocument.documentElement;
    const style = getComputedStyle(root);
    // No hex fallback on purpose. A plausible-looking default here would hide a
    // missing token — which is the exact failure `design-tokens.test.ts` exists to
    // catch — so an unresolvable token falls through to `parseRgb`'s grey and the
    // grid looks obviously unstyled rather than subtly wrong.
    const pos = parseRgb(style.getPropertyValue('--ramp-pos'));
    const neg = parseRgb(style.getPropertyValue('--ramp-neg'));

    const scale =
      max ?? data.reduce<number>((m, v) => (v === null ? m : Math.max(m, Math.abs(v))), 0);

    canvas.width = cols;
    canvas.height = rows;
    const image = ctx.createImageData(cols, rows);
    for (let i = 0; i < rows * cols; i += 1) {
      const value = data[i];
      const o = i * 4;
      if (value === null || value === undefined || !scale) {
        // Not measured. Left fully transparent so the surface behind shows
        // through — a blank cell and a zero cell must not look the same.
        image.data[o + 3] = 0;
        continue;
      }
      const t = Math.max(-1, Math.min(1, value / scale));
      const [r, g, b] = t >= 0 ? pos : neg;
      image.data[o] = r;
      image.data[o + 1] = g;
      image.data[o + 2] = b;
      image.data[o + 3] = Math.round((0.1 + Math.abs(t) * 0.9) * 255);
    }
    ctx.putImageData(image, 0, 0);
  }, [data, rows, cols, max, themeTick]);

  return (
    <canvas
      ref={ref}
      className="viz-heat"
      style={{ height }}
      role="img"
      aria-label={label}
      onMouseMove={
        onHover
          ? (event) => {
              const rect = event.currentTarget.getBoundingClientRect();
              const col = Math.floor(((event.clientX - rect.left) / rect.width) * cols);
              const row = Math.floor(((event.clientY - rect.top) / rect.height) * rows);
              if (row < 0 || col < 0 || row >= rows || col >= cols) return onHover(null);
              onHover({ row, col, value: data[row * cols + col] ?? null });
            }
          : undefined
      }
      onMouseLeave={onHover ? () => onHover(null) : undefined}
    />
  );
}

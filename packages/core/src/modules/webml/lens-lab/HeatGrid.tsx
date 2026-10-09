/**
 * A layer × token heatmap on a canvas: layer 0 at the top, as in the model
 * explorer's diagram (embedding above, LM head below), one column per token.
 *
 * Canvas rather than DOM because a long reply is 512 tokens × 36 layers — eighteen
 * thousand cells — and this redraws on every hover. The cells carry no text; the
 * tooltip and the detail beside the grid do.
 *
 * Colour comes from the caller as a value in [-1, 1] per cell: sequential grids
 * pass [0, 1] (the theme accent, stronger with the value), diverging ones the full
 * range (accent above zero, `--danger` below, the surface at zero). Identity is
 * never colour alone: a marked cell gets an outline, a settle layer a tick.
 */
import { useEffect, useRef, useState, type ReactNode } from 'react';

export interface HeatGridProps {
  rows: number;
  cols: number;
  /** In [-1, 1]; NaN draws an empty (masked) cell. */
  value: (row: number, col: number) => number;
  /** Cells to outline (e.g. "this is the token finally chosen"). */
  marked?: (row: number, col: number) => boolean;
  /** Per column, the row to tick on the left edge of the cell (a settle layer). */
  tick?: (col: number) => number | null;
  selectedCol?: number | null;
  selectedRow?: number | null;
  onPickCol?: (col: number) => void;
  onPickRow?: (row: number, col: number) => void;
  /** Tooltip content for a hovered cell. */
  tip?: (row: number, col: number) => ReactNode;
  /** Pixel size of a cell; the grid scrolls horizontally past its width. */
  cell?: { w: number; h: number };
  label: string;
}

function token(el: Element, name: string, fallback: string): string {
  return getComputedStyle(el).getPropertyValue(name).trim() || fallback;
}

export function HeatGrid({
  rows,
  cols,
  value,
  marked,
  tick,
  selectedCol,
  selectedRow,
  onPickCol,
  onPickRow,
  tip,
  cell = { w: 10, h: 8 },
  label,
}: HeatGridProps) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [hover, setHover] = useState<{ row: number; col: number; x: number; y: number } | null>(
    null,
  );
  const width = cols * cell.w;
  const height = rows * cell.h;

  useEffect(() => {
    const el = canvas.current;
    const ctx = el?.getContext('2d');
    if (!el || !ctx) return;
    const dpr = window.devicePixelRatio || 1;
    el.width = Math.max(1, Math.round(width * dpr));
    el.height = Math.max(1, Math.round(height * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const surface = token(el, '--bg-raised', '#1d2026');
    const positive = token(el, '--accent', '#6ea8fe');
    const negative = token(el, '--danger', '#e06c75');
    const ink = token(el, '--text-strong', '#ffffff');
    const muted = token(el, '--border', '#2e333d');
    ctx.fillStyle = surface;
    ctx.fillRect(0, 0, width, height);
    // A 1px surface gap between cells, so neighbours of equal value stay cells.
    const gap = cell.w > 4 ? 1 : 0;
    for (let c = 0; c < cols; c++) {
      for (let r = 0; r < rows; r++) {
        const v = value(r, c);
        const x = c * cell.w;
        const y = r * cell.h;
        if (Number.isNaN(v)) {
          ctx.globalAlpha = 1;
          ctx.fillStyle = muted;
          ctx.fillRect(x, y, cell.w - gap, cell.h - gap);
          continue;
        }
        if (v === 0) continue;
        ctx.globalAlpha = Math.min(1, 0.08 + Math.abs(v) * 0.92);
        ctx.fillStyle = v > 0 ? positive : negative;
        ctx.fillRect(x, y, cell.w - gap, cell.h - gap);
      }
    }
    ctx.globalAlpha = 1;
    if (marked) {
      ctx.strokeStyle = ink;
      ctx.lineWidth = 1;
      for (let c = 0; c < cols; c++)
        for (let r = 0; r < rows; r++)
          if (marked(r, c))
            ctx.strokeRect(c * cell.w + 0.5, r * cell.h + 0.5, cell.w - gap - 1, cell.h - gap - 1);
    }
    if (tick) {
      ctx.fillStyle = ink;
      for (let c = 0; c < cols; c++) {
        const r = tick(c);
        if (r !== null) ctx.fillRect(c * cell.w, r * cell.h, Math.max(2, cell.w / 4), cell.h - gap);
      }
    }
  }, [rows, cols, value, marked, tick, width, height, cell.w, cell.h]);

  const at = (e: React.MouseEvent) => {
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const col = Math.floor((e.clientX - rect.left) / cell.w);
    const row = Math.floor((e.clientY - rect.top) / cell.h);
    if (col < 0 || col >= cols || row < 0 || row >= rows) return null;
    return { row, col, x: e.clientX - rect.left, y: e.clientY - rect.top };
  };

  return (
    <div className="ll-heat" role="img" aria-label={label}>
      <div className="ll-heat-axis" style={{ height }} aria-hidden="true">
        <span>layer 0</span>
        {rows > 1 && <span>{rows - 1}</span>}
      </div>
      <div className="ll-heat-plot" style={{ width, height }}>
        <canvas
          ref={canvas}
          style={{ width, height }}
          onPointerMove={(e) => setHover(at(e))}
          onPointerLeave={() => setHover(null)}
          onClick={(e) => {
            const hit = at(e);
            if (!hit) return;
            onPickCol?.(hit.col);
            onPickRow?.(hit.row, hit.col);
          }}
        />
        {selectedCol != null && selectedCol < cols && (
          <div
            className="ll-heat-col"
            style={{ left: selectedCol * cell.w - 1, width: cell.w + 1, height }}
          />
        )}
        {selectedRow != null && selectedRow < rows && (
          <div
            className="ll-heat-row"
            style={{ top: selectedRow * cell.h - 1, height: cell.h + 1, width }}
          />
        )}
        {hover && tip && (
          <div
            className="ll-tip"
            style={{
              left: Math.min(hover.x + 12, Math.max(0, width - 220)),
              top: hover.y + 14,
            }}
          >
            {tip(hover.row, hover.col)}
          </div>
        )}
      </div>
    </div>
  );
}

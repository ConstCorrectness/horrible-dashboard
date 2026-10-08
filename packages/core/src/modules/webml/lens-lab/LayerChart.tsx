/**
 * One measure across the layers of the stack: a residual norm, an entropy, a
 * settle layer across positions. A thin line, one axis, a recessive baseline, and
 * a crosshair with the value under the pointer. Two measures of different scale
 * are two of these, never one chart with two axes.
 */
import { useLayoutEffect, useRef, useState } from 'react';

export interface Series {
  label: string;
  values: (number | null)[];
  /** `accent` or `danger`: the two hues the lab uses, A and B in a comparison. */
  tone: 'accent' | 'danger';
}

export function LayerChart({
  title,
  series,
  xLabel,
  format = (v) => v.toFixed(2),
  marker,
  height = 120,
}: {
  title: string;
  series: Series[];
  /** What the x axis counts ("layer", "position"). */
  xLabel: string;
  format?: (v: number) => string;
  /** An x to mark with a vertical rule (the settle layer). */
  marker?: { x: number; label: string } | null;
  height?: number;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const n = Math.max(0, ...series.map((s) => s.values.length));
  const all = series.flatMap((s) => s.values.filter((v): v is number => v !== null));
  const drawn = n > 0 && all.length > 0;
  // Drawn at its real pixel width, so text and strokes stay their true size
  // instead of scaling with a stretched viewBox.
  const box = useRef<HTMLElement>(null);
  const [width, setWidth] = useState(320);
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const measure = () => setWidth(Math.max(160, Math.round(el.clientWidth - 20)));
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [drawn]);
  if (!drawn) return null;
  const pad = { l: 34, r: 8, t: 8, b: 18 };
  const lo = Math.min(0, ...all);
  const hi = Math.max(...all) || 1;
  const x = (i: number) => pad.l + (n === 1 ? 0 : (i / (n - 1)) * (width - pad.l - pad.r));
  const y = (v: number) => pad.t + (1 - (v - lo) / (hi - lo || 1)) * (height - pad.t - pad.b);
  const path = (values: (number | null)[]) => {
    let d = '';
    let pen = false;
    values.forEach((v, i) => {
      if (v === null) {
        pen = false;
        return;
      }
      d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
      pen = true;
    });
    return d;
  };
  return (
    <figure className="ll-chart" ref={box}>
      <figcaption>
        <span>{title}</span>
        {series.length > 1 && (
          <span className="ll-legend">
            {series.map((s) => (
              <span key={s.label} className={`ll-key ll-key--${s.tone}`}>
                {s.label}
              </span>
            ))}
          </span>
        )}
      </figcaption>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        width={width}
        height={height}
        role="img"
        aria-label={`${title} by ${xLabel}`}
        onPointerMove={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          const fx = ((e.clientX - rect.left) / rect.width) * width;
          const i = Math.round(((fx - pad.l) / (width - pad.l - pad.r)) * (n - 1));
          setHover(i >= 0 && i < n ? i : null);
        }}
        onPointerLeave={() => setHover(null)}
      >
        <line className="ll-axis" x1={pad.l} x2={width - pad.r} y1={y(lo)} y2={y(lo)} />
        <text className="ll-tick" x={pad.l - 4} y={y(hi) + 4} textAnchor="end">
          {format(hi)}
        </text>
        <text className="ll-tick" x={pad.l - 4} y={y(lo)} textAnchor="end">
          {format(lo)}
        </text>
        <text className="ll-tick" x={pad.l} y={height - 4}>
          {xLabel} 0
        </text>
        <text className="ll-tick" x={width - pad.r} y={height - 4} textAnchor="end">
          {n - 1}
        </text>
        {marker && (
          <g>
            <line
              className="ll-marker"
              x1={x(marker.x)}
              x2={x(marker.x)}
              y1={pad.t}
              y2={height - pad.b}
            />
            <text className="ll-tick ll-tick--marker" x={x(marker.x) + 4} y={pad.t + 20}>
              {marker.label}
            </text>
          </g>
        )}
        {series.map((s) => (
          <path key={s.label} className={`ll-line ll-line--${s.tone}`} d={path(s.values)} />
        ))}
        {hover !== null && (
          <g>
            <line className="ll-cross" x1={x(hover)} x2={x(hover)} y1={pad.t} y2={height - pad.b} />
            {series.map((s) =>
              s.values[hover] != null ? (
                <circle
                  key={s.label}
                  className={`ll-dot ll-line--${s.tone}`}
                  cx={x(hover)}
                  cy={y(s.values[hover]!)}
                  r={4}
                />
              ) : null,
            )}
          </g>
        )}
      </svg>
      <div className="ll-readout" aria-live="polite">
        {hover === null
          ? ' '
          : `${xLabel} ${hover} · ${series
              .map(
                (s) =>
                  `${series.length > 1 ? `${s.label} ` : ''}${s.values[hover] == null ? '—' : format(s.values[hover]!)}`,
              )
              .join(' · ')}`}
      </div>
    </figure>
  );
}

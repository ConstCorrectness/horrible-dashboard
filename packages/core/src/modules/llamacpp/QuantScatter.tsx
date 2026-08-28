/**
 * What a quantization actually costs, as a picture.
 *
 * The model list carries `parameters` and `sizeBytes` per file and prints them as
 * two numbers in a row of text. Plotted against each other they stop being two
 * numbers and become a *rate* — bytes per parameter — and each quantization family
 * falls on its own line through the origin. Q8_0 sits at roughly twice Q4_K_M's
 * slope, F16 at four times, and you can read that off the picture without knowing
 * what any of the names mean. That is the thing this pane is for.
 *
 * Log-log, because a catalogue spans 0.5B to 70B and a linear axis would put every
 * small model in one pixel at the origin. On log-log a constant bytes-per-parameter
 * ratio is still a straight line, so the property being read survives the transform.
 *
 * **It refuses to draw itself under three points.** A scatter of one is not a
 * scatter, it is a dot with axes implying a trend that was never measured.
 */
import { useMemo, useState } from 'react';

import { formatBytes, formatParams, type ModelEntry } from './api';

const W = 320;
const H = 190;
const PAD = { left: 34, right: 8, top: 10, bottom: 22 };

/** Enough points that the eye can find a line; fewer is a table with axes. */
export const MIN_POINTS = 3;

/** The family, not the exact type: `Q4_K_M` and `Q4_K_S` sit on the same line. */
function family(quantization: string): string {
  const q = quantization.toUpperCase();
  const m = /^(IQ?\d+|Q\d+|F\d+|BF\d+)/.exec(q);
  return m ? m[1] : q || '?';
}

export function QuantScatter({ models }: { models: ModelEntry[] }) {
  const [hover, setHover] = useState<string | null>(null);

  const points = useMemo(
    () =>
      models
        .filter((m) => !!m.parameters && m.parameters > 0 && m.sizeBytes > 0)
        .map((m) => ({
          path: m.path,
          name: m.name.split('/').pop() ?? m.name,
          x: Math.log10(m.parameters as number),
          y: Math.log10(m.sizeBytes),
          params: m.parameters as number,
          bytes: m.sizeBytes,
          quant: m.quantization || '?',
          family: family(m.quantization),
        })),
    [models],
  );

  if (points.length < MIN_POINTS) return null;

  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  const x0 = Math.min(...xs);
  const x1 = Math.max(...xs);
  const y0 = Math.min(...ys);
  const y1 = Math.max(...ys);
  // A degenerate axis (every model the same size) would divide by zero; pad it so
  // the points land in the middle rather than collapsing onto an edge.
  const sx = (v: number) =>
    PAD.left + ((v - x0) / (x1 - x0 || 1)) * (W - PAD.left - PAD.right) - (x1 === x0 ? -0.5 : 0);
  const sy = (v: number) =>
    H - PAD.bottom - ((v - y0) / (y1 - y0 || 1)) * (H - PAD.top - PAD.bottom);

  const families = [...new Set(points.map((p) => p.family))].sort();
  const active = hover ? points.find((p) => p.path === hover) : null;

  return (
    <div className="llama-scatter-wrap">
      <svg
        className="llama-scatter"
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={`${points.length} models plotted by parameter count against file size`}
      >
        {/* The reference lines are the point of the chart: each is a constant
            bytes-per-parameter, so a family's members lie along one of them. */}
        {[0.5, 1, 2, 4].map((bpp) => {
          const ay = Math.log10(bpp);
          return (
            <line
              key={bpp}
              className="llama-scatter-guide"
              x1={sx(x0)}
              y1={sy(x0 + ay)}
              x2={sx(x1)}
              y2={sy(x1 + ay)}
            />
          );
        })}

        {points.map((p) => (
          <circle
            key={p.path}
            className={`llama-scatter-dot${hover === p.path ? ' llama-scatter-on' : ''}`}
            data-family={p.family}
            cx={sx(p.x)}
            cy={sy(p.y)}
            r={hover === p.path ? 5 : 3.2}
            onMouseEnter={() => setHover(p.path)}
            onMouseLeave={() => setHover(null)}
          >
            <title>{`${p.name}\n${formatParams(p.params)} params · ${formatBytes(p.bytes)} · ${p.quant}`}</title>
          </circle>
        ))}

        <text className="llama-scatter-axis" x={PAD.left} y={H - 6}>
          {formatParams(10 ** x0)}
        </text>
        <text className="llama-scatter-axis llama-scatter-end" x={W - PAD.right} y={H - 6}>
          {formatParams(10 ** x1)} params
        </text>
        <text
          className="llama-scatter-axis"
          transform={`translate(10 ${PAD.top + 8}) rotate(-90)`}
          textAnchor="end"
        >
          {formatBytes(10 ** y1)}
        </text>
      </svg>

      <div className="llama-scatter-legend">
        {families.map((f) => (
          <span key={f} className="llama-chip" data-family={f}>
            {f}
          </span>
        ))}
        <span className="llama-meta">
          {active
            ? `${active.name} — ${(active.bytes / active.params).toFixed(2)} bytes/param`
            : 'guides are 0.5, 1, 2 and 4 bytes per parameter'}
        </span>
      </div>
    </div>
  );
}

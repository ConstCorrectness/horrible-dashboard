/**
 * A sparkline. Lifted from `modules/llamacpp/TracesSection.tsx`, generalized off
 * the llama.cpp `TraceSeries` type so anything with a series can draw one.
 *
 * The geometry — including the gap rule — lives in `spark.ts` and is tested there.
 * This file is only the SVG.
 */
import './viz.css';
import { sparkRuns, type SparkPoint } from './spark';

export interface SparklineProps {
  points: SparkPoint[];
  /** Shared y domain, for a set of charts that must be comparable. */
  domain?: readonly [number, number];
  width?: number;
  height?: number;
  /** Required: a chart with no accessible name is a decoration. */
  label: string;
  /** Shown in place of the chart when nothing was measured. */
  empty?: string;
}

export function Sparkline({
  points,
  domain,
  width = 96,
  height = 18,
  label,
  empty = 'no series',
}: SparklineProps) {
  const geo = sparkRuns(points, width, height, domain);
  if (geo.measured === 0) return <span className="viz-spark-empty">{empty}</span>;

  return (
    <svg
      className="viz-spark"
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      role="img"
      aria-label={label}
    >
      {geo.runs.map((coords, index) =>
        coords.length > 1 ? (
          <polyline key={index} className="viz-spark-line" points={coords.join(' ')} />
        ) : (
          <circle
            key={index}
            className="viz-spark-dot"
            cx={coords[0].split(',')[0]}
            cy={coords[0].split(',')[1]}
            r={1.4}
          />
        ),
      )}
    </svg>
  );
}

/**
 * A progress bar, with the indeterminate state kept.
 *
 * Lifted from `modules/llamacpp/ServerPane.tsx`. The addition is **rate and ETA**:
 * a 4 GB download that shows only a percentage gives no way to decide whether to
 * wait for it, and the numbers needed are already arriving — successive
 * `completed` values from the NDJSON stream.
 *
 * The rate is a windowed average rather than an instantaneous one, because an
 * instantaneous rate over a chunked stream reads as noise and an ETA computed from
 * it swings by minutes between frames.
 */
import { useEffect, useRef, useState, type CSSProperties } from 'react';

import './viz.css';

export interface ProgressBarProps {
  completed?: number | null;
  total?: number | null;
  status?: string | null;
  /** Rendered after the status. Bytes, usually. */
  detail?: string;
  /** Show the derived rate/ETA. Off for progress that is not a byte count. */
  rate?: boolean;
  formatRate?: (bytesPerSecond: number) => string;
}

interface Sample {
  at: number;
  completed: number;
}

/** Long enough to smooth a chunked stream, short enough to react to a stall. */
const WINDOW_MS = 8000;

export function ProgressBar({
  completed,
  total,
  status,
  detail,
  rate = false,
  formatRate,
}: ProgressBarProps) {
  const value = total && completed ? Math.min(100, Math.round((completed / total) * 100)) : null;

  const samples = useRef<Sample[]>([]);
  const [speed, setSpeed] = useState<number | null>(null);

  useEffect(() => {
    if (!rate || completed == null) return;
    const now = Date.now();
    const list = samples.current;
    list.push({ at: now, completed });
    while (list.length > 2 && now - list[0].at > WINDOW_MS) list.shift();
    const first = list[0];
    const seconds = (now - first.at) / 1000;
    // A window shorter than a second divides by noise; say nothing until it opens.
    setSpeed(seconds >= 1 ? Math.max(0, (completed - first.completed) / seconds) : null);
  }, [completed, rate]);

  const remaining = speed && speed > 0 && total && completed ? (total - completed) / speed : null;

  return (
    <div className="viz-progress">
      <div className="viz-progress-track">
        <div
          className={`viz-progress-fill${value === null ? ' viz-progress-idle' : ''}`}
          // A scale factor, not a width: the bar is animated with `transform` so a
          // stream of progress frames never triggers layout. See `viz.css`.
          style={value === null ? undefined : ({ ['--viz-p' as string]: value / 100 } as CSSProperties)}
        />
      </div>
      <span className="viz-progress-label">
        {status ?? 'working'}
        {value !== null ? ` · ${value}%` : ''}
        {detail ? ` · ${detail}` : ''}
        {speed !== null && formatRate ? ` · ${formatRate(speed)}/s` : ''}
        {remaining !== null && remaining > 1 ? ` · ${formatEta(remaining)} left` : ''}
      </span>
    </div>
  );
}

/** Coarse on purpose: "about 4 min" is the useful answer, "3:47" is false precision. */
function formatEta(seconds: number): string {
  if (seconds < 90) return `${Math.round(seconds)}s`;
  const minutes = seconds / 60;
  if (minutes < 90) return `${Math.round(minutes)} min`;
  return `${(minutes / 60).toFixed(1)} h`;
}

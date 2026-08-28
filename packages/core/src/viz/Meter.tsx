/**
 * A segmented budget meter with an optional threshold rule.
 *
 * One component for three call sites that had each drawn their own: the VRAM
 * ceiling in `OffloadPreview`, the model disk budget, and the trace disk budget.
 * The last two were a *sentence* ("4.2 GB of 80 GB"), which is a fact you read
 * rather than a proportion you see.
 *
 * ## The rule that is in the type
 *
 * `threshold` is `number | null`, and `null` means **nothing measured it**. The
 * component then draws no rule and returns no verdict, because "unknown" must not
 * render as "fits" — the honesty rule `OffloadPreview` and the hardware module
 * both state, promoted here from a comment to something a caller cannot get wrong.
 * There is deliberately no `fits` prop: a caller cannot assert a verdict this
 * component would have refused to draw.
 */
import type { ReactNode } from 'react';

import './viz.css';

export type MeterTone = 'primary' | 'secondary' | 'muted' | 'warn';

export interface MeterSegment {
  value: number;
  tone: MeterTone;
  label: string;
  /** Marks this segment as the hovered/selected one from outside. */
  active?: boolean;
  onHover?: (entering: boolean) => void;
}

export interface MeterProps {
  segments: MeterSegment[];
  /**
   * The full width of the scale. Pass the max of the content and the threshold so
   * a rule that sits beyond the content is still on-canvas.
   */
  total: number;
  /** The budget line, or null when nothing measured one. */
  threshold?: number | null;
  thresholdLabel?: string;
  label: string;
  children?: ReactNode;
}

export function Meter({
  segments,
  total,
  threshold = null,
  thresholdLabel,
  label,
  children,
}: MeterProps) {
  const scale = Math.max(total, threshold ?? 0) || 1;
  const pct = (n: number) => `${Math.min(100, Math.max(0, (n / scale) * 100))}%`;
  const used = segments.reduce((sum, s) => sum + s.value, 0);
  const over = threshold !== null && used > threshold;

  return (
    <div className="viz-meter-wrap">
      <div className={`viz-meter${over ? ' viz-meter-over' : ''}`} role="img" aria-label={label}>
        {segments.map((segment, index) => (
          <div
            key={index}
            className={`viz-meter-seg${segment.active ? ' viz-meter-seg-on' : ''}`}
            data-tone={segment.tone}
            style={{ width: pct(segment.value) }}
            title={segment.label}
            onMouseEnter={segment.onHover ? () => segment.onHover?.(true) : undefined}
            onMouseLeave={segment.onHover ? () => segment.onHover?.(false) : undefined}
          />
        ))}
        {threshold !== null && (
          <div className="viz-meter-rule" style={{ left: pct(threshold) }} title={thresholdLabel} />
        )}
      </div>
      {children}
    </div>
  );
}

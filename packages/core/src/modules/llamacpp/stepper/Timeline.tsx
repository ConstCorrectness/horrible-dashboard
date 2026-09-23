/**
 * The whole program at a glance: one bar per step, the scrubber under the stepper.
 *
 * Colour is the stage (so a block reads as a repeating attention/ffn/residual
 * rhythm), height is `log(1 + rms)` of the node over the whole trace (so the
 * residual stream's growth with depth and any blow-up are visible before you step
 * to them), and a bar with nothing measured is drawn as a short hollow tick rather
 * than a zero — the "gap is not zero" rule the profile route already follows.
 *
 * SVG rather than canvas: a few hundred to a few thousand rects is well inside what
 * a browser lays out, and it keeps the stage colours as CSS classes over theme
 * tokens rather than literals a canvas would need.
 */
import { useCallback, useRef } from 'react';

import type { Breakpoint, HitContext, Program } from './program';
import { stopsAt } from './program';

export interface TimelineProps {
  program: Program;
  cursor: number;
  /** `recordIndex -> rms`, possibly partial. */
  rms: ReadonlyMap<number, number | null>;
  breakpoints: readonly Breakpoint[];
  /** The same context Continue evaluates against, so a mark is drawn exactly
   * where Continue would stop — a lens breakpoint included. */
  ctx: HitContext;
  onSeek: (step: number) => void;
}

const H = 44;

export function Timeline({ program, cursor, rms, breakpoints, ctx, onSeek }: TimelineProps) {
  const ref = useRef<SVGSVGElement>(null);
  const dragging = useRef(false);
  const n = program.steps.length;

  let peak = 0;
  for (const v of rms.values()) if (typeof v === 'number') peak = Math.max(peak, Math.log1p(v));

  const seekAt = useCallback(
    (clientX: number) => {
      const svg = ref.current;
      if (!svg || !n) return;
      const rect = svg.getBoundingClientRect();
      const t = (clientX - rect.left) / Math.max(1, rect.width);
      onSeek(Math.max(0, Math.min(n - 1, Math.floor(t * n))));
    },
    [n, onSeek],
  );

  if (!n) return null;
  return (
    <svg
      ref={ref}
      className="stp-timeline"
      viewBox={`0 0 ${n} ${H}`}
      preserveAspectRatio="none"
      role="slider"
      aria-label="Program timeline"
      aria-valuemin={0}
      aria-valuemax={n - 1}
      aria-valuenow={cursor}
      onPointerDown={(e) => {
        dragging.current = true;
        e.currentTarget.setPointerCapture(e.pointerId);
        seekAt(e.clientX);
      }}
      onPointerMove={(e) => dragging.current && seekAt(e.clientX)}
      onPointerUp={() => {
        dragging.current = false;
      }}
    >
      {program.steps.map((s) => {
        const value = rms.get(s.recordIndex);
        const measured = typeof value === 'number' && peak > 0;
        const h = measured ? 4 + (Math.log1p(value) / peak) * (H - 12) : 3;
        const frameStartHere =
          s.step > 0 &&
          (program.steps[s.step - 1].layer !== s.layer ||
            program.steps[s.step - 1].passIndex !== s.passIndex);
        return (
          <g key={s.step}>
            {frameStartHere && (
              <line
                className={
                  program.steps[s.step - 1].passIndex !== s.passIndex
                    ? 'stp-tl-pass'
                    : 'stp-tl-frame'
                }
                x1={s.step}
                x2={s.step}
                y1={0}
                y2={H}
              />
            )}
            <rect
              className={`stp-tl-bar stp-stage-${s.stage}${measured ? '' : ' stp-tl-gap'}${
                s.step > cursor ? ' stp-tl-future' : ''
              }`}
              x={s.step + 0.1}
              width={0.8}
              y={H - 4 - h}
              height={h}
            />
            {stopsAt(program, s.step, breakpoints, ctx) && (
              <rect className="stp-tl-break" x={s.step} width={1} y={H - 3} height={3} />
            )}
          </g>
        );
      })}
      <rect className="stp-tl-cursor" x={cursor} width={1} y={0} height={H} />
    </svg>
  );
}

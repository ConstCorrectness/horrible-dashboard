/**
 * The diverging ramp, as a strength rather than as a colour.
 *
 * This used to be `cellColor()` in `modules/llamacpp/TracesSection.tsx`, returning
 * `rgb(110 190 255 / 34%)`. That is a hardcoded colour — it renders identically on
 * the daylight theme, where a pale blue on white is invisible — and it slipped past
 * `no-hex-literals.test.ts` only because that guard matches `#rrggbb` and not the
 * functional notation. Passing the ratchet is not the same as being themed.
 *
 * So this returns a **sign and an alpha, never a colour**. The caller stamps the
 * sign as a data attribute and the alpha as a custom property, and `viz.css`
 * resolves both against `--ramp-pos` / `--ramp-neg`. A theme can then say what
 * "positive" looks like, which is the whole point of having themes.
 *
 * Signed and centred on zero because activations are signed and the sign is the
 * interesting part — a sequential ramp would hide half the story.
 */

export type RampSign = 'pos' | 'neg' | 'zero';

export interface RampCell {
  sign: RampSign;
  /** 0..1, consumed as a percentage of the sign's colour. */
  alpha: number;
}

/** The floor keeps a near-zero cell visible as a cell rather than as a hole. */
export const RAMP_FLOOR = 0.12;
export const RAMP_RANGE = 0.8;

/**
 * `scale` is the magnitude that maps to full strength — normally the largest
 * absolute value in the strip, so the ramp is relative to what is actually there.
 *
 * A zero (or absent) scale means nothing has been measured to compare against, and
 * the honest answer is the neutral cell, not a confident full-strength one.
 */
export function rampCell(value: number, scale: number): RampCell {
  if (!scale || !Number.isFinite(scale) || !Number.isFinite(value)) {
    return { sign: 'zero', alpha: 0 };
  }
  const t = Math.max(-1, Math.min(1, value / scale));
  if (t === 0) return { sign: 'zero', alpha: 0 };
  return {
    sign: t > 0 ? 'pos' : 'neg',
    alpha: RAMP_FLOOR + Math.abs(t) * RAMP_RANGE,
  };
}

/** The largest absolute value, which is what `rampCell`'s `scale` wants. */
export function rampScale(values: ArrayLike<number>): number {
  let max = 0;
  for (let i = 0; i < values.length; i += 1) {
    const v = Math.abs(values[i] ?? 0);
    if (Number.isFinite(v) && v > max) max = v;
  }
  return max;
}

/**
 * The props a ramped element needs. Returned as one object so a caller cannot
 * apply the sign without the alpha, which would draw every cell at full strength.
 */
export function rampProps(cell: RampCell): {
  'data-sign': RampSign;
  style: Record<string, string>;
} {
  return { 'data-sign': cell.sign, style: { '--viz-a': String(cell.alpha) } };
}

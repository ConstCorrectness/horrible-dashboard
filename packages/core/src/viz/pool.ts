/**
 * Downsampling, kept honest.
 *
 * A residual is ~4096 floats and a browser will lay out 4096 spans; it just should
 * not have to. Everything here reduces a series to a drawable number of cells.
 *
 * The choice of reducer is not cosmetic. **Mean** is right for a 1-D strip, where
 * neighbouring components are unrelated and the average is the honest summary of a
 * bucket. **Max-of-abs** is right for anything where a single extreme is the signal
 * — an attention row is mostly near-zero with one or two spikes, and averaging it
 * erases exactly the thing you opened the view to see.
 *
 * Whichever is used, a pooled view must SAY it pooled. A caller reading `pooled`
 * off the result and not rendering it is the bug this comment exists to prevent.
 */

export interface Pooled {
  cells: number[];
  /** True when cells are summaries of several values rather than the values. */
  pooled: boolean;
  /** How many source values each cell stands for, 1 when not pooled. */
  factor: number;
}

function pool(values: ArrayLike<number>, cells: number, mean: boolean): Pooled {
  const n = values.length;
  if (n <= cells) {
    return { cells: Array.from(values as ArrayLike<number>), pooled: false, factor: 1 };
  }
  const size = n / cells;
  const out: number[] = [];
  for (let i = 0; i < cells; i += 1) {
    const start = Math.floor(i * size);
    const end = Math.max(start + 1, Math.floor((i + 1) * size));
    if (mean) {
      let total = 0;
      for (let j = start; j < end; j += 1) total += values[j] ?? 0;
      out.push(total / (end - start));
    } else {
      // Max of absolute value, but the SIGN of the winner is kept: a strip whose
      // extremes all came back positive would misreport a signed quantity.
      let best = 0;
      for (let j = start; j < end; j += 1) {
        const v = values[j] ?? 0;
        if (Math.abs(v) > Math.abs(best)) best = v;
      }
      out.push(best);
    }
  }
  return { cells: out, pooled: true, factor: n / cells };
}

/** Bucket means. The default for a 1-D activation strip. */
export function poolMean(values: ArrayLike<number>, cells: number): Pooled {
  return pool(values, cells, true);
}

/** Bucket extremes, sign preserved. For anything where a spike is the signal. */
export function poolMaxAbs(values: ArrayLike<number>, cells: number): Pooled {
  return pool(values, cells, false);
}

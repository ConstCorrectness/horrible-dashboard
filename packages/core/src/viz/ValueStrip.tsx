/**
 * A 1-D diverging heat strip — a tensor's values as a row of cells.
 *
 * Lifted from `modules/llamacpp/TracesSection.tsx`. Two things changed on the way:
 * the colour is now a token (see `ramp.ts` for why the old `rgb()` literal was not
 * as themed as it looked), and the strip **says when it pooled**. The old one
 * silently averaged 4096 values into 192 cells and presented the result as the
 * values.
 */
import './viz.css';
import { poolMean } from './pool';
import { rampCell, rampProps, rampScale } from './ramp';

export interface ValueStripProps {
  values: ArrayLike<number>;
  /** Required: this is a figure, and a figure needs a name. */
  label: string;
  cells?: number;
  /** Whether to print the "N values per cell" note. */
  showPooling?: boolean;
}

/** Enough to read a shape off, few enough for a browser to lay out. */
export const STRIP_CELLS = 192;

export function ValueStrip({
  values,
  label,
  cells = STRIP_CELLS,
  showPooling = true,
}: ValueStripProps) {
  const pooled = poolMean(values, cells);
  const scale = rampScale(pooled.cells);

  return (
    <div className="viz-strip-wrap">
      <div className="viz-strip" role="img" aria-label={label}>
        {pooled.cells.map((value, index) => {
          const props = rampProps(rampCell(value, scale));
          return (
            <span
              key={index}
              className="viz-strip-cell"
              data-sign={props['data-sign']}
              style={props.style}
              title={value.toFixed(4)}
            />
          );
        })}
      </div>
      {showPooling && pooled.pooled && (
        <p className="viz-note">
          Pooled — each cell is the mean of {Math.round(pooled.factor)} values.
        </p>
      )}
    </div>
  );
}

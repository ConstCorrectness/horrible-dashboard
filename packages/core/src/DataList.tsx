/**
 * The shared list-row primitives.
 *
 * A module that lists records — eval cases, run results, discovered models,
 * anything with an identity, a verdict and some figures — composes them from
 * here rather than styling its own `<div>`s. The reason is not tidiness: the
 * rows had all independently converged on the same undesigned shape (one font
 * weight, colour on the whole perimeter, numbers set as prose), and a rule
 * written in a comment is a rule that drifts. A component is one that does not.
 *
 * The type contract is the design: a row takes a `title` (identity), `meta`
 * (telemetry, always monospace) and a `kind` (verdict). It deliberately has no
 * "colour" or "style" prop — a caller that could pick a colour directly is a
 * caller that will eventually pick one that means nothing.
 *
 * See `datalist.css` for what each part is doing and why.
 */
import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';

import './datalist.css';

/**
 * The verdict a row carries, which is what its rail and mark are drawn from.
 *
 * `idle` is not "no verdict is possible" — it is "this record does not have
 * one", which is a different fact from a pass and must not look like one.
 */
export type RowKind = 'ok' | 'fail' | 'warn' | 'info' | 'idle';

/**
 * How far the entrance stagger runs before every later row arrives together.
 *
 * Without a cap the delay is linear in the list length, so a suite with two
 * hundred results would still be arriving four seconds after it rendered. The
 * flourish is worth a third of a second and nothing more.
 */
export const STAGGER_CAP = 12;

/** Per-row delay index, capped. Exported so a caller building rows by hand
 * (a table body, say) staggers identically instead of inventing its own. */
export function staggerIndex(index: number): number {
  return Math.min(index, STAGGER_CAP);
}

/**
 * The verdict glyph.
 *
 * Vector strokes inheriting `currentColor`, never an emoji: the row's accent
 * already decides the colour, and an emoji is a third-party font's opinion
 * about it that also changes size between platforms.
 */
export function RowMark({ kind }: { kind: RowKind }) {
  const common = {
    width: 12,
    height: 12,
    viewBox: '0 0 12 12',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.6,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
    'aria-hidden': true,
  };
  switch (kind) {
    case 'ok':
      return (
        <svg {...common}>
          <path d="M2 6.4 4.7 9 10 3.2" />
        </svg>
      );
    case 'fail':
      return (
        <svg {...common}>
          <path d="M3 3l6 6M9 3l-6 6" />
        </svg>
      );
    case 'warn':
      return (
        <svg {...common}>
          <path d="M6 1.6 11 10.4H1z" />
          <path d="M6 5v2.2M6 8.9v.1" />
        </svg>
      );
    case 'info':
      return (
        <svg {...common}>
          <path d="M6 1.5v9M1.5 6h9" />
        </svg>
      );
    default:
      return (
        <svg {...common}>
          <path d="M2 6h8" />
        </svg>
      );
  }
}

/**
 * The list container.
 *
 * `layout="grid"` is for lists whose items are *peers being chosen between*
 * rather than records read in order — a model picker, not a result log. A
 * column of six checkboxes is the shape that reads as an unstyled form; a grid
 * of tiles reads as a loadout.
 *
 * `size="lead"` sets the titles at reading size. It is a property of the *list*,
 * not a style prop on the row: "these items are identities being chosen between,
 * and the name is the thing you are choosing" is a fact about the list, whereas a
 * row that can be handed a font size is a row whose caller will eventually pick one
 * that means nothing. The treatment is otherwise unchanged — still uppercase, still
 * tracked — because only the size was ever wrong.
 */
export function DataList({
  children,
  layout = 'column',
  size = 'default',
  label,
  style,
}: {
  children: ReactNode;
  layout?: 'column' | 'grid';
  size?: 'default' | 'lead';
  /** Accessible name. A list of results and a list of cases are different
   * landmarks even when they are drawn the same. */
  label?: string;
  style?: CSSProperties;
}) {
  return (
    <ul
      className="hd-list"
      data-layout={layout}
      data-size={size === 'default' ? undefined : size}
      aria-label={label}
      style={style}
    >
      {children}
    </ul>
  );
}

export interface DataRowProps {
  /** Identity. Set uppercase with heavy tracking, so keep it short. */
  title: ReactNode;
  /**
   * Telemetry: ids, counts, durations, percentages. Rendered monospace and
   * divided by hairlines, and each entry is one cell — pass an array rather
   * than a pre-joined string, or the dividers cannot be drawn.
   */
  meta?: ReactNode[];
  /** Tints the whole meta line. For "this row's figures are the problem". */
  metaTone?: 'warn' | 'fail';
  kind?: RowKind;
  /** Suppress the leading glyph — for rows whose kind is a category, not a
   * verdict, where a tick would claim something that was never tested. */
  hideMark?: boolean;
  /** A short uppercase tag pinned to the right of the head line. */
  badge?: ReactNode;
  /** Buttons. Kept out of the row's own click target. */
  actions?: ReactNode;
  /** Prose. The sentence that says what to do about the row. */
  children?: ReactNode;
  /** Extra lines under the body — expectations, warnings, provenance. */
  footnotes?: ReactNode;
  index?: number;
  selected?: boolean;
  onClick?: () => void;
}

/**
 * One record.
 *
 * Rendered as a `<button>` when it is clickable and a `<div>` when it is not,
 * rather than a div with a handler: a row you can activate has to be reachable
 * from the keyboard, and a row you cannot must not be a tab stop.
 */
export function DataRow({
  title,
  meta,
  metaTone,
  kind = 'idle',
  hideMark,
  badge,
  actions,
  children,
  footnotes,
  index = 0,
  selected,
  onClick,
}: DataRowProps) {
  const interactive = Boolean(onClick);
  const inner = (
    <>
      <div className="hd-row-head">
        {!hideMark && (
          <span className="hd-row-mark">
            <RowMark kind={kind} />
          </span>
        )}
        <span className="hd-row-title">{title}</span>
        {meta && meta.length > 0 && (
          <span className="hd-row-meta" data-tone={metaTone}>
            {meta.map((m, i) => (
              <span key={i}>{m}</span>
            ))}
          </span>
        )}
        <span className="hd-row-spacer" />
        {badge && <span className="hd-row-badge">{badge}</span>}
        {actions && (
          // The actions sit inside a clickable row, so their clicks must not
          // also select it. Stopped here rather than in each button, which is
          // where one would eventually be forgotten.
          <span className="hd-row-actions" onClick={(e) => e.stopPropagation()} role="presentation">
            {actions}
          </span>
        )}
      </div>
      {children && <div className="hd-row-body">{children}</div>}
      {footnotes}
    </>
  );

  const style = { '--hd-i': staggerIndex(index) } as CSSProperties;

  return (
    <li>
      {interactive ? (
        <button
          type="button"
          className="hd-row"
          data-kind={kind}
          data-interactive="true"
          data-selected={selected ? 'true' : undefined}
          style={style}
          onClick={onClick}
        >
          {inner}
        </button>
      ) : (
        <div
          className="hd-row"
          data-kind={kind}
          data-selected={selected ? 'true' : undefined}
          style={style}
        >
          {inner}
        </div>
      )}
    </li>
  );
}

/**
 * A row that is a choice.
 *
 * The native checkbox is still there and still does the work — it is what makes
 * the row focusable, announceable and space-bar operable — but it is visually
 * hidden and the square beside the label is drawn. Replacing the input with a
 * div carrying `role="checkbox"` is the version of this that looks the same and
 * loses the keyboard.
 */
export function PickRow({
  title,
  meta,
  checked,
  onChange,
  index = 0,
  note,
}: {
  title: ReactNode;
  meta?: ReactNode;
  checked: boolean;
  onChange: (next: boolean) => void;
  index?: number;
  note?: ReactNode;
}) {
  const style = { '--hd-i': staggerIndex(index) } as CSSProperties;
  return (
    <li>
      <label
        className="hd-row"
        data-kind={checked ? 'info' : 'idle'}
        data-interactive="true"
        data-selected={checked ? 'true' : undefined}
        style={style}
      >
        <span className="hd-pick">
          <input
            className="hd-pick-input"
            type="checkbox"
            checked={checked}
            onChange={(e) => onChange(e.target.checked)}
          />
          <span className="hd-pick-box" aria-hidden="true">
            {checked && (
              <svg
                width="9"
                height="9"
                viewBox="0 0 12 12"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M2 6.4 4.7 9 10 3.2" />
              </svg>
            )}
          </span>
          <span className="hd-pick-text">
            <span className="hd-row-title">{title}</span>
            {meta && <span className="hd-row-meta">{meta}</span>}
          </span>
        </span>
        {note && <span className="hd-row-body">{note}</span>}
      </label>
    </li>
  );
}

/**
 * A number that counts up to its value.
 *
 * **Seeded at the final value, with a timeout that snaps to it.** `requestAnimationFrame`
 * does not fire in a backgrounded tab, so a counter that starts at zero and
 * relies on frames to arrive shows `0` for a stat that is `62` — which is worse
 * than not animating at all. Starting at the answer means the worst case is a
 * missing flourish rather than a wrong figure.
 */
export function RollingNumber({
  value,
  duration = 420,
  format = (n: number) => String(Math.round(n)),
}: {
  value: number;
  duration?: number;
  format?: (n: number) => string;
}) {
  const [shown, setShown] = useState(value);
  const from = useRef(value);

  useEffect(() => {
    const start = from.current;
    if (start === value || duration <= 0) {
      from.current = value;
      setShown(value);
      return;
    }
    const began = performance.now();
    let frame = 0;
    const tick = (now: number) => {
      const t = Math.min(1, (now - began) / duration);
      // Ease-out: the interesting part of a counter is where it lands.
      setShown(start + (value - start) * (1 - Math.pow(1 - t, 3)));
      if (t < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    // The backstop. If the frames never come — a hidden tab, a throttled
    // renderer — the figure is still correct by the time the animation would
    // have finished.
    const snap = setTimeout(() => setShown(value), duration + 60);
    from.current = value;
    return () => {
      cancelAnimationFrame(frame);
      clearTimeout(snap);
    };
  }, [value, duration]);

  return <span className="hd-counter">{format(shown)}</span>;
}

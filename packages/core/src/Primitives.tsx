/**
 * The shared control primitives.
 *
 * `DataList.tsx` gave "a list of records" one shape. This file gives the things
 * around a list one shape too — the button, the state tag, the pane header, the
 * empty state and the labelled field. All five were being redrawn by hand in
 * nearly every pane; five near-identical private chip components existed across
 * two modules alone.
 *
 * Same type-contract-as-design stance as `DataList`: none of these takes a
 * `color`, `size`-in-pixels or `style` escape hatch for its core look. A caller
 * that can pick a colour directly is a caller that will eventually pick one that
 * means nothing, and the whole reason the app looked unfinished is that ~1,987
 * inline style objects each picked their own.
 *
 * See `primitives.css` for what each part is doing and why.
 */
import type { ButtonHTMLAttributes, CSSProperties, ReactNode } from 'react';

import './primitives.css';
import type { RowKind } from './DataList';

/**
 * What a button *means*, not what colour it is.
 *
 * `danger` exists because a destructive control has to be visibly different
 * before the click, not only in the confirm dialog after it.
 */
export type ButtonIntent = 'default' | 'primary' | 'ghost' | 'danger';

export interface ButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'className'> {
  intent?: ButtonIntent;
  /** `sm` is for buttons living inside a row's action slot. */
  size?: 'sm' | 'md';
  /** Leading glyph. A vector node inheriting `currentColor` — never an emoji. */
  icon?: ReactNode;
}

/**
 * A button.
 *
 * `type="button"` by default rather than the HTML default of `submit`: almost
 * every button here sits inside a form-shaped layout and is not the submit, and
 * the bug that produces (a stray click reloading the pane) is both easy to write
 * and hard to spot.
 */
export function Button({
  intent = 'default',
  size = 'md',
  icon,
  children,
  type = 'button',
  ...rest
}: ButtonProps) {
  return (
    <button
      {...rest}
      type={type}
      className="hd-btn"
      data-intent={intent === 'default' ? undefined : intent}
      data-size={size === 'md' ? undefined : size}
    >
      {icon}
      {children}
    </button>
  );
}

/**
 * A short state tag.
 *
 * Reuses `RowKind` rather than declaring its own vocabulary, so a state is the
 * same colour in a chip as in a row's verdict mark and the reader learns it once.
 * `idle` renders neutral — "this has no verdict" is a real state and must not
 * look like a pass.
 */
export function Chip({
  children,
  kind = 'idle',
  dot,
  title,
}: {
  children: ReactNode;
  kind?: RowKind;
  /** Show a leading dot — for a live connection state rather than a label. */
  dot?: boolean;
  title?: string;
}) {
  return (
    <span className="hd-chip" data-kind={kind === 'idle' ? undefined : kind} title={title}>
      {dot && <span className="hd-chip-dot" />}
      {children}
    </span>
  );
}

/**
 * The bar above a pane's content.
 *
 * Splits the same three jobs a row does — identity, telemetry, actions — so a
 * header and the rows beneath it read as one object. `meta` takes an array for
 * the same reason `DataRow`'s does: the dividers cannot be drawn between cells
 * that were joined into a string first.
 */
export function PaneHeader({
  title,
  meta,
  actions,
  style,
}: {
  title: ReactNode;
  meta?: ReactNode[];
  actions?: ReactNode;
  style?: CSSProperties;
}) {
  return (
    <div className="hd-panehead" style={style}>
      <span className="hd-panehead-title">{title}</span>
      {meta && meta.length > 0 && (
        <span className="hd-panehead-meta">
          {meta.map((m, i) => (
            <span key={i}>{m}</span>
          ))}
        </span>
      )}
      <span className="hd-panehead-spacer" />
      {actions && <span className="hd-panehead-actions">{actions}</span>}
    </div>
  );
}

/**
 * Nothing to show — and what to do about it.
 *
 * `children` is the action sentence and is the point of the component. "No
 * servers yet" is a fact the user can already see from the empty pane; "Add one
 * below, or browse the registry" is the pane doing its job. The title is
 * deliberately the quiet half.
 */
export function EmptyState({
  title,
  children,
  actions,
}: {
  title: ReactNode;
  children?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="hd-empty">
      <div className="hd-empty-title">{title}</div>
      {children && <div className="hd-empty-body">{children}</div>}
      {actions && <div className="hd-empty-actions">{actions}</div>}
    </div>
  );
}

/**
 * A labelled control.
 *
 * `error` replaces `hint` rather than joining it: showing both makes the reader
 * work out which line is the current truth, and the hint has already been read by
 * the time a value is wrong.
 *
 * The control is `children` rather than a prop, because this app's forms hold
 * selects, textareas, checkbox rows and bespoke pickers — a `type` prop would
 * have to grow a case for each and would still not cover the pickers.
 *
 * `span` is the one layout affordance, and it is deliberately not a width: it
 * says "this field wants the whole line" and leaves the container to decide what
 * a line is. A field that could name its own pixels is a field that will
 * eventually be 260px inside a 200px dock.
 */
export function Field({
  label,
  hint,
  error,
  required,
  htmlFor,
  span,
  children,
}: {
  label: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  required?: boolean;
  htmlFor?: string;
  /** `full` takes the whole width of a `ControlRow` at any card size. */
  span?: 'auto' | 'full';
  children: ReactNode;
}) {
  return (
    <label
      className="hd-field"
      data-invalid={error ? 'true' : undefined}
      data-span={span === 'full' ? 'full' : undefined}
      htmlFor={htmlFor}
    >
      <span className="hd-field-label">
        {label}
        {required && <span className="hd-field-req">*</span>}
      </span>
      {children}
      {error ? (
        <span className="hd-field-error">{error}</span>
      ) : (
        hint && <span className="hd-field-hint">{hint}</span>
      )}
    </label>
  );
}

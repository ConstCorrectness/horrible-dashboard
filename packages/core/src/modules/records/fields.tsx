/**
 * Field rendering shared by the grid and the form: one declared type → one input.
 * Kept apart from both panes because a schema is user-defined, so "which widget"
 * is a runtime decision made in two places and must not drift between them.
 */
import type { FieldDecl, RecordRow } from './api';

/** Display text for a stored value. Dates render as-is (they're stored ISO). */
export function formatValue(field: FieldDecl, value: unknown): string {
  if (value === null || value === undefined || value === '') return '';
  if (field.type === 'number') {
    const n = Number(value);
    return Number.isFinite(n) ? String(n) : String(value);
  }
  return String(value);
}

interface FieldInputProps {
  field: FieldDecl;
  value: unknown;
  onCommit: (value: unknown) => void;
  autoFocus?: boolean;
  /** Rows the `ref` picker offers, with their labels. */
  refOptions?: { id: string; label: string }[];
}

/**
 * One editable field. Commits on blur (and on Enter for single-line inputs) rather
 * than on every keystroke — each commit is a PATCH, and a per-character write would
 * make typing a name a dozen round-trips.
 */
export function FieldInput({ field, value, onCommit, autoFocus, refOptions }: FieldInputProps) {
  const current = value === null || value === undefined ? '' : String(value);

  if (field.type === 'select') {
    return (
      <select
        className="rec-input"
        value={current}
        autoFocus={autoFocus}
        onChange={(e) => onCommit(e.target.value || null)}
      >
        <option value="">—</option>
        {field.options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    );
  }

  if (field.type === 'ref') {
    return (
      <select
        className="rec-input"
        value={current}
        autoFocus={autoFocus}
        onChange={(e) => onCommit(e.target.value || null)}
      >
        <option value="">—</option>
        {/* An id already set but missing from the options (deleted row, or the
            target schema isn't loaded) still has to render, or opening the form
            would silently clear it on the next save. */}
        {current && !refOptions?.some((o) => o.id === current) && (
          <option value={current}>{current}</option>
        )}
        {(refOptions ?? []).map((option) => (
          <option key={option.id} value={option.id}>
            {option.label}
          </option>
        ))}
      </select>
    );
  }

  if (field.type === 'longtext') {
    return (
      <textarea
        className="rec-input rec-textarea"
        defaultValue={current}
        autoFocus={autoFocus}
        rows={4}
        onBlur={(e) => e.target.value !== current && onCommit(e.target.value || null)}
      />
    );
  }

  const inputType =
    field.type === 'number'
      ? 'number'
      : field.type === 'date'
        ? 'date'
        : field.type === 'email'
          ? 'email'
          : field.type === 'url'
            ? 'url'
            : 'text';

  return (
    <input
      className="rec-input"
      type={inputType}
      defaultValue={current}
      autoFocus={autoFocus}
      onBlur={(e) => e.target.value !== current && onCommit(e.target.value || null)}
      onKeyDown={(e) => {
        if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
        if (e.key === 'Escape') {
          (e.target as HTMLInputElement).value = current;
          (e.target as HTMLInputElement).blur();
        }
      }}
    />
  );
}

/** The fields a pane should show: declared order, minus retired ones. */
export function visibleFields(fields: FieldDecl[]): FieldDecl[] {
  return fields.filter((f) => !f.hidden);
}

/** A compact one-line summary of a row, for agent context and card subtitles. */
export function rowSummary(fields: FieldDecl[], row: RecordRow, max = 3): string {
  return visibleFields(fields)
    .slice(0, max)
    .map((f) => `${f.label}: ${formatValue(f, row[f.key]) || '—'}`)
    .join(', ');
}

import type { Detection } from '../api';
import { FORMAT_LABELS } from '../api';

/**
 * What shape this dataset is in, and the evidence.
 *
 * A verdict, never a coercion. The three things it must always show:
 *
 * - **The shape**, in the vocabulary a person uses ("ShareGPT"), not the backend
 *   id.
 * - **The evidence** — "`conversations` holds from/value pairs in 3 of 3 rows".
 *   Without it, a wrong verdict is indistinguishable from a right one, and the
 *   wrong one costs a GPU hour.
 * - **Whether it is a guess.** Below half confidence the component says so in
 *   words rather than by rendering the same badge in a paler colour, which
 *   nobody reads as uncertainty.
 */
export function FormatVerdict({
  detection,
  compact = false,
}: {
  detection: Detection;
  compact?: boolean;
}) {
  const label = FORMAT_LABELS[detection.format] ?? detection.format;
  const unknown = detection.format === 'unknown';
  const tone = unknown
    ? 'var(--text-dim)'
    : detection.certain
      ? 'var(--ok)'
      : 'var(--warn)';

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '0.25rem',
        borderLeft: `2px solid ${tone}`,
        paddingLeft: '0.55rem',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.5rem' }}>
        <span
          style={{
            fontWeight: 700,
            letterSpacing: '0.14em',
            textTransform: 'uppercase',
            fontSize: '0.72rem',
            color: tone,
          }}
        >
          {label}
        </span>
        {!unknown && (
          <span
            style={{
              fontFamily: 'var(--font-mono, monospace)',
              fontSize: '0.7rem',
              color: 'var(--text-dim)',
            }}
          >
            {Math.round(detection.confidence * 100)}%
          </span>
        )}
        {!detection.certain && !unknown && (
          <span style={{ fontSize: '0.72rem', color: 'var(--warn)' }}>
            best guess — confirm before training
          </span>
        )}
      </div>
      {!compact && detection.reason && (
        <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', lineHeight: 1.45 }}>
          {detection.reason}
        </div>
      )}
      {!compact && Object.keys(detection.columns).length > 0 && (
        <div
          style={{
            display: 'flex',
            gap: '0.4rem',
            flexWrap: 'wrap',
            fontFamily: 'var(--font-mono, monospace)',
            fontSize: '0.7rem',
            color: 'var(--text-dim)',
          }}
        >
          {Object.entries(detection.columns).map(([role, column]) => (
            <span key={role}>
              {role} → {column}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

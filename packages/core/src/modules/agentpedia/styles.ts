/**
 * The stepper's style vocabulary.
 *
 * Inline style objects over CSS theme variables, like every other pane here — no
 * Tailwind, no CSS-in-JS runtime. Kept in its own file only because the hub is
 * large enough that 200 lines of styling at the top would bury the logic.
 *
 * Every colour is a bare token reference with **no hex fallback**. A fallback is what
 * let LocalTrack render one fixed palette in all six themes while looking fully
 * themed in the source: an undefined `var()` falls through to it silently, so the
 * fallback hides the only symptom there is. `design-tokens.test.ts` guarantees every
 * name used here resolves, which is a stronger guarantee than a hardcoded hex.
 *
 * (That guard scans source text, so this comment deliberately does not spell out a
 * literal token reference — it would be read as a call site naming a token called
 * "token".)
 */
import type { CSSProperties } from 'react';

/** Display headings: bold, uppercase, heavy tracking. */
export const heading: CSSProperties = {
  fontSize: 11,
  fontWeight: 700,
  letterSpacing: '0.14em',
  textTransform: 'uppercase',
  color: 'var(--text-secondary)',
};

/** Metadata, ids, counts — monospace and muted, never body text. */
export const mono: CSSProperties = {
  fontFamily: 'var(--font-mono)',
  fontSize: 11,
  color: 'var(--text-secondary)',
};

export const pane: CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  height: '100%',
  overflow: 'hidden',
  background: 'var(--bg-primary)',
  color: 'var(--text-primary)',
  fontSize: 13,
};

export const bar: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 8,
  padding: '8px 12px',
  borderBottom: '1px solid var(--border)',
  flexShrink: 0,
  minHeight: 30,
};

/** A 30px control, per the dev-tool form-control standard. */
export const control: CSSProperties = {
  height: 30,
  borderRadius: 6,
  border: '1px solid var(--border)',
  background: 'var(--bg-secondary)',
  color: 'var(--text-primary)',
  padding: '0 10px',
  fontSize: 12,
};

export const ghostButton: CSSProperties = {
  ...control,
  cursor: 'pointer',
  display: 'inline-flex',
  alignItems: 'center',
  gap: 6,
};

export const primaryButton: CSSProperties = {
  ...ghostButton,
  background: 'var(--accent)',
  borderColor: 'var(--accent)',
  color: 'var(--accent-contrast)',
  fontWeight: 600,
};

/** A card with a 2px accent top border — structure by edge, not by glow. */
export function card(active = false): CSSProperties {
  return {
    background: 'var(--bg-secondary)',
    border: '1px solid var(--border)',
    borderTop: `2px solid ${active ? 'var(--accent)' : 'var(--border)'}`,
    borderRadius: 'var(--radius-md)',
    padding: 10,
    cursor: 'pointer',
    transition: 'border-color 120ms ease, background 120ms ease',
  };
}

export const columnHead: CSSProperties = {
  ...heading,
  padding: '6px 10px',
  borderBottom: '1px solid var(--border)',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  gap: 8,
  position: 'sticky',
  top: 0,
  background: 'var(--bg-primary)',
  zIndex: 1,
};

export const column: CSSProperties = {
  minWidth: 0,
  overflow: 'auto',
  borderRight: '1px solid var(--border)',
};

export const pill: CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 5,
  padding: '2px 8px',
  borderRadius: 999,
  fontSize: 11,
  fontWeight: 600,
  background: 'var(--bg-tertiary)',
  color: 'var(--text-secondary)',
};

export function statusPill(tone: 'ok' | 'warn' | 'bad' | 'idle'): CSSProperties {
  // The fill is mixed from the same token as the text rather than written as a
  // second literal, so a theme that redefines `--success` moves both halves.
  const colors: Record<typeof tone, [string, string]> = {
    ok: ['var(--success)', 'color-mix(in srgb, var(--success) 14%, transparent)'],
    warn: ['var(--warning)', 'color-mix(in srgb, var(--warning) 14%, transparent)'],
    bad: ['var(--error)', 'color-mix(in srgb, var(--error) 14%, transparent)'],
    idle: ['var(--text-secondary)', 'var(--bg-tertiary)'],
  };
  const [fg, bg] = colors[tone];
  return { ...pill, color: fg, background: bg };
}

/** Recessed, rounded container for a command or a captured body. */
export const code: CSSProperties = {
  ...mono,
  display: 'block',
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-word',
  background: 'var(--bg-primary)',
  border: '1px solid var(--border)',
  borderRadius: 6,
  padding: 8,
  margin: 0,
  maxHeight: 260,
  overflow: 'auto',
  color: 'var(--text-primary)',
};

export const empty: CSSProperties = {
  padding: 24,
  textAlign: 'center',
  color: 'var(--text-secondary)',
  fontSize: 12,
  lineHeight: 1.6,
};

/**
 * Staggered entrance, capped so a 100-row list does not take five seconds to
 * finish arriving. `agentpedia-rise` is defined in packages/ui/src/styles.css.
 */
export function stagger(index: number): CSSProperties {
  return {
    animation: 'agentpedia-rise 180ms ease-out both',
    animationDelay: `${Math.min(index, 12) * 22}ms`,
  };
}

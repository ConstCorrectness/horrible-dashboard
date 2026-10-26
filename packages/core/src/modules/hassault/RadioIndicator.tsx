/**
 * Who is on the radio, pinned to the left edge of the match view.
 *
 * One row per voice — you included, so you can see your own key is down rather
 * than wondering whether anyone heard. The list itself comes from `radioSpeakers`.
 */
import type { CSSProperties } from 'react';

import type { RadioSpeaker } from './radio';

const MONO = 'var(--font-mono, "JetBrains Mono", Consolas, monospace)';

/** Scoped keyframes: inline styles cannot declare them. */
const CSS = `
@keyframes hd-radio-in { from { opacity: 0; transform: translateX(-8px) } to { opacity: 1; transform: none } }
@keyframes hd-radio-wave { 0%,100% { opacity: .35 } 50% { opacity: 1 } }
`;

/** Entrance stagger per row, capped so a full team does not trickle in. */
const STAGGER_MS = 40;
const STAGGER_CAP = 4;

export function RadioIndicator({ speakers }: { speakers: RadioSpeaker[] }) {
  if (speakers.length === 0) return null;
  return (
    <div style={list} aria-live="polite" aria-label="Speaking on radio">
      <style>{CSS}</style>
      {speakers.map((s, i) => (
        <div
          key={s.self ? 'self' : s.id}
          style={{
            ...row,
            borderLeftColor: s.self ? 'var(--accent)' : 'var(--success)',
            animationDelay: `${Math.min(i, STAGGER_CAP) * STAGGER_MS}ms`,
          }}
        >
          <span style={{ color: s.self ? 'var(--accent)' : 'var(--success)', display: 'flex' }}>
            <RadioWaves />
          </span>
          <span style={nameStyle}>{s.name}</span>
          {s.self && <span style={tag}>TX</span>}
        </div>
      ))}
    </div>
  );
}

/** A mic capsule with two arcs that pulse while the row is lit. */
function RadioWaves() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      aria-hidden="true"
    >
      <rect x="6" y="2" width="4" height="7" rx="2" />
      <path d="M8 12v2M5.5 14h5" />
      <path
        d="M3.5 6.5a4.5 4.5 0 0 0 9 0"
        style={{ animation: 'hd-radio-wave 0.9s ease-in-out infinite' }}
      />
      <path
        d="M1.5 5.5a6.5 6.5 0 0 0 13 0"
        style={{ animation: 'hd-radio-wave 0.9s ease-in-out 0.25s infinite' }}
      />
    </svg>
  );
}

const list: CSSProperties = {
  position: 'absolute',
  left: 12,
  top: '34%',
  zIndex: 3,
  display: 'flex',
  flexDirection: 'column',
  gap: 3,
  pointerEvents: 'none',
};

const row: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: '0.45rem',
  padding: '0.22rem 0.7rem 0.22rem 0.5rem',
  background: 'rgba(5,6,9,.62)',
  borderLeft: '2px solid',
  clipPath: 'polygon(0 0, 100% 0, calc(100% - 7px) 100%, 0 100%)',
  animation: 'hd-radio-in 160ms ease-out both',
  textShadow: '0 1px 2px rgba(0,0,0,.9)',
};

const nameStyle: CSSProperties = {
  fontSize: '11px',
  fontWeight: 700,
  textTransform: 'uppercase',
  letterSpacing: '0.14em',
  color: 'var(--text)',
  maxWidth: 180,
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
};

const tag: CSSProperties = {
  fontFamily: MONO,
  fontSize: '9.5px',
  letterSpacing: '0.08em',
  color: 'var(--text-dim)',
};

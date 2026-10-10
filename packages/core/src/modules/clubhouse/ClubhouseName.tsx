import type { CSSProperties } from 'react';

import { parseBadgeName } from './badgeLetters';

/**
 * Roughly what a phone's emoji font draws for a lone regional indicator — bold
 * spaced capitals in the accent blue — rendered from decoded ASCII so it looks the
 * same on every desktop font. Letter-spacing trails the last letter; the negative
 * margin takes that back so ellipsis and centring stay true.
 */
const LETTERS: CSSProperties = {
  color: 'var(--accent, #6ea8fe)',
  fontWeight: 800,
  letterSpacing: '0.3em',
  marginRight: '-0.3em',
};

/** A Clubhouse display name, with any badge-letter run styled as the phone shows it. */
export function ClubhouseName({ name }: { name: string | null | undefined }) {
  if (!name) return null;
  const segments = parseBadgeName(name);
  if (!segments.some((s) => s.kind === 'letters')) return <>{name}</>;
  // Decoded to ASCII, the runs read aloud and copy out as the letters they show.
  return (
    <>
      {segments.map((s, i) =>
        s.kind === 'letters' ? (
          <span key={i} style={LETTERS}>
            {s.text}
          </span>
        ) : (
          s.text
        ),
      )}
    </>
  );
}

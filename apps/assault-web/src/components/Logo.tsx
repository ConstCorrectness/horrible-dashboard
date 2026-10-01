/**
 * The HorribleAssault mark: the site's notched plate (the same clipped corner as
 * the primary button and the map cards) with an "A" cut through it as a chevron.
 *
 * Inline rather than an <img>, so it takes the theme's amber and ground from
 * the tokens and is never a request. `src/assets/hassault-mark.svg` is the same
 * drawing with the colours baked in, for the favicon.
 */
export function LogoMark({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" style={{ flex: 'none' }}>
      <path d="M4 2h18l6 6v22H10l-6-6z" fill="var(--accent)" />
      <path
        d="M9.5 24.5 16 8l6.5 16.5"
        fill="none"
        stroke="var(--accent-contrast)"
        strokeWidth={3.4}
        strokeLinejoin="miter"
        strokeMiterlimit={10}
      />
      <path d="M12.2 18.6h7.6" stroke="var(--accent-contrast)" strokeWidth={2.8} />
    </svg>
  );
}

/** Mark and wordmark together, as the top bar and the onboarding gate show them. */
export function Wordmark({ size = 22 }: { size?: number }) {
  return (
    <div className="wordmark">
      <LogoMark size={size} />
      <span>
        Horrible<b>Assault</b>
      </span>
    </div>
  );
}

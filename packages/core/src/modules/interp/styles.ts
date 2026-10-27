/**
 * The suite's style vocabulary — inline style objects over theme tokens, the
 * agentpedia `styles.ts` pattern. No hex fallbacks: `design-tokens.test.ts` checks
 * every token named here resolves, which a fallback would only hide.
 *
 * The look is the tactical one the project asks for: structure by hairlines and a
 * 2px accent edge rather than glow, uppercase tracked headings, monospace muted
 * metadata, a faint grid behind the body, clipped corners on cards.
 */
import type { CSSProperties } from 'react';

import './interp.css';

const heading: CSSProperties = {
  fontSize: 11,
  fontWeight: 700,
  letterSpacing: '0.14em',
  textTransform: 'uppercase',
  color: 'var(--text-secondary)',
};

const mono: CSSProperties = {
  fontFamily: 'var(--font-mono)',
  fontSize: 11,
  color: 'var(--text-secondary)',
};

/** The faint grid every suite body sits on. */
const grid =
  'linear-gradient(color-mix(in srgb, var(--border) 45%, transparent) 1px, transparent 1px) 0 0 / 24px 24px, ' +
  'linear-gradient(90deg, color-mix(in srgb, var(--border) 45%, transparent) 1px, transparent 1px) 0 0 / 24px 24px';

/** One clipped corner, top-right: an angular container without a border trick. */
const clipped = 'polygon(0 0, calc(100% - 10px) 0, 100% 10px, 100% 100%, 0 100%)';

export const S = {
  heading,
  mono,

  /** Wrapper for every rail section: the body fills, the state strip stays put. */
  frame: {
    display: 'flex',
    flexDirection: 'column',
    flex: 1,
    minHeight: 0,
    minWidth: 0,
  } satisfies CSSProperties,

  strip: {
    display: 'flex',
    alignItems: 'center',
    gap: 14,
    flex: 'none',
    minHeight: 28,
    padding: '0 12px',
    borderBottom: '1px solid var(--border)',
    background: 'var(--bg-primary)',
    overflow: 'hidden',
    whiteSpace: 'nowrap',
  } satisfies CSSProperties,

  stripFact: { ...mono, overflow: 'hidden', textOverflow: 'ellipsis' } satisfies CSSProperties,

  stripLabel: { ...heading, fontSize: 9.5, marginRight: 6 } satisfies CSSProperties,

  /** The strip's one action, pushed to the right edge. Classed: not a 30px control. */
  askButton: {
    marginLeft: 'auto',
    display: 'inline-flex',
    alignItems: 'center',
    gap: 6,
    height: 22,
    padding: '0 8px',
    border: 'none',
    borderLeft: '2px solid var(--accent)',
    borderRadius: 0,
    background: 'var(--bg-secondary)',
    color: 'var(--text-primary)',
    fontSize: 10,
    fontWeight: 700,
    letterSpacing: '0.12em',
    textTransform: 'uppercase',
    cursor: 'pointer',
    flex: 'none',
  } satisfies CSSProperties,

  body: {
    display: 'flex',
    flexDirection: 'column',
    flex: 1,
    minHeight: 0,
    minWidth: 0,
    overflow: 'auto',
  } satisfies CSSProperties,

  subRoot: {
    display: 'flex',
    flexDirection: 'column',
    flex: 1,
    minHeight: 0,
  } satisfies CSSProperties,

  subStrip: {
    display: 'flex',
    gap: 2,
    flex: 'none',
    padding: '0 8px',
    borderBottom: '1px solid var(--border)',
    background: 'var(--bg-primary)',
  } satisfies CSSProperties,

  subTab(active: boolean): CSSProperties {
    return {
      ...heading,
      fontSize: 10,
      height: 28,
      padding: '0 10px',
      border: 'none',
      borderBottom: `2px solid ${active ? 'var(--accent)' : 'transparent'}`,
      borderRadius: 0,
      background: 'none',
      color: active ? 'var(--text-primary)' : 'var(--text-secondary)',
      cursor: 'pointer',
    };
  },

  subBody: {
    display: 'flex',
    flexDirection: 'column',
    flex: 1,
    minHeight: 0,
    overflow: 'auto',
  } satisfies CSSProperties,

  /** Home's body: the grid, and room to breathe. */
  home: {
    flex: 1,
    minHeight: 0,
    overflow: 'auto',
    padding: '18px 20px 28px',
    background: `${grid}, var(--bg-primary)`,
    color: 'var(--text-primary)',
    fontSize: 13,
  } satisfies CSSProperties,

  homeTitle: {
    fontSize: 15,
    fontWeight: 800,
    letterSpacing: '0.14em',
    textTransform: 'uppercase',
    margin: 0,
  } satisfies CSSProperties,

  lede: {
    color: 'var(--text-secondary)',
    maxWidth: 640,
    lineHeight: 1.5,
    margin: '6px 0 18px',
  } satisfies CSSProperties,

  /** State tiles: one wide, the rest narrow — asymmetric on purpose. */
  tiles: {
    display: 'grid',
    gridTemplateColumns: 'minmax(220px, 2fr) repeat(auto-fit, minmax(150px, 1fr))',
    gap: 10,
    marginBottom: 22,
  } satisfies CSSProperties,

  tile: {
    background: 'var(--bg-secondary)',
    borderTop: '2px solid var(--accent)',
    clipPath: clipped,
    padding: '10px 12px',
    minWidth: 0,
  } satisfies CSSProperties,

  tileValue: {
    fontFamily: 'var(--font-mono)',
    fontSize: 13,
    color: 'var(--text-primary)',
    marginTop: 6,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  } satisfies CSSProperties,

  groupHead: { ...heading, margin: '18px 0 8px' } satisfies CSSProperties,

  cards: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(230px, 1fr))',
    gap: 10,
  } satisfies CSSProperties,

  card: {
    display: 'grid',
    gap: 6,
    textAlign: 'left',
    background: 'var(--bg-secondary)',
    border: '1px solid var(--border)',
    borderTop: '2px solid var(--border)',
    clipPath: clipped,
    padding: '10px 12px',
    color: 'var(--text-primary)',
    cursor: 'pointer',
    font: 'inherit',
    transition: 'border-color 120ms ease',
  } satisfies CSSProperties,

  cardTitle: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    ...heading,
    color: 'var(--text-primary)',
    ['--icon-size' as string]: '14px',
  } satisfies CSSProperties,

  cardText: {
    color: 'var(--text-secondary)',
    lineHeight: 1.45,
    fontSize: 12,
  } satisfies CSSProperties,

  /** Staggered entrance, capped so a long list still lands in ~a quarter second. */
  stagger(index: number): CSSProperties {
    return {
      animation: 'interp-rise 180ms ease-out both',
      animationDelay: `${Math.min(index, 10) * 24}ms`,
    };
  },
};

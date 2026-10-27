/**
 * The Attention section's styles — the suite's vocabulary (`../styles.ts`): inline
 * objects over theme tokens, hairlines and a 2px accent edge, mono metadata, the
 * faint grid behind, a clipped corner on cards.
 */
import type { CSSProperties } from 'react';

import { S } from '../styles';

const grid =
  'linear-gradient(color-mix(in srgb, var(--border) 45%, transparent) 1px, transparent 1px) 0 0 / 24px 24px, ' +
  'linear-gradient(90deg, color-mix(in srgb, var(--border) 45%, transparent) 1px, transparent 1px) 0 0 / 24px 24px';

const clipped = 'polygon(0 0, calc(100% - 10px) 0, 100% 10px, 100% 100%, 0 100%)';

const panel: CSSProperties = {
  background: 'var(--bg-secondary)',
  borderTop: '2px solid var(--border)',
  clipPath: clipped,
  padding: '10px 12px',
  minWidth: 0,
};

export const A = {
  root: {
    display: 'flex',
    flexDirection: 'column',
    gap: 14,
    padding: '14px 16px 24px',
    minHeight: '100%',
    background: `${grid}, var(--bg-primary)`,
    color: 'var(--text-primary)',
    fontSize: 13,
  } satisfies CSSProperties,

  empty: {
    padding: '28px 20px',
    color: 'var(--text-secondary)',
    background: `${grid}, var(--bg-primary)`,
    minHeight: '100%',
  } satisfies CSSProperties,

  emptyText: {
    maxWidth: 560,
    lineHeight: 1.5,
    color: 'var(--text-secondary)',
  } satisfies CSSProperties,

  header: {
    display: 'flex',
    alignItems: 'flex-end',
    justifyContent: 'space-between',
    gap: 16,
    flexWrap: 'wrap',
  } satisfies CSSProperties,

  pattern: {
    fontFamily: 'var(--font-mono)',
    fontSize: 18,
    fontWeight: 700,
    marginTop: 4,
    letterSpacing: '0.02em',
  } satisfies CSSProperties,

  path: {
    ...S.mono,
    marginTop: 2,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  } satisfies CSSProperties,

  legend: { display: 'flex', flexWrap: 'wrap', gap: 6 } satisfies CSSProperties,

  legendItem: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 6,
    padding: '3px 8px',
    borderLeft: '2px solid var(--border)',
    background: 'var(--bg-secondary)',
    fontSize: 11,
    fontWeight: 700,
    letterSpacing: '0.1em',
    textTransform: 'uppercase',
  } satisfies CSSProperties,

  legendCount: { ...S.mono, fontWeight: 400 } satisfies CSSProperties,

  swatch: {
    display: 'inline-block',
    width: 10,
    height: 10,
    flex: 'none',
    verticalAlign: '-1px',
  } satisfies CSSProperties,

  ghostSwatch: {
    background: 'transparent',
    outline: '1px dashed var(--text-dim)',
    outlineOffset: -1,
  } satisfies CSSProperties,

  ribbonWrap: { ...panel, borderTopColor: 'var(--accent)', paddingTop: 6 } satisfies CSSProperties,

  arcs: { display: 'block', width: '100%', height: 34 } satisfies CSSProperties,

  columns: {
    display: 'flex',
    alignItems: 'flex-end',
    gap: 1,
    height: 96,
  } satisfies CSSProperties,

  column(on: boolean): CSSProperties {
    return {
      flex: '1 1 0',
      minWidth: 2,
      height: '100%',
      display: 'flex',
      alignItems: 'flex-end',
      padding: 0,
      border: 'none',
      borderRadius: 0,
      borderBottom: `2px solid ${on ? 'var(--accent)' : 'transparent'}`,
      background: on ? 'color-mix(in srgb, var(--accent) 10%, transparent)' : 'transparent',
      cursor: 'pointer',
    };
  },

  bar: { display: 'block', width: '100%', transition: 'height 160ms ease' } satisfies CSSProperties,

  marks: { display: 'flex', gap: 1, height: 10, marginTop: 3 } satisfies CSSProperties,

  markCell: {
    flex: '1 1 0',
    minWidth: 2,
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: 1,
  } satisfies CSSProperties,

  mark: {
    display: 'inline-block',
    width: 4,
    height: 4,
    background: 'var(--text-dim)',
  } satisfies CSSProperties,

  markGate: {
    background: 'transparent',
    width: 0,
    height: 0,
    borderLeft: '3px solid transparent',
    borderRight: '3px solid transparent',
    borderBottom: '4px solid var(--success)',
  } satisfies CSSProperties,

  markSink: { borderRadius: '50%', background: 'var(--warn)' } satisfies CSSProperties,

  markNope: { background: 'var(--danger)' } satisfies CSSProperties,

  axis: { display: 'flex', gap: 1, marginTop: 2 } satisfies CSSProperties,

  axisCell: {
    flex: '1 1 0',
    minWidth: 2,
    ...S.mono,
    fontSize: 9,
    overflow: 'visible',
    whiteSpace: 'nowrap',
  } satisfies CSSProperties,

  key: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: '4px 14px',
    marginTop: 8,
    ...S.mono,
    fontSize: 10,
  } satisfies CSSProperties,

  /** Charts on the left, wide; the block card on the right, narrow. */
  lower: {
    display: 'grid',
    gridTemplateColumns: 'minmax(0, 1.7fr) minmax(240px, 1fr)',
    gap: 14,
    alignItems: 'start',
  } satisfies CSSProperties,

  charts: { display: 'grid', gap: 14, minWidth: 0 } satisfies CSSProperties,

  panel,

  panelHead: {
    display: 'flex',
    alignItems: 'baseline',
    gap: 12,
    flexWrap: 'wrap',
    marginBottom: 8,
  } satisfies CSSProperties,

  big: { fontFamily: 'var(--font-mono)', fontSize: 16, fontWeight: 700 } satisfies CSSProperties,

  bars: { display: 'flex', gap: 10, alignItems: 'flex-end', height: 128 } satisfies CSSProperties,

  barGroup(on: boolean): CSSProperties {
    return {
      flex: '1 1 0',
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'stretch',
      gap: 4,
      height: '100%',
      padding: '0 2px',
      border: 'none',
      borderRadius: 0,
      borderBottom: `2px solid ${on ? 'var(--accent)' : 'transparent'}`,
      background: 'transparent',
      cursor: 'pointer',
    };
  },

  barPair: {
    position: 'relative',
    flex: 1,
    display: 'flex',
    alignItems: 'flex-end',
    justifyContent: 'center',
  } satisfies CSSProperties,

  barGhost: {
    position: 'absolute',
    bottom: 0,
    left: '15%',
    right: '15%',
    outline: '1px dashed var(--text-dim)',
    outlineOffset: -1,
  } satisfies CSSProperties,

  barReal: {
    position: 'relative',
    width: '50%',
    minHeight: 1,
    background: 'var(--accent)',
    transition: 'height 200ms ease',
  } satisfies CSSProperties,

  barLabel: { ...S.mono, textAlign: 'center' } satisfies CSSProperties,

  barValue: {
    ...S.mono,
    fontSize: 9.5,
    textAlign: 'center',
    whiteSpace: 'nowrap',
  } satisfies CSSProperties,

  reach: { display: 'block', width: '100%', height: 60 } satisfies CSSProperties,

  card: {
    ...panel,
    borderTopColor: 'var(--accent)',
    display: 'grid',
    gap: 10,
  } satisfies CSSProperties,

  cardHead: { display: 'flex', alignItems: 'center', gap: 8 } satisfies CSSProperties,

  cardLayer: {
    fontFamily: 'var(--font-mono)',
    fontSize: 16,
    fontWeight: 700,
  } satisfies CSSProperties,

  cardBody: { display: 'flex', gap: 12, alignItems: 'flex-start' } satisfies CSSProperties,

  mask: { flex: 'none', background: 'var(--border)' } satisfies CSSProperties,

  facts: { display: 'grid', gap: 3, minWidth: 0, flex: 1 } satisfies CSSProperties,

  fact: {
    display: 'flex',
    justifyContent: 'space-between',
    gap: 10,
    borderBottom: '1px solid color-mix(in srgb, var(--border) 60%, transparent)',
    paddingBottom: 2,
  } satisfies CSSProperties,

  factKey: { ...S.heading, fontSize: 9.5 } satisfies CSSProperties,

  factValue: {
    ...S.mono,
    color: 'var(--text-primary)',
    textAlign: 'right',
  } satisfies CSSProperties,

  gloss: {
    margin: 0,
    color: 'var(--text-secondary)',
    lineHeight: 1.45,
    fontSize: 12,
  } satisfies CSSProperties,

  flags: { display: 'flex', flexWrap: 'wrap', gap: 6 } satisfies CSSProperties,

  flag: {
    ...S.mono,
    padding: '1px 6px',
    borderLeft: '2px solid var(--accent)',
    background: 'var(--bg-primary)',
    color: 'var(--text-primary)',
  } satisfies CSSProperties,

  /** How a fact is known: stated outright, inferred from the family, or seen. */
  basis(basis: string): CSSProperties {
    const tone =
      basis === 'metadata'
        ? 'var(--success)'
        : basis === 'rule'
          ? 'var(--warn)'
          : 'var(--text-dim)';
    return {
      ...S.mono,
      padding: '1px 6px',
      borderLeft: `2px solid ${tone}`,
      background: 'var(--bg-primary)',
      textTransform: 'uppercase',
      letterSpacing: '0.1em',
      fontSize: 9.5,
    };
  },

  actions: { display: 'flex', gap: 8, flexWrap: 'wrap' } satisfies CSSProperties,

  notes: { display: 'grid', gap: 6 } satisfies CSSProperties,

  conflict: {
    padding: '6px 10px',
    borderLeft: '2px solid var(--warn)',
    background: 'color-mix(in srgb, var(--warn) 10%, var(--bg-secondary))',
    fontSize: 12,
  } satisfies CSSProperties,

  note: {
    padding: '6px 10px',
    borderLeft: '2px solid var(--border)',
    background: 'var(--bg-secondary)',
    color: 'var(--text-secondary)',
    fontSize: 12,
  } satisfies CSSProperties,
};

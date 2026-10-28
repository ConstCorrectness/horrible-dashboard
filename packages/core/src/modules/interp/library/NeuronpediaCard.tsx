/**
 * One Neuronpedia feature card: its explanations, the logits it promotes, and its
 * top activating examples with the activation shaded under each token. Shared by
 * the Library's lookup and the Features section.
 */
import type { CSSProperties } from 'react';

import { A } from '../attention/styles';
import { S } from '../styles';
import type { FeatureCard } from './api';

/** SentencePiece marks a leading space with `▁` (U+2581); read as text, it is a space. */
export function shownToken(token: string): string {
  return token.replace(/▁/g, ' ');
}

export function NeuronpediaCard({ card, examples = 4 }: { card: FeatureCard; examples?: number }) {
  if (card.error) return <div style={A.conflict}>{card.error}</div>;
  return (
    <div style={N.card}>
      <div style={N.head}>
        <span style={N.name}>
          {card.model} / {card.source} / {card.index}
        </span>
        <span style={S.mono}>{card.cached ? 'cached' : 'fresh'}</span>
      </div>
      {(card.explanations ?? []).length === 0 && (
        <div style={S.mono}>Neuronpedia has no explanation for this feature.</div>
      )}
      {(card.explanations ?? []).slice(0, 3).map((e, i) => (
        <div key={i} style={N.explanation}>
          “{e.text}” <span style={S.mono}>{e.by}</span>
        </div>
      ))}
      <div style={S.mono}>
        promotes{' '}
        {(card.promotes ?? [])
          .slice(0, 8)
          .map((t) => JSON.stringify(shownToken(t)))
          .join(' ')}
      </div>
      <div style={S.mono}>
        max act {card.maxActivation?.toFixed(2) ?? '?'} · density{' '}
        {card.density != null ? `${(card.density * 100).toFixed(3)}%` : '?'}
      </div>
      {(card.examples ?? []).slice(0, examples).map((ex, i) => (
        <div key={i} style={N.example}>
          {ex.tokens.map((t, j) => (
            <span key={j} style={N.token(ex.maxValue ? (ex.values[j] ?? 0) / ex.maxValue : 0)}>
              {shownToken(t)}
            </span>
          ))}
        </div>
      ))}
      {card.url && (
        <a href={card.url} target="_blank" rel="noreferrer" style={S.mono}>
          {card.url}
        </a>
      )}
    </div>
  );
}

const N = {
  card: { display: 'grid', gap: 6, minWidth: 0 } satisfies CSSProperties,
  head: { display: 'flex', alignItems: 'baseline', gap: 10 } satisfies CSSProperties,
  name: {
    fontFamily: 'var(--font-mono)',
    fontSize: 12,
    fontWeight: 700,
    flex: 1,
    minWidth: 0,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  } satisfies CSSProperties,
  explanation: { fontSize: 13 } satisfies CSSProperties,
  example: {
    fontFamily: 'var(--font-mono)',
    fontSize: 11.5,
    lineHeight: 1.7,
    whiteSpace: 'pre-wrap',
    background: 'var(--bg-primary)',
    padding: '2px 6px',
  } satisfies CSSProperties,
  token(share: number): CSSProperties {
    return share > 0.02
      ? {
          background: `color-mix(in srgb, var(--accent) ${Math.round(Math.min(1, share) * 70)}%, transparent)`,
        }
      : {};
  },
};

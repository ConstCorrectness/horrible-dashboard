import type { TokenStats } from '../api';

/**
 * How long the examples are, against the sequence length that will truncate them.
 *
 * The number this component exists for is `over_limit`: the fraction of examples
 * longer than `max_length`. Truncation during training is completely silent —
 * loss goes down, the run finishes, and the model has learned the first third of
 * every answer — so a percentage here is worth more than the whole distribution.
 *
 * Two honesty rules:
 *
 * - **An estimate says it is one.** Without a real tokenizer the counts are
 *   characters/4. Rendering that as "1,024 tokens" would be a measurement the
 *   app did not make.
 * - **The limit is drawn, not described.** The bars past `max_length` are tinted
 *   as lost, because "23% over" and seeing a quarter of the chart go red are
 *   different amounts of convincing.
 */
export function TokenHistogram({ stats, maxLength }: { stats: TokenStats; maxLength: number }) {
  if (!stats.sampled) {
    return (
      <div style={{ fontSize: '0.75rem', color: 'var(--text-dim)' }}>
        {stats.note || 'No text could be measured in this dataset.'}
      </div>
    );
  }

  const peak = Math.max(1, ...stats.histogram.map((b) => b.count));
  const over = stats.over_limit;
  const overTone = over > 0.2 ? 'var(--danger)' : over > 0.02 ? 'var(--warn)' : 'var(--ok)';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.45rem' }}>
      <div
        style={{
          display: 'flex',
          gap: '0.9rem',
          flexWrap: 'wrap',
          alignItems: 'baseline',
          fontFamily: 'var(--font-mono, monospace)',
          fontSize: '0.72rem',
        }}
      >
        <span style={{ color: overTone, fontWeight: 700 }}>
          {(over * 100).toFixed(over > 0 && over < 0.01 ? 2 : 0)}% truncated at {maxLength}
        </span>
        <span style={{ color: 'var(--text-dim)' }}>median {stats.p50}</span>
        <span style={{ color: 'var(--text-dim)' }}>p95 {stats.p95}</span>
        <span style={{ color: 'var(--text-dim)' }}>max {stats.max}</span>
        <span style={{ color: 'var(--text-dim)' }}>n={stats.sampled}</span>
      </div>

      <div
        style={{
          display: 'flex',
          alignItems: 'flex-end',
          gap: 2,
          height: 64,
          borderBottom: '1px solid var(--border)',
        }}
      >
        {stats.histogram.map((bucket) => {
          // A bucket is "lost" when its whole range sits past the limit; the
          // bucket the limit falls inside is partly kept, so it stays neutral
          // rather than claiming certainty about rows we did not measure.
          const lost = bucket.from >= maxLength;
          return (
            <div
              key={bucket.from}
              title={`${bucket.from}–${bucket.to || '∞'} tokens: ${bucket.count} examples`}
              style={{
                flex: 1,
                minWidth: 4,
                height: `${Math.max(bucket.count ? 3 : 0, (bucket.count / peak) * 100)}%`,
                background: lost ? 'var(--danger)' : 'var(--accent)',
                opacity: lost ? 0.75 : 0.55,
                borderRadius: 'var(--radius-sm) var(--radius-sm) 0 0',
              }}
            />
          );
        })}
      </div>

      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          fontFamily: 'var(--font-mono, monospace)',
          fontSize: '0.65rem',
          color: 'var(--text-dim)',
        }}
      >
        <span>{stats.histogram[0]?.from ?? 0}</span>
        <span>tokens per example</span>
        <span>{stats.histogram.at(-1)?.from ?? 0}+</span>
      </div>

      {stats.note && <div style={{ fontSize: '0.72rem', color: 'var(--warn)' }}>{stats.note}</div>}
      {stats.exact && stats.tokenizer && (
        <div
          style={{
            fontSize: '0.7rem',
            color: 'var(--text-dim)',
            fontFamily: 'var(--font-mono, monospace)',
          }}
        >
          counted with {stats.tokenizer}
        </div>
      )}
    </div>
  );
}

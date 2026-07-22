import { type CSSProperties } from 'react';

import type { MovePolicy } from '../games-api';

/**
 * **Harness pipeline strip** — `Observation → [what runs] → Action` for the selected
 * move policy, so it's legible at a glance which harness pieces actually fire. This is
 * the direct answer to "do my tools / system prompt run every tick?": under `bot` only
 * the bot function runs (no model, no prompt); under `agent` the prompt + tools + model
 * are the whole turn; `random`/`manual` use none of the harness. See docs/modules/games.mdx.
 */

interface Stage {
  label: string;
  sub: string;
  active: boolean;
}

/** The middle stages between Observation and Action, per policy. Inactive stages are
 * shown greyed so the contrast (what's used vs. ignored) is the point. */
function stagesFor(policy: MovePolicy): Stage[] {
  switch (policy) {
    case 'bot':
      return [{ label: 'bot(obs)', sub: 'pure function · every tick · no model', active: true }];
    case 'agent':
      return [
        { label: 'context', sub: 'system prompt', active: true },
        { label: 'tools', sub: 'model may call', active: true },
        { label: 'model', sub: 'drives the loop', active: true },
      ];
    case 'manual':
      return [{ label: 'you / agent tool', sub: 'drive the move', active: true }];
    case 'random':
    default:
      return [{ label: 'random pick', sub: 'no harness used', active: true }];
  }
}

const node = (active: boolean): CSSProperties => ({
  fontFamily: 'var(--font-mono, monospace)',
  fontSize: '0.72rem',
  padding: '0.25rem 0.5rem',
  borderRadius: 6,
  border: '1px solid var(--border)',
  background: active
    ? 'color-mix(in srgb, var(--accent, #6ea8fe) 12%, transparent)'
    : 'transparent',
  color: active ? 'var(--text)' : 'var(--text-faint, #666)',
  opacity: active ? 1 : 0.55,
  whiteSpace: 'nowrap',
});

const arrow: CSSProperties = { color: 'var(--text-faint, #666)', fontSize: '0.8rem' };
const sub: CSSProperties = { color: 'var(--text-dim)', fontSize: '0.6rem', display: 'block' };

export function HarnessPipeline({ policy }: { policy: MovePolicy }) {
  const stages = stagesFor(policy);
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '0.4rem',
        flexWrap: 'wrap',
        padding: '0.5rem 0.6rem',
        border: '1px solid var(--border)',
        borderRadius: 8,
      }}
    >
      <span style={node(true)}>
        Observation<span style={sub}>each turn</span>
      </span>
      <span style={arrow}>→</span>
      {stages.map((s, i) => (
        <span key={s.label} style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
          {i > 0 && <span style={arrow}>·</span>}
          <span style={node(s.active)}>
            {s.label}
            <span style={sub}>{s.sub}</span>
          </span>
        </span>
      ))}
      <span style={arrow}>→</span>
      <span style={node(true)}>
        Action<span style={sub}>one legal id</span>
      </span>
    </div>
  );
}

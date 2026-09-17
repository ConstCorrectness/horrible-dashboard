import { describe, expect, it } from 'vitest';

import { toolSequence } from '../panels/CommonsSection';

const step = (name: string) => ({
  seq: 0,
  kind: name ? 'action' : 'message',
  round: 0,
  name,
  ok: true,
  duration_ms: 1,
  gated: false,
});

describe('toolSequence', () => {
  it('collapses repeats and skips messages', () => {
    const steps = ['', 'files.read', 'files.read', 'files.read', '', 'editor.open'].map(step);
    expect(toolSequence({ steps })).toBe('files.read ×3 → editor.open');
  });

  it('says how many it left out rather than trailing off', () => {
    const steps = ['a', 'b', 'c', 'd'].map(step);
    expect(toolSequence({ steps }, 2)).toBe('a → b → +2 more');
  });

  it('is empty for a run with no tool calls', () => {
    expect(toolSequence({ steps: [step('')] })).toBe('');
  });
});

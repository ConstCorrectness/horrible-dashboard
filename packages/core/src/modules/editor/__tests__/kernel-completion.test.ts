import { describe, expect, it } from 'vitest';

import { anchorMatches } from '../kernelCompletion';

const reply = (cursor_start: number, texts: string[], type = 'instance') => ({
  source: 'kernel' as const,
  cursor_start,
  cursor_end: 0,
  matches: texts.map((text) => ({ text, type, signature: '' })),
});

describe('kernel completion anchoring', () => {
  it('keeps a jedi-style reply that already anchors at the word', () => {
    const doc = 'model.lm';
    const out = anchorMatches(doc, 8, 6, reply(6, ['lm_head', 'model']), 0);
    expect(out.map((o) => o.label)).toEqual(['lm_head', 'model']);
  });

  it('re-anchors a reply that replaced the whole dotted expression', () => {
    // IPython's fallback completer: from 0, `model.lm_head`. The merged list anchors
    // at the word start (6), so the typed `model.` prefix is peeled off.
    const doc = 'model.lm';
    const out = anchorMatches(doc, 8, 6, reply(0, ['model.lm_head', 'other.lm_x']), 0);
    expect(out.map((o) => o.label)).toEqual(['lm_head']);
  });

  it('hides private names unless the user typed an underscore', () => {
    const doc = 'model.';
    expect(
      anchorMatches(doc, 6, 6, reply(6, ['_modules', 'config']), 0).map((o) => o.label),
    ).toEqual(['config']);
    const typed = 'model._';
    expect(anchorMatches(typed, 7, 6, reply(6, ['_modules']), 0).map((o) => o.label)).toEqual([
      '_modules',
    ]);
  });

  it('maps IPython types onto CodeMirror kinds', () => {
    const out = anchorMatches('np.', 3, 3, reply(3, ['linalg'], 'module'), 0);
    expect(out[0]).toMatchObject({ label: 'linalg', type: 'namespace', detail: 'module' });
  });
});

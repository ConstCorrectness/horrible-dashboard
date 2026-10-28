import { describe, expect, it } from 'vitest';

import type { SteerResult } from '../steer/api';
import { requestOf, steerDigest, steerStore, type SteerState } from '../steer/state';

const base: SteerState = { ...steerStore.get() };

const draft: SteerState['draft'] = {
  artifactId: 'scope:l13',
  feature: 13769,
  label: 'France and French language',
  traceId: 't1',
  prompt: 'The Eiffel Tower is in the capital of',
  strength: -1,
  maxTokens: 16,
};

function result(patch: Partial<SteerResult> = {}): SteerResult {
  return {
    defaultTraceId: 't2',
    steeredTraceId: 't3',
    feature: 13769,
    layer: 13,
    strength: -1,
    unit: 177.8,
    scale: -177.8,
    default: { text: ' France', tokens: [' France'] },
    steered: { text: ' France', tokens: [' France'] },
    diverges: null,
    arrived: {
      state: 'arrived',
      defaultMean: 22.2,
      steeredMean: 0,
      defaultActiveShare: 0.2,
      steeredActiveShare: 0,
    },
    nextToken: {
      defaultTop: { id: 1, token: ' France', default: 0.9955, steered: 0.47, deltaLogProb: -0.75 },
      steeredTop: { id: 1, token: ' France', default: 0.9955, steered: 0.47, deltaLogProb: -0.75 },
      gained: [],
      lost: [],
      kl: 4.19,
    },
    ...patch,
  };
}

describe('requestOf', () => {
  it('needs an SAE, a feature and a trace', () => {
    expect(requestOf({ ...draft, feature: null })).toBeNull();
    expect(requestOf({ ...draft, traceId: '' })).toBeNull();
    expect(requestOf({ ...draft, artifactId: '' })).toBeNull();
  });

  it('always sends the prompt — the backend decides whether the default is reusable', () => {
    expect(requestOf(draft)).toEqual({
      artifactId: 'scope:l13',
      feature: 13769,
      strength: -1,
      traceId: 't1',
      prompt: 'The Eiffel Tower is in the capital of',
      maxTokens: 16,
    });
  });
});

describe('steerDigest', () => {
  it('says nothing before a run', () => {
    expect(steerDigest({ ...base, result: null })).toBeNull();
  });

  it('leads with whether it took, then the prediction and the text', () => {
    expect(steerDigest({ ...base, result: result() })).toBe(
      'steered F13769 at L13 ×-1 (scale -178): arrived (mean 22.2→0); ' +
        'next token " France"→" France", KL 4.19; text unchanged',
    );
  });

  it('names where the text diverges', () => {
    const line = steerDigest({
      ...base,
      result: result({ diverges: 3, arrived: { ...result().arrived, state: 'did_not_arrive' } }),
    });
    expect(line).toContain('did not arrive');
    expect(line).toContain('diverges at token 3');
  });
});

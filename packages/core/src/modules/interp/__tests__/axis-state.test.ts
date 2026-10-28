import { describe, expect, it } from 'vitest';

import type { AxisSummary } from '../axis/api';
import { axisDigest, axisStore, parseConversation, type AxisState } from '../axis/state';

describe('parseConversation', () => {
  it('opens a message at each role marker and continues it on the lines after', () => {
    expect(
      parseConversation(
        'system: You are a ghost.\nuser: Who are you?\nassistant: Ooooh...\nI wander.\n\nuser: why',
      ),
    ).toEqual([
      { role: 'system', content: 'You are a ghost.' },
      { role: 'user', content: 'Who are you?' },
      { role: 'assistant', content: 'Ooooh...\nI wander.' },
      { role: 'user', content: 'why' },
    ]);
  });

  it("treats text before any marker as the user's, and drops empty messages", () => {
    expect(parseConversation('hello there\nAssistant:\nuser: again')).toEqual([
      { role: 'user', content: 'hello there' },
      { role: 'user', content: 'again' },
    ]);
  });
});

function axis(): AxisSummary {
  return {
    id: 'axis:gemma',
    modelName: 'gemma-3-1b-it-Q8_0',
    modelPath: 'C:/m/gemma-3-1b-it-Q8_0.gguf',
    modelSha: 'abc',
    nLayer: 4,
    nEmbd: 8,
    defaultLayer: 2,
    check: { state: 'separates', tokenAuc: 0.97, auc: 1, reason: '' },
    cap: { layers: [1, 2], tau: { '1': 5, '2': 6 }, percentile: 25 },
    layers: [],
    roles: ['pirate', 'ghost', 'teacher'],
    roleProjections: {},
    assistantProjection: [],
    examples: {},
    questions: [],
    heldOut: 3,
    samples: { assistant: 3, role: 9, heldOut: 6, heldOutTokens: 60 },
    maxTokens: 32,
    elapsed: 1,
    createdAt: 1,
  };
}

describe('axisDigest', () => {
  it('is silent with no axis', () => {
    expect(axisDigest({ ...axisStore.get(), axes: [], id: '' })).toBeNull();
  });

  it('leads with the held-out check, then the last reading and cap', () => {
    const s: AxisState = {
      ...axisStore.get(),
      axes: [axis()],
      id: 'axis:gemma',
      monitor: {
        ...axisStore.get().monitor,
        result: {
          values: [],
          tokens: [],
          layer: 2,
          bands: {},
          summary: { mean: 1, belowFloor: 0.9, slopePer100: -3 },
          assistantSummary: { mean: 4, belowFloor: 0.25, slopePer100: -12 },
          messages: [],
          clipped: 0,
          source: { kind: 'turn', turnId: 't1' },
          drift: { perMessage: [], gap: 400, change: -180, changeInGaps: -0.45, replies: 3 },
        },
      },
    };
    const line = axisDigest(s)!;
    expect(line).toMatch(
      /^axis on gemma-3-1b-it-Q8_0 at L2: separates \(held-out token AUC 0.97\)/,
    );
    // The drift between replies leads: a conversation's level is offset by text the
    // model did not write, its change is not.
    expect(line).toContain(
      'turn): replies moved -0.45 of the assistant-to-role gap over 3 replies, mean 4, 25% below the assistant floor, trend -12/100 tokens',
    );
  });
});

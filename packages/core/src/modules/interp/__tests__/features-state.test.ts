import { describe, expect, it } from 'vitest';

import type { FeatureEncoding } from '../features/api';
import { featuresDigest, featuresStore, leadFeature, type FeaturesState } from '../features/state';

function encoding(patch: Partial<FeatureEncoding> = {}): FeatureEncoding {
  return {
    traceId: 't1',
    model: 'gemma-3-1b-it-Q8_0',
    artifactId: 'scope:l13',
    arch: 'jump_relu',
    site: 'resid_post',
    layer: 13,
    width: 16384,
    fidelity: 'fp16',
    tokens: [
      { position: 0, text: '', generated: false },
      { position: 1, text: 'The', generated: false },
      { position: 2, text: ' Paris', generated: false },
    ],
    firstPosition: 0,
    perToken: [],
    top: [
      { index: 371, peak: 932, position: 1, tokens: 1, firstToken: true },
      { index: 90, peak: 494.7, position: 2, tokens: 9, firstToken: false },
    ],
    check: { state: 'verified', fvu: 0.31, cosine: 0.995, tokens: 37, l0: 60, expectedL0: 60 },
    compat: { state: 'compatible', reasons: [] },
    neuronpedia: { model: 'gemma-3-1b-it', source: '13-gemmascope-2-res-16k' },
    ...patch,
  };
}

const base: FeaturesState = { ...featuresStore.get() };

describe('leadFeature', () => {
  it('passes over a first-token feature to the strongest elsewhere', () => {
    expect(leadFeature(encoding())?.index).toBe(90);
  });

  it('falls back to a first-token feature when it is all there is', () => {
    const only = encoding({ top: [encoding().top[0]] });
    expect(leadFeature(only)?.index).toBe(371);
  });
});

describe('featuresDigest', () => {
  it('says nothing without an encoding', () => {
    expect(featuresDigest({ ...base, result: null })).toBeNull();
  });

  it('leads with the verdict, then the lead feature and its label', () => {
    const line = featuresDigest({
      ...base,
      result: encoding(),
      cards: { 90: { explanations: [{ text: 'city names', by: 'oai' }] } },
    });
    expect(line).toBe(
      'SAE L13 16k on gemma-3-1b-it-Q8_0 t1: verified (FVU 0.31, L0 60/60); ' +
        'lead F90 "city names" 495 at " Paris"',
    );
  });

  it('says when the reconstruction could not be checked', () => {
    const line = featuresDigest({
      ...base,
      result: encoding({ check: { state: 'not_checked', tokens: 1, reason: 'one token' } }),
      cards: {},
    });
    expect(line).toContain('reconstruction not checked');
    expect(line).toContain('lead F90 495');
  });
});

import { describe, expect, it } from 'vitest';

import type { LensSpec } from '../../llamacpp/api';
import {
  firstLayerWithin,
  lensCompareDigest,
  lensCompareStore,
  type LensCompareState,
} from '../lenses/state';

const identity: LensSpec = {
  id: 'identity',
  kind: 'identity',
  label: 'identity (logit lens)',
  provenance: '',
  layers: [],
  dModel: 0,
};
const jlens: LensSpec = {
  id: 'np-jlens-salesforce-wikitext',
  kind: 'jacobian',
  label: 'Jacobian lens (Neuronpedia, Salesforce-wikitext), 460 prompts',
  provenance: '',
  layers: [0, 1, 2],
  dModel: 1152,
};

const base: LensCompareState = { ...lensCompareStore.get() };

describe('firstLayerWithin', () => {
  it('finds the earliest layer the token is in the top N', () => {
    // Mount Fuji, " Japan": the logit lens never gets there; the J-lens by L15.
    expect(firstLayerWithin({ 11: 4205, 15: 5720, 20: 22, 25: 22 })).toBeNull();
    expect(firstLayerWithin({ 11: 88, 15: 12, 20: 9, 22: 8 })).toBe(20);
    expect(firstLayerWithin({ 11: 88, 15: 12, 20: 9 }, 20)).toBe(15);
  });

  it('ignores layers the lens could not read', () => {
    expect(firstLayerWithin({ [-1]: null, 3: 5 })).toBe(3);
  });
});

describe('lensCompareDigest', () => {
  it('says nothing with only the logit lens', () => {
    expect(
      lensCompareDigest({ ...base, traceId: 't', lenses: [identity], position: 4 }),
    ).toBeNull();
  });

  it('names the lenses and where a tracked token surfaces under each', () => {
    const line = lensCompareDigest({
      ...base,
      traceId: 't',
      lenses: [identity, jlens],
      position: 11,
      track: {
        tokenId: 6056,
        text: ' Japan',
        ranks: { identity: { 11: 4205, 20: 22 }, [jlens.id]: { 11: 88, 20: 9 } },
      },
    });
    expect(line).toBe(
      `lenses on t at position 11: logit lens vs ${jlens.label}; ` +
        '" Japan" logit top-10 from L—, fitted top-10 from L20',
    );
  });
});

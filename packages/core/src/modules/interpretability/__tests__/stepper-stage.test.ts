import { describe, expect, it } from 'vitest';

import { explorerStage, stepperStage } from '../inspect/stepper-stage';

describe('stepper stage mapping', () => {
  it('lights the sub-block the stepper is executing', () => {
    expect(explorerStage('attention', false)).toBe('attention');
    expect(explorerStage('ffn', false)).toBe('ffn');
    // An MoE model draws its FFN as the experts node; a dense ffn stage on it
    // must still land somewhere drawn.
    expect(explorerStage('ffn', true)).toBe('moe');
    expect(explorerStage('moe', true)).toBe('moe');
  });

  it('selects the block itself for a residual step or an unknown stage', () => {
    expect(explorerStage('residual', false)).toBeNull();
    expect(explorerStage(undefined, false)).toBeNull();
    expect(explorerStage('other', false)).toBeNull();
  });

  it('only steers the stepper with stages it can find', () => {
    expect(stepperStage('attention')).toBe('attention');
    expect(stepperStage('block')).toBeUndefined();
  });
});

import { describe, expect, it } from 'vitest';

import { collapseCarriageReturns } from '../streamText';

/** The browser must merge stream chunks exactly as `notebook_core.session` does —
 *  the wire carries deltas, so a different rule here means the pane and the saved
 *  notebook disagree about what the cell printed. */
describe('collapseCarriageReturns', () => {
  it('keeps only the last frame of a progress bar', () => {
    // The trailing `\r` survives: it is what makes the next chunk overwrite this
    // frame rather than be appended to it.
    expect(collapseCarriageReturns('a 10%\ra 40%\ra 100%\r')).toBe('a 100%\r');
    expect(collapseCarriageReturns('a 100%\rdone\n')).toBe('done\n');
  });

  it('leaves real lines alone', () => {
    expect(collapseCarriageReturns('first\nsecond\n')).toBe('first\nsecond\n');
    expect(collapseCarriageReturns('plain')).toBe('plain');
  });

  it('collapses each line independently', () => {
    expect(collapseCarriageReturns('one\ntwo\rTWO')).toBe('one\nTWO');
  });
});

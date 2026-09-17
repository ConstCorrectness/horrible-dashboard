/**
 * A failing case whose expected tool the budget cut measured the catalog, not the
 * model — the pane has to say so, because the red verdict beside it says otherwise.
 */
import { describe, expect, it } from 'vitest';

import { budgetCut } from '../budget';

const call = (name: string) => ({ name, arguments: {} });

describe('budgetCut', () => {
  it('names the expected tools the budget cut', () => {
    expect(
      budgetCut({
        passed: false,
        expected: [call('scratch.open'), call('scratch.open'), call('editor.open')],
        tools_dropped: ['files.write', 'scratch.open'],
      }),
    ).toEqual(['scratch.open']);
  });

  it('is empty when the cut tools were not the expected ones', () => {
    expect(
      budgetCut({ passed: false, expected: [call('editor.open')], tools_dropped: ['files.write'] }),
    ).toEqual([]);
  });

  it('is empty for a pass — the absence did not hold it back', () => {
    expect(
      budgetCut({ passed: true, expected: [call('scratch.open')], tools_dropped: ['scratch.open'] }),
    ).toEqual([]);
  });
});
